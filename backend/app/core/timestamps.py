"""One way to stamp "now" for this database.

★★★Every timestamp column in this schema is a plain ``DateTime`` — Postgres
``timestamp without time zone``. asyncpg REFUSES a timezone-aware datetime for
such a column:

    asyncpg.exceptions.DataError: invalid input for query argument $1

It is not a coercion and not a warning; the statement fails. Three login-related
writes did exactly this and every one of them was wrapped in a swallow, so the
product simply had no record of anyone ever signing in — the column read NULL
for accounts that had signed in many times, and nothing anywhere said why.

So: store naive UTC, read it back as UTC, and do the conversion in exactly one
place. ``datetime.utcnow()`` would produce the same value, but it is deprecated
in 3.12 and says nothing about which clock it means; this spells out that the
value IS UTC and that the tzinfo is dropped deliberately at the boundary.
"""
from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """The current UTC time, with tzinfo stripped for a naive DateTime column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    """Read a naive column back as an aware UTC datetime.

    The inverse of ``utcnow_naive``. Use it before comparing a stored value
    against ``datetime.now(timezone.utc)`` — subtracting a naive from an aware
    datetime raises TypeError, which in a swallowed block looks like the
    comparison simply never being true.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
