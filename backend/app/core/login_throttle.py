"""Rate limiting for the two doors anyone can knock on without an account.

Sign-in and registration had no limit at all. An attacker could try passwords
against a known address as fast as the network allowed — and this product now
admits people automatically through single sign-on and a directory, which makes
the local password form the softest remaining target rather than the only one.

Four deliberate choices:

★★★**Counted in the database, not in memory.** The app runs uvicorn with up to
4 workers. A module-level counter is invisible to the other three, so a stated
limit of 10 would admit 40. This codebase has made that mistake twice already
(``learn_progress``, the LLM circuit breaker). A limiter that enforces a
different number than the one it states is worse than none, because it is
believed.

★★**Per IP *and* per email, with different limits.** Counting only by email
lets anyone lock a real user out of their own account by failing their sign-in
on purpose — a denial of service against the victim, delivered by the security
feature. The IP limit is the one that actually stops guessing; the email limit
is a wide backstop against a distributed attempt, set high enough that a person
mistyping their own password never reaches it.

★★**A successful sign-in is refunded to its address.** The address charge is
made by a dependency, before anyone knows whether the password was right, so an
honest arrival cost the same as a guess. Behind a shared address — an office, a
VPN concentrator, a Citrix farm — that is an outage rather than a limit: 200
directory users signing in correctly through one address got 20 in and 180
refused. ``refund_login_ip`` returns exactly one attempt on success, so the cap
counts failures without ever being clearable at will. Open WebUI avoids the
problem by having no address limit at all, only a per-email one; keeping ours
costs nothing once success is free.

★**Fails OPEN.** If the counter cannot be read or written, the request is
allowed. A throttle that locks everybody out when the database hiccups has
turned a degraded dependency into a total outage — and unlike the admission
checks in S1, being unable to count is not evidence that anything is wrong.
"""
import logging
import uuid
from datetime import timedelta
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import DateTime, Integer, select, text
from sqlalchemy import column as sa_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timestamps import utcnow_naive
from app.dependencies import get_async_db
from app.models.auth_throttle import AuthThrottle

log = logging.getLogger(__name__)

# ★The limits are settings, not constants, because the right number depends on
# how many people share one source address — see the block in settings/config.py
# that defines them. Read through functions so a deployment can change the
# setting without this module having captured the old value at import.


def _window_seconds() -> int:
    from app.settings.config import settings
    return settings.login_rate_limit_window_seconds


def _login_per_ip() -> int:
    """One address guessing passwords.

    ★Counts FAILURES in practice: a sign-in that succeeds hands its increment
    back in ``refund_login_ip``. Before that, an office of 200 people behind one
    NAT address exhausted this on honest arrivals alone — measured, 20 in and
    180 refused.
    """
    from app.settings.config import settings
    return settings.login_rate_limit_per_ip


def _login_per_email() -> int:
    """One account being guessed at from many addresses.

    Wide, because a person who genuinely cannot remember their password must not
    be locked out by it. This bucket is per-person, so a shared office address
    does not pool into it — it is the limit Open WebUI relies on exclusively.
    """
    from app.settings.config import settings
    return settings.login_rate_limit_per_email


# Account creation is rarer and more expensive than a sign-in attempt.
REGISTER_PER_IP = 5


def client_ip(request: Request) -> str:
    """The caller's address, honouring one proxy hop.

    ★``X-Forwarded-For`` is caller-supplied and trivially spoofed, so this is
    only trustworthy behind a proxy that overwrites it. Behind one, the socket
    address is the proxy and every user shares a bucket, which would be worse.
    The left-most entry is used; a spoofed value costs an attacker nothing but
    also buys nothing beyond what a fresh address would.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first[:100]
    client = request.client
    return (client.host if client else "unknown")[:100]


# ★★★ONE statement, because counting in Python does not survive concurrency.
#
# The first version read the row, added one in Python, and wrote it back. That
# is a lost update: when attempts arrive together they all read the counter
# BEFORE any of them commits, so every one of them computes a small number and
# not one sees itself as over the limit. Measured on a live instance: 40
# simultaneous attempts against one address were ALL allowed, and the counter
# finished at 15 — twenty-five increments silently overwritten.
#
# That matters more than it sounds. Guessing passwords is done in parallel, so
# the old code stopped the slow case and missed the fast one, while still
# reporting a limit of 20. A limiter that enforces a different number than the
# one it states is worse than none, because it is believed.
#
# `ON CONFLICT DO UPDATE` takes a row lock, so concurrent transactions queue on
# that row and each sees the previous COMMITTED value; `RETURNING` hands back
# the count for THIS request. Three things follow from it being one statement:
#   * the window roll happens WITH the increment, not as a second decision made
#     on data already read — the same race, somewhere nobody would look for it;
#   * two requests creating the same bucket at once no longer race the unique
#     index (one used to raise and fail open);
#   * the unique index on `bucket` stops being a nice-to-have and becomes the
#     thing correctness rests on, which is what its migration already claims.
_HIT_SQL = text("""
    INSERT INTO auth_throttle (id, bucket, window_start, attempts, created_at, updated_at)
    VALUES (:id, :bucket, :now, 1, :now, :now)
    ON CONFLICT (bucket) DO UPDATE SET
        attempts = CASE
            WHEN auth_throttle.window_start <= :cutoff THEN 1
            ELSE auth_throttle.attempts + 1
        END,
        window_start = CASE
            WHEN auth_throttle.window_start <= :cutoff THEN EXCLUDED.window_start
            ELSE auth_throttle.window_start
        END,
        updated_at = EXCLUDED.updated_at
    RETURNING attempts, window_start
