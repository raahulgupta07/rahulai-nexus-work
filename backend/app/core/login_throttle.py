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


def _pair_claim_per_ip() -> int:
    """One address guessing local-runtime pairing codes.

    ★Counts FAILURES: a claim that succeeds hands its increment straight back
    (``refund_pair_claim``), exactly as a successful sign-in does. So the
    person who pastes their code correctly on the first try never touches this
    at all, and the budget belongs entirely to people who got it wrong.

    ★**Ten, and lower than the sign-in cap on purpose.** A password is typed
    from memory and mistyped often; a pairing code is read off the screen next
    to the helper's dialog and is far more often pasted than typed. Ten wrong
    attempts inside the window is well past clumsy and well short of useful:
    against the 30-symbol eight-character code it buys 1.5e-11 of a chance.
    Nobody is locked out either way — the user can mint a fresh code from the
    settings page, and doing so is not a claim attempt, so it is not charged.

    ★Read through a function, and through the environment rather than
    ``settings``, so a deployment can raise it without this module having
    captured the old value at import. It is not in ``settings/config.py``
    because that file was owned by another change in flight; moving it there
    is a tidy-up, not a behaviour change.
    """
    return _env_int("PAIR_CLAIM_RATE_LIMIT_PER_IP", 10)


def _trusted_proxy_hops() -> int:
    """How many reverse proxies sit in front of this app. See ``source_ip``."""
    return max(0, _env_int("TRUSTED_PROXY_HOPS", 1))


def _env_int(name: str, default: int) -> int:
    """``DASH_<name>``, falling back to ``<name>`` and then the default.

    ★``env_compat.normalize_environment()`` mirrors ``DASH_``/``BOW_`` both
    ways at start-up, so the prefixed spelling is the one to document.
    """
    import os
    for key in (f"DASH_{name}", name):
        raw = os.environ.get(key)
        if raw is not None and str(raw).strip():
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                pass
    return default


def _register_per_ip() -> int:
    """Account creation is rarer and more expensive than a sign-in attempt, so
    this stays well below the sign-in cap.

    ★It is a SETTING for the same reason the three above are: it was a hardcoded
    5, and one office behind one address could create five accounts ever. Read
    through a function so a deployment can change it without this module having
    captured the old value at import.
    """
    from app.settings.config import settings
    return settings.register_rate_limit_per_ip


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


def source_ip(request: Request) -> str:
    """The caller's address, taken from the end of the chain a proxy APPENDS to.

    ★★★``client_ip`` above reads the LEFT-MOST ``X-Forwarded-For`` entry, and
    under this deployment that entry is written by the attacker. The bundled
    ``Caddyfile`` proxies to ``app:3000`` with a plain ``reverse_proxy`` and no
    ``header_up X-Forwarded-For`` override, and Caddy's documented default is
    to APPEND the peer address to whatever the client already sent. So a
    request carrying ``X-Forwarded-For: 1.2.3.4`` arrives as
    ``1.2.3.4, <real address>`` and the left-most read hands back a value the
    caller chose. A limiter keyed on that is not a limiter: a fresh header
    string per request is a fresh bucket per request, at zero cost and without
    even renting an address.

    Counting from the RIGHT inverts that. An attacker can prepend entries; it
    cannot remove the one the last proxy appended, because that append happens
    after the request leaves them. With ``DASH_TRUSTED_PROXY_HOPS`` hops of
    proxy in front (default 1, matching the Caddyfile), the entry that hop
    wrote is at ``-hops``.

    ★Getting the hop count too LOW is safe and getting it too high is not:
    too low lands on a proxy's own address, which pools users into one shared
    bucket — stricter than intended, never spoofable. Too high walks back into
    the caller-supplied prefix. So a short or absent header falls back to the
    socket peer rather than reaching for the left-most entry.

    ★``client_ip`` is deliberately left as it was. It is what the sign-in and
    registration limits have always used, and changing the address every
    existing bucket is keyed on is a separate decision from adding a limit to
    an endpoint that had none. That the same weakness applies there is written
    up rather than silently changed here.
    """
    hops = _trusted_proxy_hops()
    fwd = request.headers.get("x-forwarded-for")
    if hops and fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops][:100]
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
    allowed, retry = await _hit(db, f"register:ip:{ip}", _register_per_ip())
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


async def _refund(db: AsyncSession, bucket: str) -> None:
    """Return exactly one attempt to ``bucket``.

    Swallowed and logged, like the rest of this module: bookkeeping must never
    be the reason a valid request fails. Failing to refund is safe in the
    direction that matters — it can only make the limiter stricter.
    """
    try:
        now = utcnow_naive()
        cutoff = now - timedelta(seconds=_window_seconds())
        await db.execute(_REFUND_SQL, {"bucket": bucket, "now": now, "cutoff": cutoff})
        await db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("could not refund the throttle bucket %s: %s", bucket, e)


async def refund_login_ip(db: AsyncSession, request: Optional[Request]) -> None:
    """Hand back the one attempt a successful sign-in charged to its address."""
    if request is None:
        return
    await _refund(db, f"login:ip:{client_ip(request)}")


# --------------------------------------------------------------------------- #
#  Local-runtime pairing
# --------------------------------------------------------------------------- #
#
# ★★★The third unauthenticated door, and it had no lock at all.
#
# `POST /local-runtime/pair/claim` is public for the same reason the sign-in
# form is: the caller has no session yet, and a short secret is the whole
# proof. Sign-in was throttled and this was not — the same shape of endpoint,
# guarding a larger prize (a long-lived runtime token, and with it the file
# reads and folder listings meant for that person's machine), left open.
#
# The counting is the proven one above, unchanged: one atomic statement, one
# row per bucket, fails open, visible to all four uvicorn workers. What is new
# is only which bucket, which limit, and which address — see `source_ip`.


async def throttle_pair_claim(db: AsyncSession, request: Optional[Request]) -> None:
    """Charge one pairing-claim attempt to the caller's address; 429 when over.

    ★★★**Charged BEFORE the outcome is known, refunded when it succeeds** —
    the pattern ``refund_login_ip`` already established, and it is a
    concurrency decision rather than a stylistic one. The obvious alternative
    is to peek at the counter, do the lookup, and charge only on failure; that
    is a read-then-write across two statements, so a burst of parallel guesses
    all pass the peek before any of them records anything. This module has the
    measured version of that mistake in the comment above ``_HIT_SQL``: 40
    simultaneous attempts, all allowed, counter finishing at 15. Guessing is
    done in parallel, so the racy form stops precisely the attacker who was
    never a threat.

    Charging first and refunding on success is one atomic statement in each
    direction, and the arithmetic is break-even: an honest first-try claim
    costs nothing, and there is no way to profit from a refund you can only
    earn by already holding a valid code.
    """
    if request is None:
        return
    allowed, retry = await _hit(
        db, f"pair_claim:ip:{source_ip(request)}", _pair_claim_per_ip()
    )
    if not allowed:
        raise _too_many(retry)


async def refund_pair_claim(db: AsyncSession, request: Optional[Request]) -> None:
    """Give the address its attempt back once a claim actually succeeded."""
    if request is None:
        return
    await _refund(db, f"pair_claim:ip:{source_ip(request)}")
