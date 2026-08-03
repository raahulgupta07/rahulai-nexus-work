"""Tell apart the two kinds of thing that can end an indexing run.

A sync can fail because the customer's source refused us — bad Fabric
credential, a lakehouse that no longer exists, a permission that was revoked.
It can also fail because *our own* infrastructure was unreachable at that
moment: on 2026-08-03 production Postgres rejected new connections for an hour
(`password authentication failed for user "dash"`, 88 times, only on connects
made after an idle gap), and every Fabric sync that happened to tick during
that window died with `asyncpg.exceptions.InvalidPasswordError`.

Those two need opposite handling and opposite words:

- **Infrastructure** is ours, it is transient, and it is not the user's problem
  to solve. Retry it, and when we tell the user, say it was us.
- **Source** is a real answer from the far side. Retrying an unchanged wrong
  credential every hour just burns the account's lockout budget. Show it, name
  it, and wait for a human.

★Anything we cannot place goes to ``UNKNOWN`` rather than to a guess.
``UNKNOWN`` is treated as non-retryable, so a misclassification costs a delayed
retry — never an infinite loop against a source that keeps saying no.

★The discriminator for our own database is the driver, not the message.
``asyncpg`` appears nowhere under ``app/data_sources/`` — it is the driver for
``DASH_DATABASE_URL`` and nothing else — so an ``asyncpg`` exception raised
inside an indexing run is by construction about *our* database, whatever it
says. Matching on the string "password authentication failed" would instead
mark a customer's own Postgres data source as our outage.
"""
from __future__ import annotations

from typing import Optional


FAILURE_INFRASTRUCTURE = "infrastructure"
FAILURE_SOURCE = "source"
FAILURE_UNKNOWN = "unknown"


# Modules that only ever talk to our own database. See the note above.
_OUR_DATABASE_MODULES = ("asyncpg",)

# SQLAlchemy's connection-level errors. These are raised by any engine, ours or
# a customer's, so the module alone does not decide — see `_sqlalchemy_kind`.
_SQLALCHEMY_CONNECTION_ERRORS = (
    "OperationalError",
    "InterfaceError",
    "DBAPIError",
    "DisconnectionError",
    "InvalidRequestError",
)

# Raised by the client layer that speaks to customer sources.
_SOURCE_MODULE_PREFIXES = (
    "pyodbc",
    "msal",
    "azure",
    "google",
    "googleapiclient",
    "snowflake",
    "duckdb",
    "clickhouse",
    "pymysql",
    "psycopg2",
    "requests",
    "httpx",
    "urllib3",
)


def _module_root(exc: BaseException) -> str:
    return (type(exc).__module__ or "").split(".")[0]


def _causes(exc: BaseException, limit: int = 10):
    """Walk `__cause__`/`__context__`, yielding the chain including `exc`.

    A driver error is almost always wrapped by the time it reaches the run's
    outer handler — SQLAlchemy re-raises asyncpg's `InvalidPasswordError` as an
    `OperationalError`, so looking only at the outermost type sees nothing.
    Bounded so a self-referential chain cannot spin.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and len(seen) < limit:
        if id(current) in seen:
            break
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def classify_failure(exc: BaseException) -> str:
    """Return one of the ``FAILURE_*`` constants for an indexing failure."""
    chain = list(_causes(exc))

    for link in chain:
        if _module_root(link) in _OUR_DATABASE_MODULES:
            return FAILURE_INFRASTRUCTURE

    for link in chain:
        if _module_root(link) in _SOURCE_MODULE_PREFIXES:
            return FAILURE_SOURCE

    # A bare SQLAlchemy connection error with nothing underneath it naming a
    # driver. Our engines are the only ones this layer opens directly, so it is
    # more likely ours than a source's — but "likely" is not "known".
    for link in chain:
        if _module_root(link) == "sqlalchemy" and type(link).__name__ in _SQLALCHEMY_CONNECTION_ERRORS:
            return FAILURE_UNKNOWN

    return FAILURE_UNKNOWN


def is_retryable(kind: str) -> bool:
    """Only an infrastructure failure earns an automatic retry.

    ★``UNKNOWN`` deliberately returns False. An automatic retry against a
    source that is genuinely refusing us is how an account gets locked out.
    """
    return kind == FAILURE_INFRASTRUCTURE


def describe_failure(exc: BaseException, kind: Optional[str] = None) -> str:
    """A sentence for the user, in front of the raw error.

    The raw error is kept: it is what makes a support ticket answerable. What
    changes is that it no longer arrives unexplained.
    """
    if kind is None:
        kind = classify_failure(exc)
    detail = str(exc).strip() or type(exc).__name__

    if kind == FAILURE_INFRASTRUCTURE:
        return (
            "This sync stopped because our own service was briefly unreachable, "
            "not because of anything wrong with your connection. It will retry "
            f"automatically. ({detail})"
        )
    if kind == FAILURE_SOURCE:
        return (
            "The data source refused this sync. Check the connection's "
            f"credentials and permissions, then try again. ({detail})"
        )
    return f"This sync failed and did not report a cause we recognise. ({detail})"
