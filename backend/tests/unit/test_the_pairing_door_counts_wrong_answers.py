"""The public pairing endpoint has an attempt limit, and it behaves like one.

``POST /local-runtime/pair/claim`` is unauthenticated by design and hands the
caller a long-lived runtime token. Our sign-in form — the same shape of door,
guarding less — has been throttled since ``login_throttle`` was written. This
one had no limit and no delay at all, so a guesser was bounded only by network
speed.

★★★**Why these are behavioural and not a grep.** A test that searches the route
for the word "throttle" passes the moment the import exists and keeps passing
if the call is later moved above the flag check, given the wrong bucket, or
charged on success so that honest users are locked out. Every test here fires
real requests at the real handler and reads the real status code.

★These need a database (the counter is a table — deliberately, because four
uvicorn workers cannot share a Python integer), so this file cannot live in
``tests/unit/fork``, whose conftest makes ``run_migrations`` a no-op. The
entropy half of the fix is schema-free and lives there, in
``test_a_pairing_code_is_worth_guessing_at.py``.
"""
import hashlib
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.core import login_throttle as lt
from app.core.pairing_codes import generate_pair_code, normalize_pair_code
from app.dependencies import async_session_maker
from app.models.auth_throttle import AuthThrottle
from app.models.local_runtime import LocalRuntime
from app.routes.local_runtime import ClaimBody, pair_claim

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _feature_on(monkeypatch):
    """The endpoint 403s when the build has the flag off, which would make every
    assertion below pass for the wrong reason."""
    from app.settings.config import settings
    monkeypatch.setattr(settings, "hybrid_local_runtime", True, raising=False)


@pytest.fixture
def limit_of_three(monkeypatch):
    """Three, so the exhaustion tests are short and the boundary is visible.

    ★Set through the environment rather than by patching the function, so the
    path under test is the one production uses — including the reading of the
    setting. A test that patches ``_pair_claim_per_ip`` would still pass if the
    env plumbing were broken.
    """
    monkeypatch.setenv("DASH_PAIR_CLAIM_RATE_LIMIT_PER_IP", "3")
    return 3


def _request(ip: str, forwarded: str | None = None) -> Request:
    """A real Starlette request, not a stub — ``source_ip`` reads both the
    header list and the socket peer, and a stub is free to be wrong about the
    shape of either."""
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/local-runtime/pair/claim",
        "headers": headers,
        "client": (ip, 44321),
        "scheme": "http",
        "server": ("testserver", 80),
        "query_string": b"",
    })


def _fresh_ip() -> str:
    """One address per test. Buckets are shared state in a real table, so tests
    reusing an address would pass or fail depending on their order."""
    return f"198.51.100.{uuid.uuid4().int % 250 + 1}.{uuid.uuid4().hex[:8]}"


async def _pending_code(*, expired: bool = False) -> str:
    """Mint a pending pairing the way ``pair/start`` does, return the plaintext."""
    code = generate_pair_code()
    async with async_session_maker() as db:
        db.add(LocalRuntime(
            organization_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status="pending",
            pair_code_hash=hashlib.sha256(normalize_pair_code(code).encode()).hexdigest(),
            pair_expires_at=datetime.utcnow() + (
                timedelta(minutes=-1) if expired else timedelta(minutes=10)
            ),
        ))
        await db.commit()
    return code


async def _claim(code: str, request: Request):
    """Call the endpoint, returning the HTTPException instead of raising it so
    each test can assert on the status code it actually got."""
    async with async_session_maker() as db:
        try:
            return await pair_claim(ClaimBody(code=code), request, db)
        except HTTPException as exc:
            return exc


async def _attempts(bucket: str):
    async with async_session_maker() as db:
        return (await db.execute(
            select(AuthThrottle.attempts).where(AuthThrottle.bucket == bucket)
        )).scalar()


# --------------------------------------------------------------------------- #
#  The legitimate user must still get through
# --------------------------------------------------------------------------- #


async def test_the_right_code_on_the_first_try_still_pairs():
    """★The test that stops this fix from being a lockout.

    Everything else here is about refusing people. If this one ever goes red
    the feature is broken for everybody, and a throttle that achieves that has
    not secured the endpoint, it has removed it.
    """
    code = await _pending_code()
    result = await _claim(code, _request(_fresh_ip()))
    assert not isinstance(result, HTTPException), getattr(result, "detail", result)
    assert result["token"]
    assert result["runtime_id"]


