"""Microsoft sign-in errors, said in a way a member can act on.

Two things are pinned here. The obvious one is that known codes translate. The
one that actually matters is that the RAW blob never reaches the screen: it
carries the tenant name, a trace id and a correlation id, none of which belong
in a browser and none of which tell anybody what to do next.
"""
import pytest

from app.services import microsoft_error_text as mx


# A real-shaped Microsoft error, including everything that must not be shown.
RAW_700016 = (
    "AADSTS700016: Application with identifier '1950a258-227b-4e31-a9cf-717495945fc2' "
    "was not found in the directory 'citymart.onmicrosoft.com'. This can happen if the "
    "application has not been installed by the administrator of the tenant or consented "
    "to by any user in the tenant. Trace ID: 3f2c9a1e-77bd-4c5e-9a0c-5b1d2e8f0a11 "
    "Correlation ID: 8b1e77a2-2d4c-4f9b-9a3e-1c2b3d4e5f60 "
    "Timestamp: 2026-07-28 06:11:02Z"
)

RAW_50126 = "AADSTS50126: Error validating credentials due to invalid username or password."


# ---------------------------------------------------------------------------
# 1. Nothing from Microsoft's blob reaches the member
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("leak", [
    "1950a258-227b-4e31-a9cf-717495945fc2",   # client id
    "citymart.onmicrosoft.com",               # tenant name
    "3f2c9a1e-77bd-4c5e-9a0c-5b1d2e8f0a11",   # trace id
    "8b1e77a2-2d4c-4f9b-9a3e-1c2b3d4e5f60",   # correlation id
    "Timestamp",
])
def test_the_raw_blob_never_reaches_the_screen(leak):
    out = mx.humanize_sentence(RAW_700016)
    assert leak not in out


def test_the_code_itself_is_kept_for_support():
    """Stripping the code entirely would make a real problem untraceable."""
    assert "AADSTS700016" in mx.humanize_sentence(RAW_700016)


# ---------------------------------------------------------------------------
# 2. Known codes say what happened AND what to do
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expect", [
    (RAW_50126, "not accepted"),
    ("AADSTS50055: The password is expired.", "expired"),
    ("AADSTS50053: account is locked", "locked"),
    ("AADSTS7000218: request body must contain client_assertion", "does not allow"),
    ("AADSTS700016: not found in the directory", "not enabled"),
    ("AADSTS50173: fresh auth token required", "no longer valid"),
])
def test_known_codes_translate(raw, expect):
    h = mx.humanize(raw)
    assert expect in h["message"].lower()
    assert h["action"], "every message must carry a next step"


def test_every_mapped_entry_has_both_halves():
    """A message without an action is a dead end. There should be none."""
    for code, entry in mx._MESSAGES.items():
        assert entry.get("message"), code
        assert entry.get("action"), code
        assert entry["message"].endswith("."), code


def test_no_message_mentions_a_code_or_jargon():
    """These are read by somebody who wanted last month's sales."""
    banned = ("aadsts", "oauth", "ropc", "tenant id", "client_id", "grant_type", "token endpoint")
    for code, entry in mx._MESSAGES.items():
        blob = (entry["message"] + " " + entry["action"]).lower()
        for word in banned:
            assert word not in blob, f"{code} says '{word}'"


# ---------------------------------------------------------------------------
# 3. The cases with no code at all
# ---------------------------------------------------------------------------
def test_a_transport_failure_is_not_blamed_on_microsoft_admin():
    """★Every SDK stringifies a dead endpoint to 'Connection error.' with no
    code. Telling somebody to ask their Microsoft administrator about a dropped
    connection wastes two people's time."""
    h = mx.humanize("Connection error.")
    assert "reach Microsoft" in h["message"]
    assert "connection" in h["action"].lower()
    assert h["code"] is None


def test_an_unknown_code_still_gives_a_next_step():
    h = mx.humanize("AADSTS999999: something nobody has mapped yet")
    assert h["message"]
    assert h["action"]
    assert h["code"] == "AADSTS999999"


def test_empty_input_does_not_crash_or_return_nothing():
    for raw in (None, "", "   "):
        h = mx.humanize(raw)
        assert h["message"]
        assert h["action"]


def test_a_sentence_with_no_code_does_not_end_in_none():
    """A bare 'None' appended to an otherwise clear sentence helps nobody."""
    out = mx.humanize_sentence("Connection error.")
    assert "None" not in out
    assert not out.rstrip().endswith("()")


# ---------------------------------------------------------------------------
# 4. The Power BI licence case — the most common refusal
# ---------------------------------------------------------------------------
def test_not_licensed_names_the_licence_and_the_way_around_it():
    h = mx.humanize("User is not licensed for Power BI.")
    assert "not licensed" in h["message"].lower()
    assert "pro" in h["action"].lower()
    # The alternative matters: Fabric lakehouses need no such licence, and a
    # member who is told only "no" will stop there.
    assert "fabric" in h["action"].lower()


def test_licence_detection_is_not_precedence_dependent():
    """`A or B and C` parses as `A or (B and C)`. Both spellings must work."""
    assert "not licensed" in mx.humanize("User is not licensed")["message"].lower()
    assert "not licensed" in mx.humanize("powerbi license missing")["message"].lower()


# ---------------------------------------------------------------------------
# 5. Code extraction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,code", [
    (RAW_50126, "50126"),
    ("prefix AADSTS7000218 suffix", "7000218"),
    ("no code here", None),
    (None, None),
])
def test_extract_code(raw, code):
    assert mx.extract_code(raw) == code
