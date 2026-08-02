"""Pressing stop must come back 200, not 500.

The run WAS being stopped correctly. The route then returned the raw
``Completion`` ORM row with no ``response_model``, FastAPI ran
``jsonable_encoder`` over it, and that walks relationships — completion →
report → completions → report — until it raises ``RecursionError``.

So the user pressed stop, the run stopped, and the UI showed a failure. The
worst shape a bug takes: it worked and said it hadn't, so the natural response
is to press it again.
"""

import inspect

import pytest
from pydantic import BaseModel

from app.routes import completion as completion_routes
from app.schemas.completion_v2_schema import CompletionStopResponse


def _route(path_suffix: str, method: str = "POST"):
    for r in completion_routes.router.routes:
        if getattr(r, "path", "").endswith(path_suffix) and method in getattr(r, "methods", ()):
            return r
    raise AssertionError(f"no {method} route ending {path_suffix!r}")


# ── the route ────────────────────────────────────────────────────────────────


def test_the_stop_route_declares_what_it_returns():
    """★The guard. Without a response_model FastAPI serialises the ORM graph."""
    route = _route("/sigkill")

    assert route.response_model is CompletionStopResponse, (
        "the stop route is encoding whatever the service hands back again"
    )


def test_the_declared_shape_holds_no_relationships():
    """A schema that reaches back into the ORM would recurse just the same."""
    for name, field in CompletionStopResponse.model_fields.items():
        annotation = field.annotation
        assert not (
            inspect.isclass(annotation)
            and issubclass(annotation, BaseModel)
        ), f"{name} nests a model; keep the stop response flat"


def test_it_carries_what_a_caller_would_need_to_confirm_the_stop():
    fields = set(CompletionStopResponse.model_fields)

    assert {"id", "report_id", "status", "sigkill"} <= fields


# ── the shape it builds from ─────────────────────────────────────────────────


class FakeCompletion:
    """Stands in for the ORM row, including the relationship that recursed."""

    def __init__(self):
        self.id = "c-1"
        self.report_id = "r-1"
        self.status = "stopped"
        self.sigkill = None
        self.report = self  # the cycle


def test_the_row_serialises_without_following_the_cycle():
    """★This is the actual regression, reproduced: give the schema a row whose
    `report` points back at itself. Before, encoding this raised
    RecursionError. Reading only the four declared fields cannot."""
    out = CompletionStopResponse.model_validate(FakeCompletion())

    assert out.id == "c-1"
    assert out.status == "stopped"
    assert "report" not in out.model_dump()


def test_a_run_that_had_already_finished_still_reports_its_real_status():
    """Stop on a finished run stamps sigkill but leaves the answer standing —
    so the response must not claim 'stopped'."""
    row = FakeCompletion()
    row.status = "success"

    assert CompletionStopResponse.model_validate(row).status == "success"


# ── the siblings ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suffix,method", [("/queued", "DELETE"), ("/steer", "POST")])
def test_the_neighbouring_routes_do_not_return_orm_rows(suffix, method):
    """They return plain dicts today. If one is ever changed to return a row,
    it needs a response_model too — this is where that gets noticed."""
    route = _route(suffix, method)
    source = inspect.getsource(route.endpoint)

    assert route.response_model is not None or "completion_service." in source
