"""A database that will not answer must not read as "this org wants no redaction".

`load_redactor_for_org` returns None for several ordinary reasons — unlicensed,
disabled, no active rules. `llm._apply_pii` treats None as "send the prompt as
it is". So when the loader ALSO returned None on an exception, an unreachable
database and a deliberately-empty policy became the same value, and prompts went
to the third-party model unredacted with a single WARNING to show for it.

★★★Measured in production 2026-08-04: a ~5 hour Postgres auth failure produced
9 "PII redactor load failed" lines. Nothing else recorded that redaction had
stopped. The org's `block` mode — which refuses a prompt containing PII outright
— lives in the same settings row, so an org on the strictest setting was handed
the loosest one, silently.

★The fix is to remember the last policy a SUCCESSFUL read produced and fall back
to it, rather than to None. These tests pin the three states apart:
no-policy-configured, read-failed-with-history, read-failed-cold.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest

from app.ai.llm.pii import loader as pii_loader


class _BoomSession:
    """A session whose every query raises, like asyncpg with a bad password."""

    async def execute(self, *_a, **_kw):
        raise RuntimeError("password authentication failed for user \"dash\"")


class _RowSession:
    """A session that returns one OrganizationSettings-shaped row."""

    def __init__(self, config):
        self._config = config

    async def execute(self, *_a, **_kw):
        cfg = self._config

        class _Settings:
            config = cfg

        class _Result:
            def scalar_one_or_none(self_inner):
                return _Settings()

        return _Result()


def _maker(session):
    @asynccontextmanager
    async def _cm():
        yield session

    # session_maker is called as `session_maker()` and used as an async context
    # manager, which is exactly what the decorated factory produces.
    return _cm


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Each test starts with empty caches and the enterprise gate open.

    ★The gate is checked BEFORE the database is touched, so without this every
    test here would pass for the wrong reason — returning None because the
    feature is unlicensed, never reaching the code under test.

    ★`_LAST_GOOD` is cleared via getattr-with-default, NOT attribute access.
    The first version of this fixture touched it directly, so against a loader
    that does not have it every test ERRORED during setup instead of FAILING on
    its assertion. That looks like proof the guard works and is not: an error at
    setup says the symbol is missing, never that the behaviour is wrong. A guard
    must fail for the reason it exists.
    """
    monkeypatch.setattr(pii_loader, "has_feature", lambda *_a, **_kw: True)

    def _reset():
        pii_loader._CACHE.clear()
        getattr(pii_loader, "_LAST_GOOD", {}).clear()

    _reset()
    yield
    _reset()


ACTIVE_POLICY = {
    "pii_protection": {
        "enabled": True,
        "rules": [{"id": "email", "enabled": True}],
    }
}


def _load(org, session):
    return asyncio.run(pii_loader.load_redactor_for_org(org, _maker(session)))


def test_a_successful_read_with_no_policy_returns_none():
    """The ordinary "nothing configured" answer still works."""
    assert _load("org-empty", _RowSession({})) is None


def test_a_failed_read_keeps_the_policy_it_last_saw():
    """★The defect. A live policy, then the database dies — redaction must not
    silently stop."""
    first = _load("org-1", _RowSession(ACTIVE_POLICY))
    assert first is not None and first.active, (
        "fixture problem: the policy under test never built an active redactor, "
        "so the fallback assertion below would pass for the wrong reason"
    )

    pii_loader._CACHE.clear()  # force a re-read; the DB is now broken
    after = _load("org-1", _BoomSession())

    assert after is first, (
        "a failed settings read fell back to None — indistinguishable from an "
        "org that configured no redaction, so llm._apply_pii sends the prompt "
        "unredacted"
    )


def test_a_failed_read_is_not_cached_as_the_new_truth():
    """The remembered policy must survive repeated failures, not decay to None
    once the failure itself gets cached."""
    _load("org-2", _RowSession(ACTIVE_POLICY))
    for _ in range(3):
        pii_loader._CACHE.clear()
        again = _load("org-2", _BoomSession())
        assert again is not None and again.active


def test_a_cold_failure_has_nothing_to_fall_back_to():
    """★The one residual fail-open case, asserted so it stays deliberate.

    A process that has never read this org cannot invent a policy, and refusing
    every prompt would turn a degraded database into a total outage — orgs with
    no policy at all reach this same code path. It returns None, and the loader
    logs at ERROR saying prompts are going out unredacted.
    """
    assert _load("org-never-seen", _BoomSession()) is None


def test_the_failure_is_logged_at_error_not_warning(caplog):
    """★It was WARNING, which is why five hours of it passed unnoticed among
    5,227 INFO lines."""
    _load("org-3", _RowSession(ACTIVE_POLICY))
    pii_loader._CACHE.clear()
    with caplog.at_level("ERROR", logger=pii_loader.logger.name):
        _load("org-3", _BoomSession())
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "a privacy control falling back to a remembered policy must be findable "
        "in the log"
    )


def test_none_is_a_real_answer_and_not_the_unknown_marker():
    """`None` means "this org configured nothing" — a legitimate result — so it
    cannot double as "never read". Conflating them is what made the original
    bug invisible."""
    last_good = getattr(pii_loader, "_LAST_GOOD", None)
    assert last_good is not None, (
        "the loader keeps no memory of a successful read, so a failed one has "
        "nothing to fall back to and must return None — the original defect"
    )
    assert getattr(pii_loader, "_UNKNOWN", None) is not None
    _load("org-4", _RowSession({}))
    assert "org-4" in last_good
    assert last_good["org-4"] is None
