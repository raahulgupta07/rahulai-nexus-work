"""A sync that fails must say which side failed, and must land that fact.

The incident this guards: on 2026-08-03 production Postgres rejected new
connections for an hour. Every Fabric sync that ticked in that window died on
`asyncpg.exceptions.InvalidPasswordError`. The crash handler tried to record
the failure — on the same failing engine — the write failed, and the failure
was discarded by a bare `except Exception: pass`. The row stayed `running`
forever, four later callers each blocked the full 600s on it, and the user was
told to "attach or refresh the lakehouse", as though the credential they had
just used was the problem.

Two separate things have to be true to stop that recurring, and this file
checks both:

1. The failure is **classified** — ours or theirs — and worded accordingly.
2. The code that records it does not silently drop it, and a row with no
   runner behind it is not treated as work in flight forever.

★No schema here. Classification is pure, and the service checks are made
against source rather than a live run, so this file stays in `tests/unit/fork`
where the per-test migration is a no-op. See CLAUDE.md — split by cost.
"""
import ast
import inspect

import pytest

from app.services.indexing_failures import (
    FAILURE_INFRASTRUCTURE,
    FAILURE_SOURCE,
    FAILURE_UNKNOWN,
    classify_failure,
    describe_failure,
    is_retryable,
)


# ─────────────────────────── classification ───────────────────────────


class _FakeAsyncpgError(Exception):
    """Stands in for `asyncpg.exceptions.InvalidPasswordError`.

    Classification reads the exception's module, not its name, so the fake has
    to lie about where it lives — which is also the point of the test: any
    future exception from that driver classifies correctly without this file
    being taught its name.
    """
    __module__ = "asyncpg.exceptions"


class _FakePyodbcError(Exception):
    __module__ = "pyodbc"


class _FakeSqlAlchemyOperationalError(Exception):
    __module__ = "sqlalchemy.exc"


_FakeSqlAlchemyOperationalError.__name__ = "OperationalError"


def test_our_own_database_refusing_us_is_infrastructure():
    exc = _FakeAsyncpgError('password authentication failed for user "dash"')
    assert classify_failure(exc) == FAILURE_INFRASTRUCTURE


def test_the_source_refusing_us_is_not_our_outage():
    exc = _FakePyodbcError("Login failed for user 'fabric-svc'")
    assert classify_failure(exc) == FAILURE_SOURCE


def test_a_wrapped_driver_error_is_still_found():
    """SQLAlchemy re-raises the driver's error as its own. Looking only at the
    outermost type sees `OperationalError` and learns nothing — the whole
    `__cause__` chain has to be walked."""
    inner = _FakeAsyncpgError('password authentication failed for user "dash"')
    outer = _FakeSqlAlchemyOperationalError("(asyncpg.exceptions.InvalidPasswordError)")
    outer.__cause__ = inner
    assert classify_failure(outer) == FAILURE_INFRASTRUCTURE


def test_a_wrapped_source_driver_error_is_still_found():
    inner = _FakePyodbcError("08001 Cannot open server")
    outer = _FakeSqlAlchemyOperationalError("(pyodbc.OperationalError)")
    outer.__cause__ = inner
    assert classify_failure(outer) == FAILURE_SOURCE


def test_a_customers_own_postgres_is_not_blamed_on_us():
    """★The trap this exists to hold shut.

    A customer can connect their *own* Postgres as a data source, and it can
    refuse us with the identical sentence. Matching on the message
    'password authentication failed' would report our outage banner, retry
    automatically against a credential that is genuinely wrong, and bury a real
    customer problem. The driver is the discriminator: `asyncpg` is used for
    `DASH_DATABASE_URL` and nowhere under `app/data_sources/`.
    """
    exc = _FakePyodbcError('password authentication failed for user "reporting"')
    assert classify_failure(exc) == FAILURE_SOURCE
    assert not is_retryable(classify_failure(exc))