async def test_a_person_who_mistypes_and_then_gets_it_right_still_pairs(limit_of_three):
    """★★★The boundary, and it is a whole attempt tighter than it reads.

    A budget of N is N *attempts*, not N failures followed by a free retry —
    the charge is made before the outcome is known, so the Nth call is the last
    one answered at all. With a budget of three that means two mistypes and
    then the correct code, and this test was written the other way round the
    first time and went red. Worth pinning explicitly: the number a person can
    get wrong is the limit MINUS ONE, so the default of ten buys nine mistakes,
    not ten.

    ★It is also why the default is ten rather than three. Three would be a
    genuine lockout risk for someone typing an eight-character code by hand off
    a second screen.
    """
    code = await _pending_code()
    ip = _fresh_ip()
    for _ in range(2):
        wrong = await _claim("ZZZZ-ZZZZ", _request(ip))
        assert isinstance(wrong, HTTPException) and wrong.status_code == 400

    result = await _claim(code, _request(ip))
    assert not isinstance(result, HTTPException), getattr(result, "detail", result)
    assert result["token"]


async def test_the_last_attempt_in_the_budget_is_answered_not_refused(limit_of_three):
    """The off-by-one, from the other side. A budget of three must answer three
    guesses on their merits and refuse only the fourth — a limiter that refuses
    the third is quietly stricter than the number it states, and one that
    refuses the fifth is quietly looser."""
    ip = _fresh_ip()
    got = [
        (await _claim("ZZZZ-ZZZZ", _request(ip))).status_code
        for _ in range(4)
    ]
    assert got == [400, 400, 400, 429], got


async def test_a_claim_that_succeeds_costs_the_address_nothing():
    """★The refund, measured at the counter rather than inferred.

    The charge is made before the outcome is known — it has to be, or parallel
    guesses race past a peek. Refunding on success is what keeps the cap
    counting failures, so an office where forty people pair their laptops
    through one address does not exhaust it on forty correct codes.
    """
    ip = _fresh_ip()
    bucket = f"pair_claim:ip:{ip}"
    code = await _pending_code()

    result = await _claim(code, _request(ip))
    assert not isinstance(result, HTTPException)
    assert await _attempts(bucket) == 0, "a correct claim spent part of the budget"


async def test_minting_a_fresh_code_is_not_an_attempt(limit_of_three):
    """A user who has burned their budget is not stuck.

    ``pair/start`` is session-authenticated and charges nothing, so the way out
    of a 429 is to generate a new code and wait out the window — not to contact
    an administrator.
    """
    ip = _fresh_ip()
    for _ in range(4):
        await _claim("ZZZZ-ZZZZ", _request(ip))
    assert isinstance(await _claim("ZZZZ-ZZZZ", _request(ip)), HTTPException)

    # A different machine, which is what "install the helper somewhere else"
    # looks like, is unaffected by the first one's failures.
    code = await _pending_code()
    result = await _claim(code, _request(_fresh_ip()))
    assert not isinstance(result, HTTPException), getattr(result, "detail", result)


# --------------------------------------------------------------------------- #
#  The guesser must not
# --------------------------------------------------------------------------- #


async def test_after_the_budget_is_spent_the_door_answers_differently(limit_of_three):
    """★★★The core behavioural assertion: N wrong answers, then a different
    refusal.

    Before the fix every one of these returned 400 and the loop could have run
    to sixty thousand. The status code changing from 400 to 429 is the whole
    point — it is the first evidence in the sequence that anything is counting.
    """
    ip = _fresh_ip()
    codes = [400, 400, 400, 429, 429, 429]
    got = []
    for _ in codes:
        result = await _claim("ZZZZ-ZZZZ", _request(ip))
        assert isinstance(result, HTTPException)
        got.append(result.status_code)
    assert got == codes, got


async def test_the_refusal_says_how_long_to_wait(limit_of_three):
    """A 429 with no ``Retry-After`` tells an honest helper to retry
    immediately, which turns a limit into a hot loop."""
    ip = _fresh_ip()
    for _ in range(4):
        result = await _claim("ZZZZ-ZZZZ", _request(ip))
    assert result.status_code == 429
    assert int(result.headers["Retry-After"]) > 0


async def test_the_wall_stands_whether_or_not_the_guess_was_a_real_code(limit_of_three):
    """★The limit must not be an oracle either.

    If a correct-but-throttled code were let through, or answered differently
    from a wrong one, the 429 would leak which guesses were real. Once the
    budget is spent, a genuine unexpired code gets the same refusal as
    nonsense.
    """
    ip = _fresh_ip()
    for _ in range(4):
        await _claim("ZZZZ-ZZZZ", _request(ip))

    real = await _pending_code()
    throttled = await _claim(real, _request(ip))
    assert isinstance(throttled, HTTPException)
    assert throttled.status_code == 429

    # And the code was not consumed by the refused attempt — it still pairs
    # from an address that has budget left.
    ok = await _claim(real, _request(_fresh_ip()))
    assert not isinstance(ok, HTTPException), getattr(ok, "detail", ok)


