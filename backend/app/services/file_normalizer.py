"""Content normalizer for CityAgent Insights uploads.

Rewrites raw uploaded content into clean, structured instructions / skills /
knowledge. Today the app dumps raw text; these functions produce
LLM-rephrased, deduped, structured output instead.

Design contract
----------------
* Dependency-free and pure. This module imports NOTHING from the app and does
  NO network by itself.
* Every public function takes an ``infer(prompt: str) -> str`` callable. In
  production the caller passes a real LLM call; tests pass a deterministic
  mock. This keeps the whole module testable offline.
* Every function is defensive: it builds a tight prompt, calls ``infer``,
  parses robustly, and on ANY inference/parse failure falls back to the raw
  input. It never raises and never loses content.

Run ``python3 file_normalizer.py`` to exercise all four functions offline with
a canned mock (no network).
"""

from __future__ import annotations

import json
import re
from typing import Callable, List, Dict, Any

Infer = Callable[[str], str]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$")


def _strip_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if present."""
    if text is None:
        return ""
    t = text.strip()
    # Strip an opening ```lang fence and a closing ``` fence, tolerating prose
    # by only touching the outermost fences.
    if t.startswith("```"):
        # drop first line (``` or ```json etc.)
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        else:
            t = t[3:]
        # drop trailing fence
        rfence = t.rfind("```")
        if rfence != -1:
            t = t[:rfence]
    return t.strip()


def _extract_json(text: str) -> Any:
    """Fence-strip then brace-match the first balanced JSON value.

    Tolerates surrounding prose. Returns the parsed object/list, or ``None`` if
    nothing parseable is found. Handles both ``{...}`` objects and ``[...]``
    arrays, and is string-literal aware so braces inside strings don't confuse
    the matcher.
    """
    if not text:
        return None

    cleaned = _strip_fences(text)

    # Fast path: the whole thing is JSON.
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    # Find the first balanced { } or [ ] block anywhere in the text.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidate = cleaned[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except (ValueError, TypeError):
                            break  # try next start position
            start = cleaned.find(open_ch, start + 1)

    return None


def _clean_text(text: str) -> str:
    """Strip fences and collapse excess whitespace from a prose LLM reply."""
    t = _strip_fences(text or "")
    # collapse 3+ blank lines to a single blank line
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _first_lines(text: str, n: int) -> List[str]:
    """Return up to ``n`` non-empty, stripped lines of ``text``."""
    out: List[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            out.append(s)
        if len(out) >= n:
            break
    return out


def _snake_case(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (name or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "skill"


def _as_str_list(value: Any) -> List[str]:
    """Coerce an arbitrary JSON value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            elif item is not None:
                out.append(str(item).strip())
        return out
    return [str(value).strip()]


# ---------------------------------------------------------------------------
# 1. Definitions -> data-dictionary instruction
# ---------------------------------------------------------------------------

def normalize_definitions(raw_block: str, infer: Infer) -> str:
    """Turn a raw "term - meaning" dump into a crisp, deduped, grouped
    data-dictionary instruction.

    Prompt strategy: instruct the model to act as a data-dictionary editor,
    dedupe synonymous terms, express each as ``term = concise definition``
    (SQL-ish where obvious, e.g. ``revenue = SUM(payments)``), group related
    terms, and return PLAIN TEXT (one definition per line, no prose preamble).

    Fallback: on empty inference or exception, return the raw block unchanged.
    """
    if not raw_block or not raw_block.strip():
        return raw_block or ""

    prompt = (
        "You are a data-dictionary editor. Rewrite the raw term definitions "
        "below into a clean, deduplicated data dictionary that can be used as "
        "an agent instruction.\n"
        "Rules:\n"
        "- One definition per line in the form `term = concise definition`.\n"
        "- Merge duplicate or synonymous terms into a single line.\n"
        "- Prefer compact, computable phrasing where the meaning implies it "
        "(e.g. `revenue = SUM(payments)`, `active_account = last activity < 90d`).\n"
        "- Keep every distinct concept; do not invent new terms.\n"
        "- Output ONLY the definition lines. No preamble, no markdown fences.\n\n"
        "RAW DEFINITIONS:\n"
        f"{raw_block.strip()}\n"
    )

    try:
        raw_out = infer(prompt)
    except Exception:
        return raw_block.strip()

    cleaned = _clean_text(raw_out)
    if not cleaned:
        return raw_block.strip()
    return cleaned


# ---------------------------------------------------------------------------
# 2. Rules / logic / Q&A doc -> list of imperative instructions
# ---------------------------------------------------------------------------

