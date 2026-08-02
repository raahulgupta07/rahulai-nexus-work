"""A bound on how long one question may spend looking around before answering.

`agent_max_steps` already bounds the planner loop, but it bounds *steps*, and
its default (100) is more than an order of magnitude above any real run — so
in practice nothing bounds the time a single question can spend exploring. One
cross-source question spent 140s of its 277s inside two `inspect_data` calls;
the loop cap was never approached and could not have been.

This bounds the thing that actually runs away: cumulative wall time spent in
pre-answer inspection. It is a budget, not a call count, because inspection
cost varies by two orders of magnitude — a run doing five cheap inspections is
not the problem, and a call count would punish it while letting two expensive
ones through.

★ The bound is deliberately set ABOVE the worst observed good run. The 277s
question produced the best answer of its test run; a bound that would have
truncated it trades a large quality loss for a small latency win. What this
prevents is the unbounded case — the run that keeps inspecting because nothing
tells it to stop.

Exhausting the budget never fails a turn. The inspection tool is simply no
longer offered, and the planner proceeds with the evidence it has.
"""

from typing import Any, Dict, Iterable, Optional

# Tools whose job is to look at data before answering. Only tools that execute
# generated code against a source belong here — catalog reads (describe_tables,
# read_instruction) are cheap and bounded by the schema, not by the data.
INSPECTION_TOOLS = ("inspect_data",)

# Milliseconds of cumulative inspection allowed per run.
#
# Defence of the number: observed single inspections ranged ~19s to ~78s. The
# slow cross-source run spent 140s across two calls and answered well. 180s
# clears that run untouched while capping the pathological case at roughly two
# expensive inspections plus one cheap one. Below ~150s we would start cutting
# runs that were converging; far above it the bound stops being a bound.
DEFAULT_INSPECTION_BUDGET_MS = 180_000


class InspectionBudget:
    """Tracks inspection time for one run and reports when it is spent.

    Pure bookkeeping — no I/O, no clock of its own. The caller feeds it the
    duration each tool execution already recorded.
    """

    def __init__(
        self,
        budget_ms: int = DEFAULT_INSPECTION_BUDGET_MS,
        tools: Iterable[str] = INSPECTION_TOOLS,
    ):
        try:
            budget = int(budget_ms)
        except (TypeError, ValueError):
            budget = DEFAULT_INSPECTION_BUDGET_MS
        # A non-positive budget would disable inspection entirely on the first
        # call, which is a worse product than no bound at all.
        self.budget_ms = max(1, budget)
        self.tools = tuple(tools)
        self.spent_ms = 0.0
        self.calls = 0

    def tracks(self, tool_name: Optional[str]) -> bool:
        return tool_name in self.tools

    def record(self, tool_name: Optional[str], duration_ms: Any) -> None:
        """Add one tool execution. Ignores anything that is not an inspection."""
        if not self.tracks(tool_name):
            return
        self.calls += 1
        try:
            elapsed = float(duration_ms)
        except (TypeError, ValueError):
            return
        if elapsed <= 0:
            return
        self.spent_ms += elapsed

    @property
    def exhausted(self) -> bool:
        return self.spent_ms >= self.budget_ms

    @property
    def remaining_ms(self) -> float:
        return max(0.0, self.budget_ms - self.spent_ms)

    def notice(self) -> str:
        """What the planner is told when the budget runs out."""
        return (
            f"Inspection budget spent: {self.spent_ms / 1000:.0f}s across "
            f"{self.calls} inspection call(s), limit {self.budget_ms / 1000:.0f}s. "
            "No further data inspection is available on this turn — answer from "
            "the evidence already gathered, or produce the result directly, and "
            "state plainly anything you could not verify."
        )

    def user_notice(self) -> str:
        """What the PERSON is told when the budget runs out.

        ★`notice` above goes to the planner and always has. Nothing went to the
        reader: the inspection tool simply stopped being offered, the planner
        answered with whatever it had, and the report showed an ordinary
        completed turn. An answer built on deliberately curtailed evidence has
        to say so, or it is indistinguishable from one built on all of it.
        """
        return (
            f"Stopped looking after {self.spent_ms / 1000:.0f}s of data "
            "inspection and answered with what had been gathered."
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "spent_ms": round(self.spent_ms),
            "budget_ms": self.budget_ms,
            "calls": self.calls,
            "exhausted": self.exhausted,
        }


def resolve_budget_ms(organization_settings: Any) -> int:
    """Budget for this org, falling back to the default.

    Reads an optional `agent_inspection_budget_ms` setting if the org has one
    so the bound is tunable without a code change, and clamps it so a stored
    value can neither disable inspection nor make it unbounded.
    """
    value: Any = None
    try:
        cfg = (
            organization_settings.get_config("agent_inspection_budget_ms")
            if organization_settings
            else None
        )
        value = getattr(cfg, "value", None)
    except Exception:
        value = None
    if isinstance(value, bool) or value is None:
        return DEFAULT_INSPECTION_BUDGET_MS
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INSPECTION_BUDGET_MS
    return max(30_000, min(900_000, ms))