""").columns(
    # ★★Declare the result types, or the answer depends on the driver.
    #
    # Raw SQL gets no result processors by default. asyncpg hands back a real
    # datetime, but SQLite hands back a STRING — so `now - window_start` raised
    # TypeError, the fail-open swallow turned that into "allowed", and the limit
    # silently never fired on that backend. A swallow that converts a type error
    # into a granted request is exactly how the last one of these survived.
    #
    # Naming the columns makes SQLAlchemy coerce on every backend, so the limit
    # behaves the same wherever it runs.
    sa_column("attempts", Integer),
    sa_column("window_start", DateTime),
)


async def _hit(db: AsyncSession, bucket: str, limit: int) -> Tuple[bool, int]:
    """Count one attempt against ``bucket``. Returns (allowed, retry_after)."""
    now = utcnow_naive()
    window = _window_seconds()
    cutoff = now - timedelta(seconds=window)
    try:
        # ★The id and timestamps are Python-side defaults on the base model, so
        # a raw INSERT has to supply them itself.
        attempts, window_start = (await db.execute(_HIT_SQL, {
            "id": str(uuid.uuid4()),
            "bucket": bucket,
            "now": now,
            "cutoff": cutoff,
        })).one()
        await db.commit()

        if attempts > limit:
            elapsed = (now - window_start).total_seconds()
            return False, max(1, int(window - elapsed))
        return True, 0
    except Exception as e:  # noqa: BLE001
        # ★Fails open — see the module docstring. Logged, because a throttle
        # that has quietly stopped counting should not be indistinguishable
        # from one that is working.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("auth throttle could not count %s: %s", bucket, e)
        return True, 0


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "too_many_attempts",
            "message": "Too many attempts. Please wait a moment and try again.",
        },
        headers={"Retry-After": str(retry_after)},
    )


async def _form_email(request: Request) -> Optional[str]:
    """The email on a sign-in form, if it can be read without disturbing it.

    ★Starlette caches the parsed form on the request, so reading it here does
    not consume the body the login route is about to read. Any failure returns
    None and the IP limit alone applies — a throttle must never be the reason a
    valid sign-in cannot be parsed.
    """
    try:
        form = await request.form()
        value = form.get("username") or form.get("email")
        return str(value).strip().lower()[:190] if value else None
    except Exception:  # noqa: BLE001
        return None


async def throttle_login(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> None:
    ip = client_ip(request)
    allowed, retry = await _hit(db, f"login:ip:{ip}", _login_per_ip())
    if not allowed:
        raise _too_many(retry)

    email = await _form_email(request)
    if email:
        allowed, retry = await _hit(db, f"login:email:{email}", _login_per_email())
        if not allowed:
            raise _too_many(retry)


async def throttle_register(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> None:
    ip = client_ip(request)
    allowed, retry = await _hit(db, f"register:ip:{ip}", REGISTER_PER_IP)
    if not allowed:
        raise _too_many(retry)


async def clear_login_throttle(db: AsyncSession, email: Optional[str]) -> None:
    """Forget an account's failed attempts once it signs in successfully.

    ★Only the EMAIL bucket is CLEARED. The address bucket is not — clearing it
    is how an attacker with one valid account would reset the limit at will.
    It is instead refunded by exactly one, in ``refund_login_ip``.
    """
    if not email:
        return
    try:
        row = (await db.execute(
            select(AuthThrottle).where(AuthThrottle.bucket == f"login:email:{email.strip().lower()[:190]}")
        )).scalars().first()
        if row is not None:
            await db.delete(row)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("could not clear the login throttle for %s: %s", email, e)


# ★★★Give the address its increment back when the sign-in SUCCEEDED.
#
# The address limit is charged in `throttle_login`, which runs as a dependency —
# before anyone knows whether the password was right. So an honest arrival cost
# the same as a guess, and a shared source address turned that into an outage:
# measured on this product, 200 directory users signing in correctly through one
# office address got 20 in and 180 refused, for five minutes at a time. Every
# office, VPN concentrator and Citrix farm presents one address for everybody.
#
# Refunding on success leaves the cap counting failures, which is what it was
# always for. What it deliberately does NOT do is CLEAR the bucket: an attacker
# holding one valid account would then wipe their own guesses at will. One
# success returns one attempt, so the arithmetic is exactly break-even and there
# is no way to profit from it.
#
# ★Guarded on `attempts > 0` and on the window, in one statement. Without the
# window check a success arriving after the window rolled would decrement a
# fresh bucket that owes it nothing, letting a slow attacker bank credit.
_REFUND_SQL = text("""
    UPDATE auth_throttle
       SET attempts = attempts - 1,
           updated_at = :now
     WHERE bucket = :bucket
       AND attempts > 0
       AND window_start > :cutoff
    RETURNING attempts
""").columns(sa_column("attempts", Integer))


async def refund_login_ip(db: AsyncSession, request: Optional[Request]) -> None:
    """Hand back the one attempt a successful sign-in charged to its address.

    Swallowed and logged, like the rest of this module: bookkeeping must never
    be the reason a valid sign-in fails. Failing to refund is safe in the
    direction that matters — it can only make the limiter stricter.
    """
    if request is None:
        return
    try:
        bucket = f"login:ip:{client_ip(request)}"
        now = utcnow_naive()
        cutoff = now - timedelta(seconds=_window_seconds())
        await db.execute(_REFUND_SQL, {"bucket": bucket, "now": now, "cutoff": cutoff})
        await db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("could not refund the login throttle for an address: %s", e)