def extract_rules_from_doc(text: str, infer: Infer) -> List[str]:
    """Turn a rules / business-logic / Q&A document into a list of discrete,
    imperative instruction strings.

    Prompt strategy: ask for STRICT JSON -- a single JSON array of short
    imperative sentences (e.g. "Always join accounts to opportunities on
    account_id", "Treat stage='Closed Won' as won"). Explicitly forbid prose
    outside the array so ``_extract_json`` can recover it.

    Fallback: on failure, return the first few non-empty lines of the raw text
    so no content is lost.
    """
    if not text or not text.strip():
        return []

    prompt = (
        "Extract the business rules and logic from the document below as a "
        "list of discrete, imperative agent instructions.\n"
        "Rules:\n"
        "- Each item is one short imperative sentence (start with a verb).\n"
        "- Capture every distinct rule; merge duplicates.\n"
        "- Examples: \"Always join accounts to opportunities on account_id\", "
        "\"Treat stage='Closed Won' as won\".\n"
        "- Return STRICT JSON: a single JSON array of strings and NOTHING "
        "else. No markdown, no commentary.\n\n"
        "DOCUMENT:\n"
        f"{text.strip()}\n"
    )

    try:
        raw_out = infer(prompt)
    except Exception:
        return _first_lines(text, 8)

    parsed = _extract_json(raw_out)
    rules = _as_str_list(parsed)
    if rules:
        # dedupe preserving order
        seen = set()
        deduped = []
        for r in rules:
            key = r.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped

    # Fallback: raw first lines.
    return _first_lines(text, 8)


# ---------------------------------------------------------------------------
# 3. Procedure / playbook doc -> skill draft
# ---------------------------------------------------------------------------

def draft_skill_from_doc(text: str, infer: Infer) -> Dict[str, Any]:
    """Turn a procedure / playbook document into a skill draft:
    ``{"name": snake_case, "when_to_use": str, "steps": [str], "body": str}``.

    Prompt strategy: ask for STRICT JSON with exactly those keys -- a
    snake_case name, a one-line trigger description, an ordered list of concrete
    steps, and a body that is the cleaned playbook prose.

    Fallback: on failure, return a minimal dict derived from the raw text
    (name from the first line, body = raw text, steps = first lines).
    """
    if not text or not text.strip():
        return {"name": "skill", "when_to_use": "", "steps": [], "body": text or ""}

    prompt = (
        "Convert the procedure/playbook below into a reusable skill definition.\n"
        "Return STRICT JSON with exactly these keys and NOTHING else:\n"
        "{\n"
        '  "name": "snake_case_short_name",\n'
        '  "when_to_use": "one sentence describing when to apply this skill",\n'
        '  "steps": ["ordered", "concrete", "actionable steps"],\n'
        '  "body": "the cleaned, rewritten playbook prose"\n'
        "}\n"
        "No markdown fences, no commentary outside the JSON.\n\n"
        "PROCEDURE:\n"
        f"{text.strip()}\n"
    )

    def _fallback() -> Dict[str, Any]:
        lines = _first_lines(text, 6)
        name = _snake_case(lines[0]) if lines else "skill"
        return {
            "name": name,
            "when_to_use": "",
            "steps": lines,
            "body": text.strip(),
        }

    try:
        raw_out = infer(prompt)
    except Exception:
        return _fallback()

    parsed = _extract_json(raw_out)
    if not isinstance(parsed, dict):
        return _fallback()

    name = _snake_case(str(parsed.get("name") or ""))
    if not name or name == "skill":
        lines = _first_lines(text, 1)
        name = _snake_case(lines[0]) if lines else "skill"

    when = parsed.get("when_to_use") or parsed.get("when") or ""
    steps = _as_str_list(parsed.get("steps"))
    body = parsed.get("body") or ""

    result = {
        "name": name,
        "when_to_use": str(when).strip(),
        "steps": steps,
        "body": str(body).strip() or text.strip(),
    }
    # If the model gave us nothing usable, fall back but keep any name.
    if not result["steps"] and not result["body"]:
        return _fallback()
    return result


# ---------------------------------------------------------------------------
# 4. Reference doc -> knowledge synopsis
# ---------------------------------------------------------------------------

def synopsize_knowledge(text: str, infer: Infer) -> Dict[str, Any]:
    """Summarize a reference document into
    ``{"synopsis": str (3-5 sentences), "key_facts": [str]}``.

    The caller keeps the raw text separately; this only produces the summary
    layer used for retrieval / display.

    Prompt strategy: ask for STRICT JSON with a 3-5 sentence synopsis and a
    list of atomic key facts.

    Fallback: on failure, synopsis = first ~3 lines joined, key_facts = first
    lines of the raw text.
    """
    if not text or not text.strip():
        return {"synopsis": "", "key_facts": []}

    prompt = (
        "Summarize the reference document below for later retrieval.\n"
        "Return STRICT JSON with exactly these keys and NOTHING else:\n"
        "{\n"
        '  "synopsis": "a 3-5 sentence plain-language overview",\n'
        '  "key_facts": ["atomic", "standalone factual statements"]\n'
        "}\n"
        "No markdown fences, no commentary outside the JSON.\n\n"
        "DOCUMENT:\n"
        f"{text.strip()}\n"
    )

    def _fallback() -> Dict[str, Any]:
        lines = _first_lines(text, 5)
        synopsis = " ".join(lines[:3])
        return {"synopsis": synopsis, "key_facts": lines}

    try:
        raw_out = infer(prompt)
    except Exception:
        return _fallback()

    parsed = _extract_json(raw_out)
    if not isinstance(parsed, dict):
        return _fallback()

    synopsis = str(parsed.get("synopsis") or "").strip()
    key_facts = _as_str_list(parsed.get("key_facts") or parsed.get("facts"))
    if not synopsis and not key_facts:
        return _fallback()
    if not synopsis:
        synopsis = " ".join(_first_lines(text, 3))
    return {"synopsis": synopsis, "key_facts": key_facts}


