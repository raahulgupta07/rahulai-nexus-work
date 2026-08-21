"""DEF-013/014/015/016 — the four silences from Phase 3.

Each of these is a place the product knew something and did not say it.

DEF-013  `_refresh_user_overlay` was `except Exception: pass`. It is the
         FALLBACK that still saves a lone tenant's tables, it raised on every
         Power BI insert on dev, and nothing was logged anywhere. A member could
         press Connect forever: the scan re-ran and succeeded, the insert failed
         identically each time, and every surface said "connected".

DEF-014  The run finished with the number the tenant SCAN saw, never with the
         number that was STORED. That is how the Connect dialog came to read
         "6 tables, all switched on, 2 tenants connected" beside a Tables page
         reading "No tables found." Both were true about different things.

DEF-015  `sweep_abandoned` required `started_at IS NOT NULL`, so a row that
         never recorded a start could never be swept — the row most likely to be
         broken was the one guaranteed to sit at `running` forever.

DEF-016  `Completion.status` defaulted to 'success', so a row that had done
         nothing was indistinguishable from a finished turn.

★Three of the four ORIGINAL observations (8.2, 10.3, and 9.2's truncation half)
were measured on dev and do NOT reproduce on this tree — dev serves `.543.8` out
of an image tagged `0.0.543.4`. What is fixed here is the part that is real on
THIS code, and the tests say which is which rather than implying all four were
live defects. See `test_the_dev_only_observations_do_not_reproduce_here`.
"""
import ast
import inspect
import pathlib

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[3]
APP = BACKEND / "app"


