"""Two things wrong with the local sign-in form, one of them silent for months.

  1. Every write to `last_login` and `last_seen` sent a timezone-AWARE datetime
     into a plain `DateTime` column. asyncpg does not coerce that, it raises
     DataError — and all three writes were wrapped in a bare swallow, so the
     product simply had no record of anyone ever signing in. Proven cold on the
     live database: the administrator's `last_login` read NULL after many
     sign-ins, an aware write raised, a naive write committed.

  2. Sign-in and registration had no rate limit of any kind. Passwords could be
     guessed against a known address as fast as the network allowed. That
     matters more now, not less: the product admits people automatically
     through single sign-on and a directory, which makes the password form the
     softest remaining target rather than the only one.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AUTH = REPO / "backend" / "app" / "core" / "auth.py"
PROVIDERS = REPO / "backend" / "app" / "services" / "auth_providers.py"
STAMPS = REPO / "backend" / "app" / "core" / "timestamps.py"
THROTTLE = REPO / "backend" / "app" / "core" / "login_throttle.py"
MODEL = REPO / "backend" / "app" / "models" / "auth_throttle.py"
MAIN = REPO / "backend" / "main.py"
MIGRATION = REPO / "backend" / "alembic" / "versions" / "ca11auththrottle_login_rate_limit.py"
USER = REPO / "backend" / "app" / "models" / "user.py"


def _fn(src: str, header: str, indent: str = "    ") -> str:
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(rf"\n{indent}(?:async def |def |[A-Z_]+ = )", rest)
    return rest[: nxt.start()] if nxt else rest


# ---------------------------------------------------------------------------
# Naive columns take naive values
# ---------------------------------------------------------------------------
def test_the_columns_really_are_naive():
    """★The premise. If these ever become `DateTime(timezone=True)` the fix
    below is wrong in the other direction, and this test says so first."""
    src = USER.read_text(encoding="utf-8")
    for col in ("last_login", "last_seen"):
        line = [l for l in src.splitlines() if l.strip().startswith(f"{col} =")]
        assert len(line) == 1, line
        assert "Column(DateTime," in line[0], line[0]
        assert "timezone=True" not in line[0], line[0]


def test_no_login_write_sends_an_aware_datetime():
    """★★★asyncpg raises DataError for an aware value into a naive column. It
    is not a warning and not a coercion — the statement fails.

    ★Resolves one level of indirection. The first version demanded the call be
    on the write line itself and failed on correct code: `auth_providers` binds
    `now = utcnow_naive()` a line earlier. What matters is what the value IS,
    not where it was spelled.
    """
    for f in (AUTH, PROVIDERS):
        src = f.read_text(encoding="utf-8")
        lines = src.splitlines()
        found = 0
        for idx, line in enumerate(lines):
            code = line.split("#", 1)[0]
            # ★The capture must not swallow the enclosing `values(...)` paren:
            # a greedy `[\w.()]*` matched `utcnow_naive())` and then failed
            # to resolve it as a name.
            m = re.search(r"last_(?:login|seen)=([A-Za-z_][\w.]*(?:\(\))?)", code)
            if not m:
                continue
            found += 1
            assert "datetime.now(timezone.utc)" not in code, (
                f"{f.name}:{idx + 1} {code.strip()} — this write raises and is swallowed"
            )
            value = m.group(1)
            if value == "utcnow_naive()":
                continue
            # A bare name — find where it was bound, searching upwards.
            binding = None
            for prev in reversed(lines[max(0, idx - 40):idx]):
                b = re.match(rf"\s*{re.escape(value)}\s*=\s*(.+)$", prev.split("#", 1)[0])
                if b:
                    binding = b.group(1).strip()
                    break
            assert binding is not None, f"{f.name}:{idx + 1} cannot resolve {value!r}"
            assert "utcnow_naive()" in binding, (
                f"{f.name}:{idx + 1} writes {value!r}, bound to {binding!r}"
            )
        assert found, f"{f.name}: no login timestamp write found — did it move?"


def test_all_three_writers_are_covered():
    """★Two files, three writes. A fix applied to the one that was noticed
    would have left the other two failing exactly as before."""
    a = AUTH.read_text(encoding="utf-8")
    p = PROVIDERS.read_text(encoding="utf-8")
    assert "last_login=utcnow_naive()" in a
    assert "last_seen=utcnow_naive()" in a
    assert "now = utcnow_naive()" in p


def test_the_conversion_lives_in_one_place():
    src = STAMPS.read_text(encoding="utf-8")
    assert "def utcnow_naive()" in src
    assert "def as_utc(" in src
    assert "replace(tzinfo=None)" in src


def test_reading_a_stored_value_back_goes_through_the_same_module():
    """★The debounce compares a stored value against an aware `now`. Subtracting
    a naive from an aware datetime raises TypeError, which inside a swallow
    looks like the comparison merely never being true."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "async def _update_last_seen(", indent="")
    assert "as_utc(user.last_seen)" in body
    assert "user.last_seen.replace(tzinfo=timezone.utc)" not in body


def test_the_swallows_now_say_something():
    """★A swallow with no log is how this survived from the day it shipped. The
    write must still not fail a login — but it must leave a trace."""
    a = AUTH.read_text(encoding="utf-8")
    login = _fn(a, "    async def on_after_login(")
    assert "Could not record last_login" in login
    seen = _fn(a, "async def _update_last_seen(", indent="")
    assert "Could not record last_seen" in seen


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_both_open_doors_are_limited():
    src = MAIN.read_text(encoding="utf-8")
    auth_router = src[src.index("get_auth_router(auth_backend)"):]
    auth_router = auth_router[: auth_router.index(")\n\n")]
    assert "Depends(throttle_login)" in auth_router

    reg = src[src.index("get_register_router("):]
    reg = reg[: reg.index(")\n\n")]
    assert "Depends(throttle_register)" in reg


def test_the_counter_is_shared_between_workers():
    """★★★The app runs uvicorn with multiple workers. A module-level counter is
    invisible to the others, so a stated limit of 10 admits 40 — a limit that
    enforces a different number than the one it states is worse than none,
    because it is believed. This codebase has made that mistake twice already.

    ★Two things were wrong with the first version of this check, and a planted
    in-memory counter walked straight past both. It matched specific NAMES,
    which any rename defeats. And it tried to skip the module docstring by
    splitting on the triple-quote marker and taking the last piece — the file
    has ten of those markers, so that left it scanning the last ten lines. It
    was not looking at the code it claimed to police.

    So: scan every module-level binding, whatever it is called, and reject any
    mutable container. State that survives a request belongs in the database.
    """
    src = THROTTLE.read_text(encoding="utf-8")
    assert "AuthThrottle" in src and "AsyncSession" in src

    module_level = [
        l for l in src.splitlines()
        if l and not l[0].isspace() and not l.startswith("#")
    ]
    bindings = [
        l for l in module_level
        if re.match(r"^[A-Za-z_]\w*\s*(?::[^=]+)?=", l)
    ]
    # Guard the guard: if the scan finds no bindings at all it is looking at
    # nothing, and every assertion below passes vacuously.
    #
    # ★Was 4, which encoded the file's shape rather than the invariant. The
    # fourth binding was `REGISTER_PER_IP = 5`, and moving that hardcoded limit
    # into settings — the whole point of the change — dropped the count to 3.
    # Lowering it is not weakening the check: the check is "the scan sees real
    # module-level code", and a module that legitimately holds fewer constants
    # must not read as a regression. The assertion that matters is `mutable`
    # below, which is unaffected by how many immutable bindings exist.
    assert len(bindings) >= 3, bindings

    mutable = [
        l for l in bindings
        if re.search(r"=\s*(\{|\[|dict\(|list\(|set\(|defaultdict|Counter\()", l)
        or re.match(r"^[A-Za-z_]\w*\s*:\s*(dict|list|set|Dict|List|Set)\b", l)
    ]
    assert not mutable, f"in-process state has appeared: {mutable}"


def test_the_bucket_is_unique_or_the_workers_split_again():
    """★Without the unique index two workers racing the same bucket each insert
    their own row and each counts to the limit separately — reintroducing the
    per-worker split the table exists to remove."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "'ix_auth_throttle_bucket'" in src
    assert "unique=True" in src
    assert MODEL.read_text(encoding="utf-8").count("unique=True") >= 1


def test_it_counts_by_address_and_by_account():
    src = THROTTLE.read_text(encoding="utf-8")
    assert 'f"login:ip:{ip}"' in src
    assert 'f"login:email:{email}"' in src


# ★The limits moved out of login_throttle.py and into settings/config.py, so
# a deployment can raise them without a rebuild — the right number depends on
# how many people share one source address, which no default can know.
CONFIG = REPO / "backend" / "app" / "settings" / "config.py"


def _limit(name: str) -> int:
    """The packaged DEFAULT for a limit, read from config.py."""
    src = CONFIG.read_text(encoding="utf-8")
    m = re.search(r'_analytics_env_int\(\s*"' + name + r'",\s*(\d+)\s*\)', src)
    assert m, f"{name} is no longer declared in config.py"
    return int(m.group(1))


def test_the_limits_are_configurable_not_baked_in():
    """★A single office, VPN concentrator or Citrix farm presents ONE address
    for everybody behind it. No packaged default can be right for both that and
    a home connection, so the number has to be settable without a rebuild."""
    src = CONFIG.read_text(encoding="utf-8")
    for env in ("LOGIN_RATE_LIMIT_PER_IP", "LOGIN_RATE_LIMIT_PER_EMAIL",
                "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                # ★Registration was the one exception — a hardcoded 5 that no
                # deployment could raise, which is precisely the single-office
                # case this test exists to prevent.
                "REGISTER_RATE_LIMIT_PER_IP"):
        assert env in src, f"{env} cannot be set by a deployment"


def test_the_account_limit_cannot_be_used_to_lock_someone_out():
    """★★Counting only by email would let anyone deny a real user their own
    account by failing their sign-in on purpose. The per-address limit is the
    one that stops guessing; the per-account limit is a wide backstop."""
    assert _limit("LOGIN_RATE_LIMIT_PER_EMAIL") > _limit("LOGIN_RATE_LIMIT_PER_IP")


def test_registration_is_held_tighter_than_sign_in():
    # Reads the packaged default from config.py, where the registration limit
    # now lives alongside the sign-in ones rather than as a constant in the
    # throttle module.
    assert _limit("REGISTER_RATE_LIMIT_PER_IP") < _limit("LOGIN_RATE_LIMIT_PER_IP")


def test_the_limits_are_above_what_a_person_does():
    """A limit that catches someone retyping their own password is a support
    ticket, not a security control."""
    assert _limit("LOGIN_RATE_LIMIT_PER_IP") >= 10
    assert _limit("LOGIN_RATE_LIMIT_WINDOW_SECONDS") <= 900


def test_it_fails_open():
    """★A throttle that locks everyone out when the database hiccups has turned
    a degraded dependency into a total outage. Unlike an admission check, being
    unable to count is not evidence that anything is wrong."""
    body = _fn(THROTTLE.read_text(encoding="utf-8"), "async def _hit(", indent="")
    tail = body[body.index("except Exception"):]
    assert "return True, 0" in tail, "a counting failure denies the request"
    assert "log.warning" in tail, "a throttle that stopped counting must say so"


def test_reading_the_form_never_breaks_the_sign_in():
    """★Starlette caches the parsed form, so reading the email here does not
    consume the body the login route is about to read — but any failure must
    still degrade to the address limit rather than refuse."""
    body = _fn(THROTTLE.read_text(encoding="utf-8"), "async def _form_email(", indent="")
    assert "except Exception" in body and "return None" in body

    login = _fn(THROTTLE.read_text(encoding="utf-8"), "async def throttle_login(", indent="")
    assert "if email:" in login, "a missing email must not skip or fail the ip limit"
    assert login.index("login:ip:") < login.index("_form_email"), (
        "the address limit must apply before the body is touched at all"
    )


def test_the_window_rolls_instead_of_growing():
    """★One row per bucket, rolled forward — not one INSERT per attempt. A flood
    must not also be an unbounded write of rows exactly when the system is
    already under load.

    ★The roll now happens INSIDE the counting statement. It used to be a second
    decision made on a row that had already been read, which is the same lost
    update as the increment itself — just somewhere nobody would look for it.
    """
    src = THROTTLE.read_text(encoding="utf-8")
    assert "ON CONFLICT (bucket) DO UPDATE" in src
    assert "window_start = CASE" in src
    assert "auth_throttle.window_start <= :cutoff" in src


def test_the_count_is_incremented_by_the_DATABASE_not_by_python():
    """★★★The bug this replaced, found by firing attempts concurrently.

    Reading the counter, adding one in Python and writing it back is a lost
    update: attempts that arrive together all read the value BEFORE any of them
    commits, so each computes a small number and none sees itself as over the
    limit. Measured live: 40 simultaneous attempts against one address were all
    allowed, and the counter finished at 15.

    Guessing passwords is done in parallel, so that version stopped the slow
    case and missed the fast one while still advertising a limit of 20.

    This test exists because the behavioural tests could not see it — every one
    of them was sequential.
    """
    src = THROTTLE.read_text(encoding="utf-8")
    body = _fn(src, "async def _hit(", indent="")

    # the increment must be expressed in SQL
    assert "auth_throttle.attempts + 1" in src, "the increment left the database"
    assert "RETURNING attempts, window_start" in src, "the count must come back from the write"

    # ...and must NOT be computed in Python inside _hit
    for banned in ("row.attempts = (row.attempts or 0) + 1",
                   "row.attempts += 1",
                   "attempts = attempts + 1"):
        assert banned not in body, "the increment moved back into Python: %r" % banned

    # a read-then-decide pair inside _hit is the shape of the original bug
    assert "select(AuthThrottle)" not in body, (
        "_hit selects the row again — the decision must come from RETURNING"
    )


def test_the_unique_index_is_load_bearing_for_correctness():
    """★The upsert relies on it. Without the unique constraint on `bucket`,
    ON CONFLICT has nothing to conflict on and concurrent first-hits each insert
    their own counter row."""
    assert "unique=True" in MODEL.read_text(encoding="utf-8")
    src = THROTTLE.read_text(encoding="utf-8")
    assert "ON CONFLICT (bucket)" in src


def test_a_refusal_says_when_to_come_back():
    src = THROTTLE.read_text(encoding="utf-8")
    assert "status_code=429" in src
    assert '"Retry-After"' in src


def test_a_success_clears_the_account_but_not_the_address():
    """★★Otherwise an attacker holding one valid account could reset the
    address limit at will, simply by signing in to their own."""
    body = _fn(THROTTLE.read_text(encoding="utf-8"), "async def clear_login_throttle(", indent="")
    assert "login:email:" in body
    assert "login:ip:" not in body


def test_a_successful_login_actually_calls_it():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def on_after_login(")
    assert "clear_login_throttle(" in body


def test_the_migration_chains_onto_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "revision = 'ca11auththrottle'" in src
    assert "down_revision = 'ca10connsync01'" in src
    assert "insp.get_table_names()" in src, "re-running the migration must be safe"