async def test_a_forwarded_header_cannot_buy_a_fresh_bucket(limit_of_three):
    """★★★The spoofing test, and the reason ``source_ip`` counts from the right.

    The bundled Caddyfile proxies with a plain ``reverse_proxy`` and no
    ``header_up`` override, so Caddy APPENDS the peer address to whatever
    ``X-Forwarded-For`` the caller sent. Reading the left-most entry — which is
    what ``client_ip`` does, and what the sign-in limiter still does — hands
    the bucket key to the attacker: a new header string per request is a new
    bucket per request, free.

    Here the socket peer is constant and the forged prefix changes every time.
    A limiter reading the left-most entry lets all six through.
    """
    ip = _fresh_ip()
    got = []
    for i in range(6):
        forged = f"10.0.0.{i}, {ip}"
        result = await _claim("ZZZZ-ZZZZ", _request(ip, forwarded=forged))
        assert isinstance(result, HTTPException)
        got.append(result.status_code)
    assert got == [400, 400, 400, 429, 429, 429], got


async def test_two_addresses_do_not_share_one_budget(limit_of_three):
    """The counterpart to the test above. Keying too coarsely — on nothing, or
    on a proxy's own address — would let one guesser lock every other user of
    the product out of pairing, which is a denial of service delivered by the
    security feature."""
    burned = _fresh_ip()
    for _ in range(4):
        await _claim("ZZZZ-ZZZZ", _request(burned))
    assert (await _claim("ZZZZ-ZZZZ", _request(burned))).status_code == 429

    other = await _claim("ZZZZ-ZZZZ", _request(_fresh_ip()))
    assert other.status_code == 400, "one address's guessing blocked another's"


# --------------------------------------------------------------------------- #
#  Behaviour that existed before and must survive the fix
# --------------------------------------------------------------------------- #


async def test_wrong_and_expired_are_answered_by_the_same_sentence():
    """★★★Not an oracle.

    Telling a guesser that a code was real but expired confirms a hit and
    collapses the search. Both branches return one 400 and one string, and
    they are compared here rather than merely eyeballed in the route.
    """
    expired = await _pending_code(expired=True)
    stale = await _claim(expired, _request(_fresh_ip()))
    unknown = await _claim("ZZZZ-ZZZZ", _request(_fresh_ip()))

    assert stale.status_code == unknown.status_code == 400
    assert stale.detail == unknown.detail


async def test_a_code_can_only_be_claimed_once():
    """Single-use. A replayed claim finds no pending row and gets the ordinary
    refusal."""
    code = await _pending_code()
    first = await _claim(code, _request(_fresh_ip()))
    assert not isinstance(first, HTTPException)

    second = await _claim(code, _request(_fresh_ip()))
    assert isinstance(second, HTTPException) and second.status_code == 400


async def test_the_plaintext_code_is_never_stored():
    """Hashed at rest, before and after. A pairing row holds a digest and, once
    claimed, not even that."""
    code = await _pending_code()
    normalized = normalize_pair_code(code)
    async with async_session_maker() as db:
        row = (await db.execute(
            select(LocalRuntime).where(
                LocalRuntime.pair_code_hash
                == hashlib.sha256(normalized.encode()).hexdigest()
            )
        )).scalars().first()
    assert row is not None
    assert row.pair_code_hash != code and row.pair_code_hash != normalized

    await _claim(code, _request(_fresh_ip()))
    async with async_session_maker() as db:
        claimed = (await db.execute(
            select(LocalRuntime).where(LocalRuntime.id == row.id)
        )).scalars().first()
    assert claimed.status == "paired"
    assert claimed.pair_code_hash is None
    assert claimed.pair_expires_at is None
    assert claimed.token_hash and len(claimed.token_hash) == 64


async def test_an_empty_body_is_refused_without_a_lookup():
    """``normalize_pair_code`` returns "" for a missing code, and the route
    must not hash that and compare it against real rows."""
    for empty in ("", "   ", "----"):
        result = await _claim(empty, _request(_fresh_ip()))
        assert isinstance(result, HTTPException) and result.status_code == 400


# --------------------------------------------------------------------------- #
#  The limit itself
# --------------------------------------------------------------------------- #


async def test_the_default_budget_is_generous_enough_to_mistype_and_small_enough_to_matter():
    """★Stated in one place so the number in the report and the number enforced
    cannot drift apart.

    Ten wrong answers per window is far past clumsy for a code that is usually
    pasted, and against a space of 30**8 it buys roughly 1.5e-11 of a chance.
    """
    from app.core.pairing_codes import pair_code_search_space
    limit = lt._pair_claim_per_ip()
    assert 5 <= limit <= 20, limit
    assert limit / pair_code_search_space() < 1e-9


async def test_the_counter_is_a_table_not_a_number_in_this_process(limit_of_three):
    """★★★The limiter has to be visible to all four uvicorn workers.

    A module-level integer would make a stated limit of three an actual limit
    of twelve. This codebase has shipped that mistake twice. Asserting the row
    exists is what distinguishes a real limiter from one that only works
    single-worker — which is exactly how this test suite would otherwise run.
    """
    ip = _fresh_ip()
    await _claim("ZZZZ-ZZZZ", _request(ip))
    assert await _attempts(f"pair_claim:ip:{ip}") == 1