def _src(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


class TestTheFallbackNoLongerSwallows:
    """DEF-013."""

    def _fn_source(self) -> str:
        from app.routes.powerbi_user_signin import _refresh_user_overlay
        return inspect.getsource(_refresh_user_overlay)

    def test_it_does_not_pass_on_exception(self):
        """★The defect, stated as the thing that must never come back."""
        body = self._fn_source()
        # Strip the docstring: it QUOTES the broken form, and a source scan that
        # reads its own explanation is a mistake this repo has made repeatedly.
        tree = ast.parse(body.lstrip())
        fn = tree.body[0]
        stripped = ast.unparse(ast.Module(body=fn.body[1:], type_ignores=[]))
        assert "pass" not in stripped.split("except")[-1][:200] or "logging" in stripped, (
            "_refresh_user_overlay swallows again"
        )

    def test_it_logs_with_a_traceback(self):
        body = self._fn_source()
        assert "exc_info=True" in body

    def test_it_returns_the_reason_rather_than_none(self):
        """A caller that owns a progress tracker can only report what it is
        told. Logging alone leaves every screen saying the same wrong thing.

        ★Written first as `... or sig.return_annotation is not None`, which is
        vacuously true for `-> None` and passed against the unfixed file. The
        annotation is checked as a STRING for exactly that reason.
        """
        from app.routes.powerbi_user_signin import _refresh_user_overlay
        sig = inspect.signature(_refresh_user_overlay)
        assert "Optional[str]" in str(sig.return_annotation), str(sig.return_annotation)
        body = self._fn_source()
        assert "return None" in body, "the success path must still say nothing went wrong"
        assert body.rstrip().endswith("[:300]"), "the failure path must return the reason"

    def test_it_rolls_the_session_back(self):
        """★One serialization error became THREE failures because the poisoned
        session took the auto re-learn and the sync notification down with it,
        and nothing on screen connected them."""
        assert "rollback" in self._fn_source()

    def test_sign_in_still_cannot_fail_on_this(self):
        """★Positive control. The original contract — a failed overlay must not
        fail the sign-in — is correct and is kept. A 'fix' that re-raises passes
        every other test in this class and breaks the product."""
        body = self._fn_source()
        tree = ast.parse(body.lstrip())
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                pytest.fail("_refresh_user_overlay re-raises — sign-in must not fail on it")


class TestTheRunReportsWhatItStored:
    """DEF-014."""

    def test_the_finish_count_is_counted_not_scanned(self):
        src = _src("routes/powerbi_user_signin.py")
        assert "_count_persisted_tables" in src
        assert "discovered = sum(" in src, "the scan's own number must keep its own name"

    def test_discovering_tables_and_storing_none_is_a_failure(self):
        src = _src("routes/powerbi_user_signin.py")
        assert "if discovered > 0 and persisted == 0:" in src

    def test_it_counts_only_tables_the_member_can_reach(self):
        from app.routes.powerbi_user_signin import _count_persisted_tables
        body = inspect.getsource(_count_persisted_tables)
        assert "UserDataSourceTable" in body
        assert "is_accessible" in body

    def test_a_count_that_could_not_be_taken_is_not_zero(self):
        """★"I could not tell" and "there are none" must stay different. A
        failed COUNT reporting a failed SYNC invents an outage on a run that may
        have been perfect."""
        from app.routes.powerbi_user_signin import _count_persisted_tables
        body = inspect.getsource(_count_persisted_tables)
        assert "return None" in body
        assert "return 0" not in body

    def test_the_failure_makes_no_claim_about_cause(self):
        """★`error_kind='infrastructure'` is the ONLY value anything renders, and
        it does not mean "our fault" — `keeper_service` skips the
        'last sync failed' item for it and `sync_notifications` downgrades the
        message to "sync was interrupted". Claiming it here would hide this
        defect on the exact screen the defect is about."""
        src = _src("routes/powerbi_user_signin.py")
        block = src[src.index("if discovered > 0 and persisted == 0:"):]
        block = block[:block.index("return")]
        assert "error_kind" not in block.replace("# ", "").split('await prog.fail')[-1]

    def test_infrastructure_really_does_suppress_the_keeper_item(self):
        """★The assumption the test above rests on, pinned. If this ever stops
        being true, the reasoning in that comment is stale and should be
        re-read rather than trusted."""
        src = _src("services/keeper_service.py")
        assert 'latest["error_kind"] != "infrastructure"' in src


class TestAStuckRunCanAlwaysBeSwept:
    """DEF-015."""

    def test_the_age_test_falls_back_to_created_at(self):
        from app.services.sync_runs import sweep_abandoned
        body = inspect.getsource(sweep_abandoned)
        assert "coalesce" in body
        assert "created_at" in body

    def test_it_no_longer_requires_a_start_time(self):
        from app.services.sync_runs import sweep_abandoned
        body = inspect.getsource(sweep_abandoned)
        tree = ast.parse(body.lstrip())
        stripped = ast.unparse(ast.Module(body=tree.body[0].body[1:], type_ignores=[]))
        assert "started_at.isnot(None)" not in stripped, (
            "a row with no start time can never be swept again"
        )

    def test_it_still_only_touches_per_user_rows(self):
        """★Positive control. The org scope has its own reaper and the two must
        not fight over the same rows."""
        from app.services.sync_runs import sweep_abandoned
        assert "user_id.isnot(None)" in inspect.getsource(sweep_abandoned)

    def test_it_still_only_touches_non_terminal_rows(self):
        from app.services.sync_runs import sweep_abandoned
        assert "TERMINAL_INDEXING_STATUSES" in inspect.getsource(sweep_abandoned)


class TestATurnIsNotBornFinished:
    """DEF-016."""

    def test_the_default_is_not_a_terminal_state(self):
        from app.models.completion import Completion
        default = Completion.__table__.c.status.default
        assert default.arg == "in_progress"

    def test_every_system_row_still_declares_its_status(self):
        """★The real protection. The default only matters when a site forgets;
        this asserts none of them do, which is what makes changing the default
        a no-op today and a safety net tomorrow."""
        omissions = []
        for path in sorted(APP.rglob("*.py")):
            if ".bak" in path.name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "Completion"):
                    continue
                if not any(k.arg == "status" for k in node.keywords if k.arg):
                    omissions.append(f"{path.name}:{node.lineno}")
        assert omissions == [], (
            f"Completion(...) built without an explicit status at {omissions} — "
            "the row's state would come from a column default rather than from "
            "the code that knows what happened"
        )

    def test_a_system_turn_starts_in_progress(self):
        """The three chat paths, pinned by construction site."""
        src = _src("services/completion_service.py")
        assert src.count('role="system",\n                status="in_progress"') >= 2


class TestWhatWasMeasuredOnDev:
    """★Recorded so nobody re-diagnoses these from the roadmap text alone.

    Three Phase 3 / Phase 2 observations do not reproduce on this tree. They
    were measured on dev, which serves `.543.8` from an image tagged
    `0.0.543.4`. These assertions state what IS true here; if one ever fails,
    the defect has arrived on this tree and the roadmap entry is live again.
    """

    def test_started_at_is_written_by_both_indexing_paths(self):
        """10.3. Dev showed every row with a NULL start; local showed 482 rows
        with none. Both writers set it."""
        assert "started_at = datetime.utcnow()" in _src("services/connection_indexing_service.py")
        assert "started_at=datetime.utcnow()" in _src("services/sync_runs.py")

    def test_the_dead_shared_writer_is_still_dead(self):
        """★`ConnectionIndexing.mark_running()` has no callers — the two paths
        each stamp the timestamp by hand. Not fixed here because unifying them
        is a change with no observable effect on this tree, but it is exactly
        the kind of divergence that produced the dev/local split."""
        hits = []
        for path in sorted(APP.rglob("*.py")):
            if ".bak" in path.name or path.name == "connection_indexing.py":
                continue
            if "mark_running" in path.read_text(encoding="utf-8"):
                hits.append(path.name)
        assert hits == [], f"mark_running now has callers ({hits}) — unify the two writers"
