"""The login limiter counts in the database, in one statement.

Its first version read the counter, added one in Python, and wrote it back.
That is a lost update: attempts arriving together all read the value BEFORE any
of them commits, so each computes a small number and none sees itself as over
the limit. Measured on a live instance: **40 simultaneous attempts against one
address were all allowed**, and the counter finished at 15 rather than 40.

It matters because guessing passwords is done in parallel. The old code stopped
the slow case, missed the fast one, and kept advertising a limit of 20.

★What these tests can and cannot prove.
These run against SQLite, which serialises writers with a database-level lock.
A "concurrency" assertion here would pass no matter how the counter were
written, and would be worse than no test because it would look like proof. So
this file covers the SEMANTICS the upsert has to get right — counting, the
window roll, the first hit, the boundary — and the concurrency itself is proven
live against PostgreSQL, which is where the bug was found.
"""
import pytest
from sqlalchemy import select, text

from app.core import login_throttle as lt
from app.core.timestamps import utcnow_naive
from app.dependencies import async_session_maker
from app.models.auth_throttle import AuthThrottle

pytestmark = pytest.mark.asyncio


async def _attempts(db, bucket):
    return (await db.execute(
        select(AuthThrottle.attempts).where(AuthThrottle.bucket == bucket)
    )).scalar()


async def test_the_first_attempt_creates_exactly_one_row():
    async with async_session_maker() as db:
        allowed, retry = await lt._hit(db, "login:ip:t1", 20)
        assert allowed is True and retry == 0
        assert await _attempts(db, "login:ip:t1") == 1

        rows = (await db.execute(text(
            "select count(*) from auth_throttle where bucket = 'login:ip:t1'"
        ))).scalar()
        assert rows == 1


async def test_each_attempt_advances_the_count_by_one():
    async with async_session_maker() as db:
        for expected in range(1, 6):
            await lt._hit(db, "login:ip:t2", 20)
            assert await _attempts(db, "login:ip:t2") == expected


async def test_the_limit_refuses_only_after_it_is_exceeded():
    """★The boundary is where an off-by-one hides. A limit of 3 must allow
    exactly 3 and refuse the 4th."""
    async with async_session_maker() as db:
        results = [await lt._hit(db, "login:ip:t3", 3) for _ in range(5)]
        allowed = [a for a, _ in results]
        assert allowed == [True, True, True, False, False], allowed


async def test_a_refusal_says_how_long_to_wait():
    async with async_session_maker() as db:
        for _ in range(3):
            await lt._hit(db, "login:ip:t4", 2)
        allowed, retry = await lt._hit(db, "login:ip:t4", 2)
        assert allowed is False
        # ★FORK PATCH: was `lt.WINDOW_SECONDS`, a module constant that no longer
        # exists — the window became configurable per installation and is read
        # through `_window_seconds()`. The test kept referencing the constant and
        # died on AttributeError, so what it actually guards (a refusal tells you
        # how long to wait, and never longer than one window) went unchecked.
        assert 0 < retry <= lt._window_seconds()


async def test_an_expired_window_starts_over_in_the_same_statement():
    """★The roll used to be a SECOND decision, made on a row already read — the
    same lost update as the increment, somewhere nobody would look for it. It
    now happens inside the counting statement."""
    async with async_session_maker() as db:
        for _ in range(5):
            await lt._hit(db, "login:ip:t5", 20)
        assert await _attempts(db, "login:ip:t5") == 5

        # age the window past the limit
        old = utcnow_naive().replace(year=utcnow_naive().year - 1)
        await db.execute(text(
            "update auth_throttle set window_start = :w where bucket = 'login:ip:t5'"
        ), {"w": old})
        await db.commit()

        allowed, _ = await lt._hit(db, "login:ip:t5", 20)
        assert allowed is True
        assert await _attempts(db, "login:ip:t5") == 1, "the window did not roll"

        rows = (await db.execute(text(
            "select count(*) from auth_throttle where bucket = 'login:ip:t5'"
        ))).scalar()
        assert rows == 1, "rolling the window must not insert a second row"


async def test_a_blocked_bucket_does_not_block_a_different_one():
    async with async_session_maker() as db:
        for _ in range(4):
            await lt._hit(db, "login:ip:t6", 2)
        blocked, _ = await lt._hit(db, "login:ip:t6", 2)
        assert blocked is False

        other, _ = await lt._hit(db, "login:ip:t7", 2)
        assert other is True, "one address being blocked must not block another"


async def test_clearing_removes_only_the_account_bucket():
    """★Otherwise an attacker holding one valid account could reset the address
    limit at will by signing in to their own."""
    async with async_session_maker() as db:
        await lt._hit(db, "login:ip:t8", 20)
        await lt._hit(db, "login:email:someone@example.com", 50)

        await lt.clear_login_throttle(db, "someone@example.com")

        assert await _attempts(db, "login:email:someone@example.com") is None
        assert await _attempts(db, "login:ip:t8") == 1, "the address count was cleared too"


async def test_counting_failure_lets_the_request_through():
    """★Fail OPEN. A limiter that locks everyone out when the database hiccups
    has turned a degraded dependency into a total outage — and unlike an
    admission check, being unable to count is not evidence anything is wrong."""
    class Broken:
        async def execute(self, *a, **k):
            raise RuntimeError("database is unhappy")

        async def commit(self):
            raise RuntimeError("database is unhappy")

        async def rollback(self):
            return None

    allowed, retry = await lt._hit(Broken(), "login:ip:t9", 1)
    assert allowed is True and retry == 0