# ---------------------------------------------------------------------------
# Offline self-test
# ---------------------------------------------------------------------------

def _mock_infer(prompt: str) -> str:
    """Deterministic offline stand-in for a real LLM call.

    Branches on keywords in the prompt to return plausible canned output for
    each of the four functions -- demonstrating the rewrite/parse path with no
    network.
    """
    p = prompt.lower()

    if "data-dictionary editor" in p:
        # normalize_definitions -> plain text lines (with a fence to exercise
        # fence stripping).
        return (
            "```\n"
            "revenue = SUM(payments)\n"
            "active_account = last activity < 90d\n"
            "churned_account = no activity >= 180d\n"
            "```"
        )

    if "imperative agent instructions" in p:
        # extract_rules_from_doc -> JSON array (wrapped in prose to exercise
        # tolerant extraction).
        return (
            "Here are the rules:\n"
            "[\n"
            '  "Always join accounts to opportunities on account_id",\n'
            "  \"Treat stage='Closed Won' as won\",\n"
            '  "Exclude test accounts where name starts with QA_"\n'
            "]\n"
            "That is all."
        )

    if "reusable skill definition" in p:
        # draft_skill_from_doc -> JSON object.
        return (
            "```json\n"
            "{\n"
            '  "name": "Monthly Pipeline Review",\n'
            '  "when_to_use": "Run at month end to assess sales pipeline health.",\n'
            '  "steps": ["Pull open opportunities", "Group by stage", '
            '"Flag deals with no activity in 30 days"],\n'
            '  "body": "Pull all open opportunities, group them by stage, and '
            'flag stale deals for follow-up."\n'
            "}\n"
            "```"
        )

    if "summarize the reference document" in p:
        # synopsize_knowledge -> JSON object.
        return (
            "{\n"
            '  "synopsis": "The company sells CRM software to mid-market firms. '
            'Revenue is recognized on payment. Accounts are scored by activity '
            'recency.",\n'
            '  "key_facts": ["Target market is mid-market", '
            '"Revenue recognized on payment", "Activity recency drives scoring"]\n'
            "}"
        )

    # Unknown prompt -> empty, so callers exercise the fallback path.
    return ""


if __name__ == "__main__":
    print("=== file_normalizer offline self-test (no network) ===\n")

    defs_input = (
        "Revenue: the total of all customer payments received.\n"
        "revenue - sum of payments.\n"
        "Active account: an account that had activity in the last 90 days.\n"
        "Churned account: no activity for 180 days or more."
    )
    print("1) normalize_definitions ->")
    print(normalize_definitions(defs_input, _mock_infer))
    print()

    rules_input = (
        "When analyzing sales, join accounts to opportunities using account_id.\n"
        "A deal counts as won when its stage equals 'Closed Won'.\n"
        "Ignore internal QA accounts (names beginning with QA_)."
    )
    print("2) extract_rules_from_doc ->")
    for r in extract_rules_from_doc(rules_input, _mock_infer):
        print(f"   - {r}")
    print()

    skill_input = (
        "Monthly Pipeline Review\n"
        "At the end of each month pull every open opportunity, group them by "
        "stage, and flag any deal with no activity in the last 30 days so reps "
        "can follow up."
    )
    print("3) draft_skill_from_doc ->")
    print(json.dumps(draft_skill_from_doc(skill_input, _mock_infer), indent=2))
    print()

    knowledge_input = (
        "Our company builds CRM software for mid-market businesses. We "
        "recognize revenue when a customer payment is received. Every account "
        "is scored based on how recently it has had activity."
    )
    print("4) synopsize_knowledge ->")
    print(json.dumps(synopsize_knowledge(knowledge_input, _mock_infer), indent=2))
    print()

    # Fallback demonstration: an infer that always fails / returns junk.
    print("5) fallback path (broken infer, no content lost) ->")

    def _broken(_p: str) -> str:
        raise RuntimeError("simulated LLM outage")

    print("   definitions:", repr(normalize_definitions("a = b\nc = d", _broken)))
    print("   rules:", extract_rules_from_doc("rule one\nrule two", _broken))
    print("   skill:", draft_skill_from_doc("Do The Thing\nstep a\nstep b", _broken))
    print("   knowledge:", synopsize_knowledge("fact one\nfact two", _broken))
