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
The fix is a second computed (`pinnedDataSources`) that yields `[]` in Auto;
this test does not care about that name, only that whatever is bound to
`:initialSelectedDataSources` is capable of being empty.

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

# The composable computed that RESOLVES a scope. Never a pin.
RESOLVER = "selectedAgentObjects"

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
    """`selectedAgentObjects` turns empty into ALL. That is why it is not a pin.

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

        # Otherwise it must be capable of yielding an empty selection.
        can_be_empty = "[]" in collapsed
        guarded = ("isAllAgents" in collapsed) or re.search(r"\.length", collapsed)
        if not (can_be_empty and guarded):
            problems.append(
                f"pages/index.vue binds :initialSelectedDataSources to `{name}`, "
                f"whose body has no empty-selection branch: {collapsed[:160]}. "
                f"Auto is encoded as an empty array, so a binding that can never "
                f"be empty can never express Auto."
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
    assert re.search(r':data_sources="selectedDataSources"', home_src), (
        "the suggested-questions strip no longer receives the resolved roster; "
        "in Auto it would render nothing at all"
    )
    body = _computed_body(home_src, "selectedDataSources")
    assert body and RESOLVER in body, (
        "`selectedDataSources` no longer resolves the full roster, so anything "
        "showing current scope goes empty in Auto"
    )
