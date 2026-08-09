"""Auto must reach the prompt box as an EMPTY selection, never as every agent.

WHAT AGENT SCOPE IS. `frontend/utils/agentSelection.ts` states the contract:
scope is a MODE, not a set. **Auto is the absence of a pin**, encoded as an
empty selection, and `resolveAgentAuto` decides it purely from
`selectedIds.length === 0`. Its docstring is explicit that the two must never be
inferred from each other — "selecting every agent by hand is manual, not Auto:
it freezes today's roster."

WHAT BROKE. `pages/index.vue` handed the prompt box `selectedAgentObjects`, a
composable computed that RESOLVES an empty selection into every agent:

    if (selectedAgents.value.length === 0) return agents.value

That is the right answer for a list — "which agents are in scope right now" —
and the wrong thing to hand a component whose entire definition of Auto is an
empty array. The selector received three ids and could not distinguish that
from an administrator having pinned all three by hand, so `isAuto` was false.

WHAT THE USER SAW. Opening the agent picker on a brand-new chat showed a tick
against every agent and none against the "Auto — any agent you can access" row.
Measured 2026-08-08 on a browser with NO stored selection, so this was the
default path, not a leftover preference.

WHY IT MATTERS BEYOND THE TICK. A non-empty selection means the backend's
`report_selection_is_auto` never fires. Every new chat was hard-pinned to that
day's roster: an agent created later was silently out of scope, and access
changes did not follow. The picker promised "any agent you can access" and
delivered a frozen list.

WHAT THIS PINS. That the home page passes the PIN and not the resolved roster.

★THE NAMES SWAPPED AT up531, THE PROPERTY DID NOT. Our 0.0.528.7 fix added a
local `pinnedDataSources` in `pages/index.vue`. Upstream then fixed the same
bug more deeply (commit 29b5a823c) and moved the split INTO the composable:

    selectedAgentObjects   the PIN       — a plain map, empty stays empty
    effectiveAgentObjects  the RESOLVER  — empty fans out to every agent

So `selectedAgentObjects` — the identifier this test was originally written to
keep AWAY from the prompt box — is now the correct thing to hand it, and the
hazard moved to `effectiveAgentObjects`. `pinnedDataSources` is gone; the port
adopted upstream's mechanism rather than running two side by side.
This test therefore follows one ALIAS HOP: `pages/index.vue` binds a computed
that is nothing but `<name>.value`, and the pin-ness now lives in useAgent.ts.
It still does not care about any particular name — only that what reaches
`:initialSelectedDataSources` cannot be the fan-out.

★LIMIT, stated honestly: this reads `.vue`/`.ts` as TEXT — it cannot evaluate
the computed or mount the component, so it proves the empty branch EXISTS, not
that it fires. Only the browser can prove the tick lands on the Auto row.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"
HOME = FRONTEND / "pages" / "index.vue"
SELECTION_UTIL = FRONTEND / "utils" / "agentSelection.ts"
USE_AGENT = FRONTEND / "composables" / "useAgent.ts"

# The composable computed that RESOLVES a scope. Never a pin. See the docstring:
# this was `selectedAgentObjects` until up531 swapped the two roles.
RESOLVER = "effectiveAgentObjects"
# The composable computed that carries the PIN. Empty must stay empty.
PIN = "selectedAgentObjects"

BINDING = re.compile(r':initialSelectedDataSources="([^"]+)"')


def _computed_body(src: str, name: str):
    """The body of `const <name> = computed(...)`, or None.

    Brace/paren matching rather than a regex, so a multi-line body with nested
    calls is captured whole — a one-line regex would silently truncate the very
    branch this test is looking for.
    """
    m = re.search(rf"const\s+{re.escape(name)}\s*=\s*computed\s*\(", src)
    if not m:
        return None
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i + 1 : j]
    return None


# --------------------------------------------------------------------------
# Checkers take source TEXT so the same logic can be pointed at the pre-fix
# file to prove it fails. See the module docstring.
# --------------------------------------------------------------------------


def check_auto_is_emptiness(util_src: str) -> list:
    """The contract itself: Auto is length === 0, not a shape."""
    if re.search(r"isAuto:\s*\(?\s*(?:input\.)?selectedIds.*?\)?\s*\.length\s*===\s*0", util_src, re.S):
        return []
    return [
        "frontend/utils/agentSelection.ts no longer defines Auto as an empty "
        "selection. Every check below is derived from that definition; if the "
        "encoding changed, this whole test needs rewriting rather than fixing."
    ]


def check_resolver_still_fans_out(use_agent_src: str) -> list:
    """`effectiveAgentObjects` turns empty into ALL. That is why it is not a pin.

    If this ever stops being true the trap is gone and the guard below is
    over-strict — so it is asserted rather than assumed.
    """
    body = _computed_body(use_agent_src, RESOLVER)
    if body is None:
        return [f"{RESOLVER} is no longer a computed in useAgent.ts — re-verify this test by hand"]
    if re.search(r"length\s*===\s*0", body) and "agents.value" in body:
        return []
    return [
        f"{RESOLVER} no longer resolves an empty selection into every agent. "
        "The hazard this test guards may have moved; re-read it before editing."
    ]


def check_the_pin_stays_empty(use_agent_src: str) -> list:
    """The other half, and the one that actually protects the user.

    `selectedAgentObjects` must be a plain projection of the selected ids — a
    `.map`, so an empty selection yields an empty array. The original bug was
    literally an `if (length === 0) return agents.value` sitting in THIS
    computed. If that fan-out ever comes back here, every consumer downstream
    is handed a full manual pin again and no amount of correct binding in
    `pages/index.vue` can save it.
    """
    body = _computed_body(use_agent_src, PIN)
    if body is None:
        return [f"{PIN} is no longer a computed in useAgent.ts — re-verify this test by hand"]
    if "agents.value" in body and re.search(r"length\s*===\s*0", body):
        return [
            f"{PIN} fans an empty selection out into every agent again. That is "
            "the original 0.0.528.7 defect, moved one file: Auto reaches the "
            "prompt box as a manual pin of the whole roster, `isAuto` is false, "
            "and every new chat is frozen to today's agents."
        ]
    if ".map(" not in body:
        return [
            f"{PIN} is no longer a plain projection of the selected ids "
            f"({' '.join(body.split())[:120]}). It must stay one, or an empty "
            "selection can stop meaning Auto."
        ]
    return []


def check_prompt_box_gets_a_pin(home_src: str) -> list:
    """Whatever is bound to the prompt box must be able to be empty."""
    problems = []
    bindings = set(BINDING.findall(home_src))
    if not bindings:
        return [
            "pages/index.vue binds :initialSelectedDataSources nowhere. Either "
            "the prompt box lost its selection prop or the page was replaced — "
            "see test_the_home_route_is_still_the_composer.py."
        ]

    for expr in sorted(bindings):
        name = expr.strip()
        body = _computed_body(home_src, name)
        if body is None:
            # Bound to something that isn't a local computed (a ref, a prop).
            # Nothing to inspect; only the direct-resolver case is a known bug.
            if name.startswith(RESOLVER):
                problems.append(
                    f"pages/index.vue binds :initialSelectedDataSources to {name}, "
                    f"which resolves Auto into every agent. The prompt box would "
                    f"read that as a manual pin of the whole roster."
                )
            continue

        collapsed = " ".join(body.split())
        # The defect shape: a computed that is nothing but the resolver.
        if re.fullmatch(rf"\(\s*\)\s*=>\s*{RESOLVER}\.value\s*,?", collapsed):
            problems.append(
                f"pages/index.vue binds :initialSelectedDataSources to `{name}`, "
                f"which is exactly `{RESOLVER}.value`. That computed turns Auto "
                f"(an empty selection) into every agent, so the selector sees a "
                f"full manual pin and `isAuto` is false. A new chat is then "
                f"frozen to today's roster and the backend never runs Auto "
                f"resolution. Pass the pin: yield [] when nothing is selected."
            )
            continue

        # ★ONE ALIAS HOP. Since up531 the pin is built in the composable, so the
        # page's computed is legitimately just `() => selectedAgentObjects.value`
        # with no empty branch of its own. Accept that, having named the pin —
        # `check_the_pin_stays_empty` is what proves the pin really is one.
        alias = re.fullmatch(rf"\(\s*\)\s*=>\s*({PIN})\.value\s*,?", collapsed)
        if alias:
            continue

        # Otherwise it must be capable of yielding an empty selection here.
        can_be_empty = "[]" in collapsed
        guarded = ("isAllAgents" in collapsed) or re.search(r"\.length", collapsed)
        if not (can_be_empty and guarded):
            problems.append(
                f"pages/index.vue binds :initialSelectedDataSources to `{name}`, "
                f"whose body has no empty-selection branch and is not the pin "
                f"`{PIN}`: {collapsed[:160]}. Auto is encoded as an empty array, "
                f"so a binding that can never be empty can never express Auto."
            )
    return problems


# --------------------------------------------------------------------------
# The tests.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def home_src() -> str:
    assert HOME.exists(), f"{HOME} is missing"
    return HOME.read_text(encoding="utf-8")


def test_auto_is_still_encoded_as_an_empty_selection():
    assert SELECTION_UTIL.exists(), f"{SELECTION_UTIL} is missing"
    problems = check_auto_is_emptiness(SELECTION_UTIL.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_the_resolver_still_expands_empty_to_every_agent():
    assert USE_AGENT.exists(), f"{USE_AGENT} is missing"
    problems = check_resolver_still_fans_out(USE_AGENT.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_the_pin_never_fans_out():
    """The half that protects the user, rather than the test's assumptions."""
    assert USE_AGENT.exists(), f"{USE_AGENT} is missing"
    problems = check_the_pin_stays_empty(USE_AGENT.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_the_original_defect_is_still_detected():
    """★Red-proof, carried in the file so it can never rot into a comment.

    Reconstructs the exact pre-0.0.528.7 shape — the fan-out living in the
    computed the page binds — and requires the checkers to reject it. Without
    this, the up531 rename could have been absorbed by pointing the constants
    at whatever the tree happens to say, and every run would stay green while
    the guard protected nothing.
    """
    pre_fix_use_agent = (
        "const selectedAgentObjects = computed(() => {\n"
        "  if (selectedAgents.value.length === 0) return agents.value\n"
        "  return selectedAgents.value.map(id => agentsById.value.get(id))\n"
        "})\n"
    )
    assert check_the_pin_stays_empty(pre_fix_use_agent), (
        "the pin check no longer rejects a fan-out inside the pin — it would "
        "have passed the very bug it was written for"
    )

    pre_fix_home = (
        '<PromptBoxV2 :initialSelectedDataSources="selectedDataSources" />\n'
        "const selectedDataSources = computed(() => effectiveAgentObjects.value)\n"
    )
    assert check_prompt_box_gets_a_pin(pre_fix_home), (
        "the binding check no longer rejects the prompt box being handed the "
        "resolver directly"
    )


def test_the_prompt_box_receives_the_pin_not_the_resolved_roster(home_src):
    problems = check_prompt_box_gets_a_pin(home_src)
    assert not problems, "\n".join(problems)


def test_the_questions_strip_still_gets_the_resolved_roster(home_src):
    """The split must not go too far the other way.

    `DataSourceQuestionsHome` and the instructions panel genuinely want "what is
    in scope", which in Auto is every agent. Handing THEM the empty pin would
    blank the suggested questions and the instructions list — a regression
    introduced by fixing the one above carelessly.
    """
    m = re.search(r':data_sources="([A-Za-z_$][\w$]*)"', home_src)
    assert m, (
        "the suggested-questions strip no longer receives a named computed for "
        "its data sources; in Auto it would render nothing at all"
    )
    name = m.group(1)
    body = _computed_body(home_src, name)
    assert body and RESOLVER in body, (
        f"`{name}` — what the suggested-questions strip is handed — no longer "
        f"resolves the full roster via `{RESOLVER}`, so anything showing current "
        f"scope goes empty in Auto. This is the regression you get by fixing the "
        f"binding above carelessly and pointing EVERYTHING at the pin."
    )