def _asyncpg_users(source: str) -> bool:
    """True when this module actually USES asyncpg, not merely names it.

    ★A plain `"asyncpg" in source` scan is what this test shipped with, and it
    went red against a completely correct tree: up538's `storage_safe_name`
    docstring explains *why* a lone surrogate is fatal by naming the driver
    that refuses it. A sentence about asyncpg does not make an asyncpg error
    ambiguous — an import does. Same family as the comment-stripping guards
    that read their own docstring and failed citing their own explanation.

    Walks the AST, so a mention inside a string or comment cannot trip it.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:  # unparseable file is a different problem, not this one
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "asyncpg" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "asyncpg":
                return True
        elif isinstance(node, ast.Attribute):
            # asyncpg.connect(...), asyncpg.exceptions.X — used without importing
            # it at module level (a local import inside a function still shows up
            # as ast.Import above).
            if isinstance(node.value, ast.Name) and node.value.id == "asyncpg":
                return True
    return False


def test_the_asyncpg_detector_still_catches_real_use():
    """★The red proof, carried inside the test rather than done once at a shell.

    Relaxing the scan from text to AST is exactly the kind of change that can
    quietly make a guard incapable of failing. These four samples are the
    shapes that WOULD break the discriminator; the fifth is the shape that
    provoked the relaxation and must not.
    """
    assert _asyncpg_users("import asyncpg")
    assert _asyncpg_users("import asyncpg.exceptions as e")
    assert _asyncpg_users("from asyncpg import Connection")
    assert _asyncpg_users("def f():\n    import asyncpg\n    return asyncpg")
    assert _asyncpg_users("async def f(p):\n    return asyncpg.connect(p)")

    assert not _asyncpg_users('"""asyncpg refuses a lone surrogate."""\nx = 1')
    assert not _asyncpg_users("# asyncpg is used for DASH_DATABASE_URL only\nx = 1")


def test_asyncpg_is_not_used_by_any_data_source_client():
    """The premise of the test above, asserted rather than assumed.

    If a data source client ever starts using asyncpg, the discriminator stops
    being sound and this test fails before the misclassification ships.
    """
    from pathlib import Path

    clients = Path(__file__).resolve().parents[3] / "app" / "data_sources"
    assert clients.is_dir()
    offenders = [
        str(p.relative_to(clients))
        for p in clients.rglob("*.py")
        if _asyncpg_users(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == [], (
        "A data source client now imports asyncpg, so an asyncpg error is no "
        f"longer proof of OUR database failing: {offenders}"
    )


def test_an_unrecognised_failure_is_never_guessed_at():
    assert classify_failure(ValueError("something went sideways")) == FAILURE_UNKNOWN


def test_a_bare_sqlalchemy_error_is_unknown_not_infrastructure():
    """It is *probably* ours. 'Probably' does not earn an automatic retry."""
    assert classify_failure(_FakeSqlAlchemyOperationalError("pool closed")) == FAILURE_UNKNOWN


def test_a_self_referential_cause_chain_terminates():
    a = ValueError("a")
    b = ValueError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert classify_failure(a) == FAILURE_UNKNOWN


# ─────────────────────────── retry policy ───────────────────────────


@pytest.mark.parametrize(
    "kind,expected",
    [
        (FAILURE_INFRASTRUCTURE, True),
        (FAILURE_SOURCE, False),
        (FAILURE_UNKNOWN, False),
    ],
)
def test_only_our_own_failures_retry_automatically(kind, expected):
    assert is_retryable(kind) is expected


def test_unknown_does_not_retry_because_a_wrong_guess_locks_an_account():
    """Stated separately from the table above because it is a decision, not a
    detail: retrying an unknown failure hourly against a source that is
    refusing us is how a service account gets locked out."""
    assert is_retryable(FAILURE_UNKNOWN) is False


# ─────────────────────────── wording ───────────────────────────


def test_our_outage_is_not_described_as_the_users_problem():
    exc = _FakeAsyncpgError('password authentication failed for user "dash"')
    text = describe_failure(exc)
    assert "our own service" in text
    assert "retry automatically" in text
    # Nothing telling the user to go fix their credentials.
    assert "credentials" not in text


def test_a_source_failure_tells_the_user_what_to_check():
    text = describe_failure(_FakePyodbcError("Login failed"))
    assert "data source refused" in text
    assert "credentials" in text


def test_the_raw_error_survives_into_every_message():
    """The sentence is for the user; the raw error is what makes a support
    ticket answerable. Replacing one with the other loses the case."""
    for exc in (
        _FakeAsyncpgError("RAW-MARKER-1"),
        _FakePyodbcError("RAW-MARKER-2"),
        ValueError("RAW-MARKER-3"),
    ):
        assert str(exc) in describe_failure(exc)


def test_an_exception_with_no_message_still_describes():
    assert "_FakePyodbcError" in describe_failure(_FakePyodbcError())


# ─────────────────── the service actually uses all this ───────────────────


def _service_source() -> str:
    from app.services import connection_indexing_service

    return inspect.getsource(connection_indexing_service)


def test_the_crash_handler_no_longer_swallows_its_own_failure():
    """★The specific line that cost the incident.

    `except Exception: pass` around the write that records a crash means the
    one failure we most need on record — our database being unreachable — is
    the one guaranteed to be discarded.
    """
    tree = ast.parse(_service_source())
    swallowed = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # ★Inspect the block being GUARDED, not the handler. The handler body
        # of the original bug was literally `pass` and nothing else — reading
        # it told you nothing about what had just been discarded. Written the
        # wrong way round first, and it passed against the unfixed code.
        guarded = "".join(ast.unparse(stmt) for stmt in node.body)
        if "FAILED" not in guarded and "finished_at" not in guarded:
            continue
        for handler in node.handlers:
            if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                swallowed.append(handler.lineno)
    assert swallowed == [], (
        "A terminal-status write is wrapped in `except Exception: pass` at "
        f"line(s) {swallowed} — a lost failure leaves the row running forever."
    )


def test_the_terminal_write_retries():
    """One attempt is not enough: the outage that killed the run is the same
    outage that blocks recording it."""
    from app.services import connection_indexing_service as svc

    assert svc._TERMINAL_WRITE_ATTEMPTS > 1
    assert len(svc._TERMINAL_WRITE_BACKOFF_S) >= 1
    assert all(d > 0 for d in svc._TERMINAL_WRITE_BACKOFF_S)


def test_a_lost_terminal_write_is_logged_loudly():
    source = inspect.getsource(
        __import__(
            "app.services.connection_indexing_service", fromlist=["_write_terminal_failure"]
        )._write_terminal_failure
    )
    assert "logger.error" in source, (
        "If every attempt to record a failure fails, that must reach the logs "
        "at ERROR — it is now the only trace the run ever existed."
    )


def test_both_failure_paths_go_through_the_durable_write():
    """The inner handler (the sync itself failed) and the outer one (the runner
    crashed) both used to hand-roll the same write, and only one of them
    recorded the connection-level error. Neither retried."""
    source = _service_source()
    assert source.count("await _write_terminal_failure(") >= 2


def test_the_failure_class_reaches_the_row():
    source = _service_source()
    assert '"error_kind"' in source, (
        "The class has to be persisted, not just computed — the UI and the "
        "retry sweeper both read it back off the row."
    )


# ─────────────────── a row with no runner is not work ───────────────────


def test_get_active_reaps_a_row_whose_process_died():
    """★Without this a zombie row blocks the connection permanently.

    `start()` returns any non-terminal row instead of kicking off work, so a
    run whose process was killed means the sync can never be started again —
    not by the user, not by the sweeper, not by a retry.
    """
    from app.services.connection_indexing_service import ConnectionIndexingService

    source = inspect.getsource(ConnectionIndexingService.get_active)
    assert "_reap_if_abandoned" in source


def test_the_abandon_threshold_outlasts_the_longest_quiet_phase():
    """A QVD convert can churn for ~40 minutes, but it flushes progress while
    it does — and every commit moves `updated_at`. The threshold guards
    against no writes at all, so it only has to outlast the gap between
    writes. It must still be comfortably longer than the 600s a caller waits,
    or reaping becomes a way to paper over a live run.
    """
    from app.services import connection_indexing_service as svc

    assert svc._ABANDONED_AFTER_MINUTES >= 15


def test_reaping_marks_the_row_infrastructure_so_it_retries():
    from app.services.connection_indexing_service import ConnectionIndexingService

    source = inspect.getsource(ConnectionIndexingService._reap_if_abandoned)
    assert "infrastructure" in source
    assert "FAILED" in source


def test_a_failed_reap_does_not_break_the_read_path():
    """`get_active` is called from request handlers. Tidying is best-effort;
    failing to tidy must not fail the read."""
    from app.services.connection_indexing_service import ConnectionIndexingService

    source = inspect.getsource(ConnectionIndexingService._reap_if_abandoned)
    assert "except Exception:" in source
    assert "return False" in source


# ─────────────────── backoff ladder ───────────────────


def test_the_infra_retry_ladder_is_bounded():
    """5m is right for a blip. Repeating 5m forever during a real outage means
    every connection in the org retrying into a database that is already
    refusing connections."""
    from app.services import connection_indexing_service as svc

    assert svc._INFRA_RETRY_DELAY_MINUTES > 0
    assert svc._INFRA_RETRY_MAX_DOUBLINGS >= 1
    ceiling = svc._INFRA_RETRY_DELAY_MINUTES * (2 ** svc._INFRA_RETRY_MAX_DOUBLINGS)
    assert ceiling <= 24 * 60, "The ladder must not climb past a day"


def test_every_terminal_sync_failure_is_classified_before_it_is_reported():
    """★The per-user Fabric/Power BI tracker is a *different* code path from
    the connection indexing runner, and it is the one the member's strip
    actually polls. Fixing only the runner would have left the exact screen in
    the incident screenshot unchanged.
    """
    from pathlib import Path

    routes = Path(__file__).resolve().parents[3] / "app" / "routes"
    for name in ("fabric_user_signin.py", "powerbi_user_signin.py"):
        source = (routes / name).read_text(encoding="utf-8")
        terminal = [
            line for line in source.splitlines()
            if "prog.fail(" in line and "str(e)" in line
        ]
        assert terminal == [], (
            f"{name} still reports a raw exception to the member without "
            f"classifying it: {terminal}"
        )
        assert "classify_failure" in source, f"{name} never classifies a failure"


def test_the_tracker_carries_the_class_to_the_browser():
    from app.services import connection_sync_progress as prog

    import inspect as _inspect

    assert "error_kind" in _inspect.getsource(prog._serialize)
    # The idle payload must carry the key too, or the field arrives `undefined`
    # on a first poll and every `=== 'infrastructure'` check reads as false for
    # a reason that has nothing to do with the failure.
    assert "error_kind" in _inspect.getsource(prog._idle)
    assert "error_kind" in _inspect.getsource(prog.fail)


def test_the_ui_reads_the_class_and_never_the_message():
    """★A customer's own Postgres refusing them produces the identical
    sentence. If the strip ever pattern-matches the error text to decide whose
    fault it was, it will tell a member with a genuinely wrong credential that
    our service is down and to wait for a retry that cannot help.
    """
    from pathlib import Path

    strip = (
        Path(__file__).resolve().parents[4]
        / "frontend" / "components" / "datasources" / "ConnectionSyncStrip.vue"
    )
    source = strip.read_text(encoding="utf-8")
    assert "error_kind === 'infrastructure'" in source
    assert "password authentication" not in source.lower()


def test_the_ladder_counts_only_the_trailing_run_of_failures():
    """A success anywhere in recent history proves the outage ended. Counting
    total failures instead would leave a connection that failed six times last
    month waiting hours after a single blip today."""
    source = inspect.getsource(
        __import__(
            "app.services.connection_indexing_service", fromlist=["_infra_retry_delay"]
        )._infra_retry_delay
    )
    assert "break" in source, "The count must stop at the first non-infra outcome"
