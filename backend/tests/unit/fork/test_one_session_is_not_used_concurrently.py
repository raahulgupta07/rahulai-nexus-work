"""A gather over builders that share one database session is a crash, not speed.

Every context builder is constructed with the SAME `AsyncSession` — the hub
passes `self.db` to each of them. A session owns one connection, so queries
issued on it cannot overlap. `asyncio.gather` over four such builders therefore
buys no parallelism at all; what it buys is

    sqlalchemy.exc.InvalidRequestError: This session is provisioning a new
    connection; concurrent operations are not permitted

Measured in production: 29 occurrences in one hour, every one of them losing
that turn's query context. The failure is caught and logged, so nothing
crashes visibly — the model simply answers without knowing which queries the
report has already run, which reads as the assistant being forgetful rather
than as an error.

★The fix is to run them in order. That gives up nothing, because a shared
session could never have run them at once. Running them genuinely in parallel
is possible, but the prerequisite is a session PER builder — the pattern
`report_activity_hub` already uses for its ticks — and not a gather over one.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
HUB = REPO / "backend" / "app" / "ai" / "context" / "context_hub.py"


def _strip_comments(text: str) -> str:
    """Blank out `#` comments, preserving line numbering.

    ★Not optional. The comment explaining WHY the gather was removed names
    `asyncio.gather` verbatim, so the first version of this guard failed
    against the corrected file, citing its own documentation. A guard that
    fires on prose gets muted, and a muted guard protects nothing.
    """
    out = []
    for line in text.splitlines():
        i = line.find("#")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


def _method(src: str, name: str) -> str:
    start = src.index(f"    async def {name}(")
    rest = src[start:]
    nxt = re.search(r"\n    (?:async )?def ", rest[10:])
    return rest[: nxt.start() + 10] if nxt else rest


def test_the_builders_really_do_share_one_session():
    """★The premise. If builders ever get their own sessions, a gather becomes
    correct again and this file should be revisited rather than obeyed."""
    src = HUB.read_text(encoding="utf-8")
    shared = len(re.findall(r"ContextBuilder\(\s*self\.db", src))
    assert shared >= 4, (
        "builders no longer take self.db at construction — re-read "
        "test_one_session_is_not_used_concurrently.py before trusting it"
    )


def test_warm_builders_are_not_gathered_over_a_shared_session():
    body = _method(_strip_comments(HUB.read_text(encoding="utf-8")), "refresh_warm")
    assert "asyncio.gather" not in body, (
        "refresh_warm gathers builders that share one AsyncSession; that "
        "cannot run in parallel and raises 'concurrent operations are not "
        "permitted'"
    )


def test_static_builders_are_not_gathered_over_a_shared_session():
    body = _method(_strip_comments(HUB.read_text(encoding="utf-8")), "prime_static")
    assert "asyncio.gather" not in body, (
        "prime_static has the same shape as the warm gather that failed in "
        "production"
    )


def test_one_failing_builder_still_leaves_the_rest():
    """★gather(return_exceptions=True) had this property and the replacement
    must keep it: a builder that raises costs its own section, not the turn."""
    for name in ("refresh_warm", "prime_static"):
        body = _method(HUB.read_text(encoding="utf-8"), name)
        assert "except Exception as exc" in body, (
            f"{name} no longer tolerates one builder failing"
        )
        assert ".append(exc)" in body, (
            f"{name} drops the exception instead of recording it in place, so "
            "the callers that check isinstance(x, Exception) will misread it"
        )
