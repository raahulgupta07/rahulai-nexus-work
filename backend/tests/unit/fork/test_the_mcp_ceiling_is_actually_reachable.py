"""`_inline_budget`'s window ceiling must be a cap, not an aspiration.

``execute_mcp._inline_budget`` clamps the operator's ``mcp_result_inline_chars``
and then caps it against what this model's transcript can actually hold. The cap
is computed from ``transcript_budget_tokens``, imported INSIDE the function:

    try:
        from app.ai.agents.planner.transcript_bridge import transcript_budget_tokens
        transcript_budget = transcript_budget_tokens(...)
    except Exception:
        return budget          # ← the ceiling silently does not apply
    ceiling = int(transcript_budget * _PREVIEW_BUDGET_SHARE * _CHARS_PER_TOKEN)
    return min(budget, max(ceiling, 0))

★A bare ``except Exception`` around a lazy import makes a MISSING SAFETY CAP
indistinguishable from a normal code path. Nothing raises, nothing is logged,
and the function still returns a perfectly plausible number — the operator's own
setting. On a 32k model that is a 50,000-char preview against a 16k-token
transcript budget: two protected tool results the decay ladder cannot shrink,
crowding out the conversation, with nothing anywhere explaining why.

This is not hypothetical. During the 511→518 port ``transcript_bridge`` was
absent from the tree and the cap was inert; only two optional tests under
``tests/unit`` noticed, and that suite is not the pre-push gate — this one is.
It is the same defect class as ``test_the_settings_rename_reaches_its_consumers``:
a rename hid inside a bare ``except`` and shipped broken alert links for three
releases. A silent fallback is how a dead code path survives a release.

★``tests/unit/fork`` has a no-op ``run_migrations``, so nothing here may touch a
database. ``_inline_budget`` is pure — keep it that way.
"""
from __future__ import annotations

import pytest

from app.ai.tools.implementations.execute_mcp import (
    _CHARS_PER_TOKEN,
    _PREVIEW_BUDGET_SHARE,
    _inline_budget,
)


class _Cfg:
    def __init__(self, value):
        self.value = value


class _Settings:
    """Stand-in for OrganizationSettings — only get_config is read.

    Deliberately the same shape as ``tests/unit/test_mcp_inline_budget.py``'s,
    so the gate test and the full-coverage test cannot drift apart in what they
    think the caller looks like.
    """

    def __init__(self, value):
        self._value = value

    def get_config(self, key):
        return _Cfg(self._value) if key == "mcp_result_inline_chars" else None


# ``transcript_budget_tokens`` honours BOW_TRANSCRIPT_BUDGET_TOKENS / _RATIO as
# test overrides. If either leaks in from the environment the numbers below stop
# following from the window, and this file would measure the environment instead
# of the code.
@pytest.fixture(autouse=True)
def _no_budget_overrides(monkeypatch):
    monkeypatch.delenv("BOW_TRANSCRIPT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("BOW_TRANSCRIPT_BUDGET_RATIO", raising=False)
    monkeypatch.delenv("DASH_TRANSCRIPT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("DASH_TRANSCRIPT_BUDGET_RATIO", raising=False)


def test_the_module_the_ceiling_depends_on_still_imports():
    """The import that the bare `except` would swallow.

    Asserted directly, at module scope, so a missing or broken
    ``transcript_bridge`` fails HERE with an ImportError naming the module —
    rather than downstream as a budget that is merely, quietly, too large.
    """
    from app.ai.agents.planner.transcript_bridge import transcript_budget_tokens

    assert callable(transcript_budget_tokens)


def test_the_ceiling_measurably_binds_on_a_small_window():
    """★THE LOAD-BEARING ASSERTION — the one that was red while the module was
    missing, and the only one here that can tell a working cap from an absent one.

    A 32k model's transcript budget is half its window (``_DEFAULT_BUDGET_RATIO``
    = 0.5) = 16,000 tokens; one preview may occupy ``_PREVIEW_BUDGET_SHARE`` of
    that, converted at ``_CHARS_PER_TOKEN``. Every number below is derived from
    those constants rather than written down, so tuning the share moves the
    expectation with it and this stays a test of the mechanism.

    With the ceiling inert, ``_inline_budget`` returns the configured 50,000 and
    the strict `<` fails. That is the whole point: it discriminates.
    """
    configured = 50_000
    window = 32_000

    # ★Measure BEFORE importing anything. If the import came first this test
    # would die on ImportError and never exercise the discrimination it exists
    # for — passing for the right reason by accident, and reporting the missing
    # module rather than the inert cap that is the actual product defect.
    got = _inline_budget(_Settings(configured), window)
    assert got < configured, (
        "the window ceiling is not applying — `_inline_budget` handed back the "
        "operator's setting unchanged on a 32k model. The likely cause is the "
        "bare `except Exception` in _inline_budget swallowing a failed import of "
        "app.ai.agents.planner.transcript_bridge, which turns a missing safety "
        "cap into an ordinary-looking return value."
    )
    assert got > 0

    from app.ai.agents.planner.transcript_bridge import transcript_budget_tokens

    expected = int(
        transcript_budget_tokens(type("_S", (), {"context_window_tokens": window})())
        * _PREVIEW_BUDGET_SHARE
        * _CHARS_PER_TOKEN
    )
    assert got == expected, (
        "the ceiling applied but not with the share/conversion the constants "
        f"declare: got {got}, constants give {expected}"
    )

    # Monotone in the window, or a bigger model would somehow get a smaller
    # slice — which would mean the cap is tracking something other than context.
    assert got < _inline_budget(_Settings(configured), 128_000) < configured


def test_a_large_window_leaves_the_operator_value_alone():
    """A cap that bites on the models this actually runs on is a bug, not a cap.

    200k is where the deployed models sit; at 100k transcript tokens the ceiling
    is comfortably above a 50,000-char preview, so `min` must pick the setting.
    """
    assert _inline_budget(_Settings(50_000), 200_000) == 50_000
    assert _inline_budget(_Settings(50_000), 1_000_000) == 50_000


def test_an_unknown_window_is_not_guessed_at():
    """★Deliberate upstream behaviour, guarded so nobody "fixes" it.

    A provider that reports no context window returns None — or 0, or a string
    someone forgot to parse. Substituting a small default there would silently
    shrink every result on that provider, which is a worse failure than the one
    the ceiling exists to prevent, because it looks like the data is simply
    short. No window to reason about ⇒ the operator's number stands.
    """
    for window in (None, 0, -1, "200k"):
        assert _inline_budget(_Settings(50_000), window) == 50_000, (
            f"window={window!r} is not a usable window and must leave the "
            "configured budget untouched"
        )
