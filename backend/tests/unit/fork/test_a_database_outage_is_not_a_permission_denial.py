""""You do not have permission" must not be how a database outage is reported.

Two containers claimed the same hostname, half of production's database
connections were refused, and every one of those refusals surfaced to the user
as **403 Forbidden** — 75 in 48 hours on one deployment. The reader goes
hunting for a permissions bug; the actual fault, a database that cannot be
reached, is invisible. That is a large part of why it hid for weeks.

`resolve_permissions` catches everything and returns an empty permission set,
so the caller denies access. Failing CLOSED is correct and is unchanged here —
nobody gains access because a query failed. What changes is the answer given:
a connection-level failure is now 503, which still refuses the request while
naming the real problem, and lets monitoring tell an outage apart from a member
legitimately being told no.

★★★The classifier matches asyncpg errors by MODULE, not only the SQLAlchemy
exception types. Measured: a bad password through `create_async_engine` raises
`asyncpg.exceptions.InvalidPasswordError`, whose whole MRO is asyncpg — it is
NOT a `sqlalchemy.exc.OperationalError`/`InterfaceError`/`DBAPIError`. The
first version of the fix tested only those three, matched nothing, and would
have shipped looking correct while changing nothing at all.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "backend" / "app" / "core" / "permission_resolver.py"

from app.core.permission_resolver import _is_infrastructure_failure


class _FakeAsyncpgError(Exception):
    """Stands in for asyncpg's real class, which is not importable in every env."""
    __module__ = "asyncpg.exceptions"


class InvalidPasswordError(_FakeAsyncpgError):
    __module__ = "asyncpg.exceptions"


class InvalidAuthorizationSpecificationError(_FakeAsyncpgError):
    __module__ = "asyncpg.exceptions"


def test_an_asyncpg_auth_failure_is_infrastructure():
    """★The exact production exception."""
    class Real(InvalidAuthorizationSpecificationError):
        __module__ = "asyncpg.exceptions"
    assert _is_infrastructure_failure(Real("password authentication failed")) is True


def test_a_sqlalchemy_operational_error_is_infrastructure():
    from sqlalchemy.exc import OperationalError
    assert _is_infrastructure_failure(OperationalError("s", {}, Exception("x"))) is True


def test_a_network_error_is_infrastructure():
    assert _is_infrastructure_failure(ConnectionError("refused")) is True
    assert _is_infrastructure_failure(TimeoutError("slow")) is True


@pytest.mark.parametrize("exc", [
    ValueError("bad value"),
    KeyError("missing"),
    AttributeError("none has no attribute"),
    RuntimeError("logic"),
])
def test_an_ordinary_bug_is_not_infrastructure(exc):
    """★The control that matters.

    If everything counted as infrastructure, a genuine bug in permission
    resolution would answer 503 and never be treated as a denial — which is
    the opposite failure and just as wrong.
    """
    assert _is_infrastructure_failure(exc) is False


def test_the_resolver_still_fails_closed_for_ordinary_errors():
    """★Unchanged behaviour: a non-infrastructure failure still denies."""
    src = SRC.read_text(encoding="utf-8")
    body = src[src.index("async def resolve_permissions("):]
    body = body[: body.index("\nasync def ", 10)] if "\nasync def " in body[10:] else body
    assert "return ResolvedPermissions()" in body, (
        "the resolver no longer fails closed on an ordinary error"
    )


def test_the_infrastructure_branch_answers_503():
    src = SRC.read_text(encoding="utf-8")
    assert "status_code=503" in src, "an unreachable database is still reported as a denial"
    assert "_is_infrastructure_failure(exc)" in src, "the classifier is not used"
