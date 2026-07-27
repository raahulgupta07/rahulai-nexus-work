"""DEF-006 — a Power BI table pull came back 16% complete, and nothing said so.

Found by: a top-5-promotions question against a live tenant. The agent pulled the
promotion tables, aggregated them in pandas, and answered with a code, an amount and
a distinct count. Its arithmetic was perfect. Every number was wrong, because
`executeQueries` had handed it a prefix of the table:

    EVALUATE <8-column table>   true 300,086 rows -> returned  48,222
    EVALUATE <8-column table>   true  88,878 rows -> returned  56,930
    1-column projection         true 300,086 rows -> returned 100,000

Two independent caps, neither of them announced: a hard 100,000-row cap, and a
response-SIZE cap that bites earlier — at an unpredictable count — when rows are
wide. A partial response is byte-shaped exactly like a complete one; there is no
marker, no header, no warning. `_execute_dax_internal` took `tables[0]["rows"]` and
handed pandas a DataFrame, so the truncation became a plausible answer instead of an
error.

The row cap is detectable for free (`len(df) == cap`). The size cap has no free
signal at all — the only reliable detection is a COUNTROWS probe, which costs an
extra call against a rate-limited (~120/user/minute) budget. So the probe is scoped
to the ONE query shape that silently loses data: a bare `EVALUATE <Table>` pull.
Over-matching that shape is the expensive failure mode, which is why the scoping
tests below are as detailed as the detection tests.

Contract these tests pin:
  * hitting the row cap exactly raises; one row short of it does not
  * a bare table pull whose COUNTROWS probe exceeds the returned rows raises
  * the probe fires ONLY for a provably bare table pull — never for aggregation,
    filters, TOPN, SUMMARIZECOLUMNS, ROW(), or anything ambiguous
  * a probe that cannot answer (network, malformed, empty) leaves the query working
  * an explicit `max_rows` is a declared limit, not the defect — it must not raise
  * the flag can disable the guard, and the STRING "off" must not enable it by
    truthiness (this codebase has been bitten three times by exactly that)
  * the raised message names the real numbers and tells the model to aggregate in DAX
"""
import pandas as pd
import pytest

from app.data_sources.clients import powerbi_client as pbi_module
from app.data_sources.clients.powerbi_client import PowerBIClient

# The fix under test adds these names. They are imported defensively ONLY so that
# this suite FAILS on pre-fix code instead of erroring out at collection — a
# collection error proves nothing about behavior. Every test that cannot express
# itself without a new symbol is skipped explicitly rather than quietly padding the
# pass count; the behavioral tests below still run and still fail.
try:
    from app.data_sources.clients.powerbi_client import (
        POWERBI_EXECUTE_QUERIES_ROW_CAP,
        PowerBIResultTruncatedError,
        _bare_table_pull_target,
        _truncation_guard_enabled,
        _truncation_message,
    )

    FIX_PRESENT = True
except ImportError:  # pre-fix code path
    FIX_PRESENT = False
    POWERBI_EXECUTE_QUERIES_ROW_CAP = 100_000

    class PowerBIResultTruncatedError(RuntimeError):
        """Never raised pre-fix — that IS the defect."""

    _bare_table_pull_target = _truncation_guard_enabled = _truncation_message = None

needs_fix = pytest.mark.skipif(
    not FIX_PRESENT, reason="symbol does not exist pre-fix (see module docstring)"
)

PROBE_MARKER = getattr(pbi_module, "_ROW_COUNT_PROBE_COLUMN", "__bow_true_row_count")

WORKSPACE = "11111111-2222-3333-4444-555555555555"
DATASET = "66666666-7777-8888-9999-000000000000"
TABLE = "Widgets"


# --- fake executeQueries transport -------------------------------------------


class _Resp:
    """The shape `_request` returns: a requests.Response as this client uses it."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _rows(n, wide=False):
    """n result rows in the real DAX shape (`Table[Column]` keys)."""
    if wide:
        return [
            {f"{TABLE}[Col{c}]": f"v{i}-{c}" for c in range(8)} for i in range(n)
        ]
    return [{f"{TABLE}[Code]": f"C{i:06d}"} for i in range(n)]


def _envelope(rows):
    return {"results": [{"tables": [{"rows": rows}]}]}


_UNSET = object()


def _client(returned_rows, probe=_UNSET, probe_status=200, probe_raises=False):
    """A PowerBIClient whose HTTP layer is a fake executeQueries endpoint.

    `returned_rows` is what the main query gets back (already truncated, as the real
    API does silently). `probe` is the COUNTROWS answer: an int, None for a response
    the probe cannot read, or left unset when no probe is expected at all.
    """
    client = PowerBIClient(
        tenant_id="tenant-under-test",
        client_id="client-under-test",
        client_secret="secret-under-test",
        access_token="token-under-test",
    )
    client.connect = lambda: None
    client.calls = []

    def _request(method, url, json_body=None, timeout=30, max_attempts=3):
        dax = ((json_body or {}).get("queries") or [{}])[0].get("query", "")
        client.calls.append(dax)
        if PROBE_MARKER in dax:
            if probe_raises:
                raise ConnectionError("probe transport died")
            if probe_status >= 300:
                return _Resp({"error": "boom"}, status_code=probe_status)
            if probe is _UNSET:
                raise AssertionError(f"unexpected COUNTROWS probe for: {dax}")
            if probe is None:
                return _Resp({"results": []})
            return _Resp(_envelope([{f"[{PROBE_MARKER}]": probe}]))
        return _Resp(_envelope(returned_rows))

    client._request = _request
    return client


def _run(client, dax, **kwargs):
    return client._execute_dax_internal(WORKSPACE, DATASET, dax, **kwargs)


def _probe_calls(client):
    return [d for d in client.calls if PROBE_MARKER in d]


# --- 1. the hard row cap (free signal) ---------------------------------------


def test_def006_exact_row_cap_is_truncation():
    """100,000 back from a table that is bigger is the defect's plainest shape."""
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, f"EVALUATE SUMMARIZECOLUMNS({TABLE}[Code])")


def test_def006_one_row_under_the_cap_is_not_truncation():
    """The control: the guard must not manufacture failures on complete results."""
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP - 1))
    df = _run(client, f"EVALUATE SUMMARIZECOLUMNS({TABLE}[Code])")
    assert len(df) == POWERBI_EXECUTE_QUERIES_ROW_CAP - 1


def test_def006_row_cap_check_costs_no_api_call():
    """The cap check is free — it must not spend the rate-limited probe budget."""
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, f"EVALUATE TOPN(10, {TABLE})")
    assert _probe_calls(client) == []


def test_def006_small_result_is_untouched():
    client = _client(_rows(12))
    df = _run(client, f"EVALUATE SUMMARIZECOLUMNS({TABLE}[Code])")
    assert len(df) == 12
    assert list(df.columns) == ["Code"]


# --- 2. the response-SIZE cap (needs the probe) ------------------------------


def test_def006_size_cap_on_bare_table_pull_raises():
    """The measured failure: 8 wide columns stopped far short of the row cap."""
    client = _client(_rows(48_222, wide=True), probe=300_086)
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, f"EVALUATE {TABLE}")
    assert len(_probe_calls(client)) == 1


def test_def006_probe_equal_to_returned_rows_is_complete():
    client = _client(_rows(5), probe=5)
    df = _run(client, f"EVALUATE {TABLE}")
    assert len(df) == 5


def test_def006_probe_below_returned_rows_does_not_raise():
    """A stale/odd probe must not invent truncation that did not happen."""
    client = _client(_rows(5), probe=3)
    assert len(_run(client, f"EVALUATE {TABLE}")) == 5


def test_def006_quoted_table_pull_is_probed_too():
    client = _client(_rows(10), probe=99_999)
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, "EVALUATE 'Order Details'")


# --- 3. probe scoping: the cost-control contract -----------------------------

BARE_PULLS = [
    ("EVALUATE Sales", "'Sales'"),
    ("   EVALUATE Sales   ", "'Sales'"),
    ("EVALUATE Sales;", "'Sales'"),
    ("EVALUATE Sales ;  ", "'Sales'"),
    ("evaluate sales", "'sales'"),
    ("EvAlUaTe Sales", "'Sales'"),
    ("EVALUATE 'Order Details'", "'Order Details'"),
    ("  EVALUATE 'Order Details' ;", "'Order Details'"),
    ("EVALUATE _internal_table", "'_internal_table'"),
    ("EVALUATE Sales2024", "'Sales2024'"),
]

NOT_BARE_PULLS = [
    # aggregation — the whole point of the fix is that the model writes these
    "EVALUATE SUMMARIZECOLUMNS(Sales[Code], \"Total\", SUM(Sales[Amount]))",
    "EVALUATE ROW(\"Distinct\", DISTINCTCOUNT(Sales[Code]))",
    "EVALUATE TOPN(5, Sales, Sales[Amount], DESC)",
    "EVALUATE ADDCOLUMNS(Sales, \"X\", 1)",
    # filtering
    "EVALUATE FILTER(Sales, Sales[Amount] > 0)",
    "EVALUATE CALCULATETABLE(Sales, Sales[Year] = 2024)",
    "EVALUATE Sales ORDER BY Sales[Amount] DESC",
    # more than one clause
    "DEFINE MEASURE Sales[M] = SUM(Sales[Amount]) EVALUATE Sales",
    "EVALUATE Sales EVALUATE Promotions",
    "EVALUATE Sales\nORDER BY Sales[Code]",
    # not a table reference at all
    "EVALUATE Sales[Amount]",
    "EVALUATE {1, 2, 3}",
    "EVALUATE (Sales)",
    "EVALUATE Sales, Promotions",
    # nothing to match
    "",
    "   ",
    "SELECT * FROM Sales",
    "EVALUATE",
    "EVALUATE 'Unclosed",
]


@needs_fix
@pytest.mark.parametrize("dax,expected", BARE_PULLS)
def test_def006_bare_table_pull_is_recognized(dax, expected):
    assert _bare_table_pull_target(dax) == expected


@needs_fix
@pytest.mark.parametrize("dax", NOT_BARE_PULLS)
def test_def006_non_bare_dax_is_never_probed(dax):
    """A false positive here costs a rate-limited call on EVERY agent query."""
    assert _bare_table_pull_target(dax) is None


@needs_fix
def test_def006_none_input_is_safe():
    assert _bare_table_pull_target(None) is None


@pytest.mark.parametrize("dax", NOT_BARE_PULLS[:8])
def test_def006_no_probe_call_is_made_for_non_bare_dax(dax):
    """End-to-end cost proof: the fake transport asserts if a probe is issued."""
    client = _client(_rows(7))
    _run(client, dax)
    assert _probe_calls(client) == []
    assert len(client.calls) == 1


def test_def006_bare_pull_spends_exactly_one_probe():
    client = _client(_rows(7), probe=7)
    _run(client, f"EVALUATE {TABLE}")
    assert len(client.calls) == 2


# --- 4. a broken probe must never break a working query ----------------------


def test_def006_probe_http_error_lets_the_query_succeed():
    client = _client(_rows(9), probe_status=429)
    assert len(_run(client, f"EVALUATE {TABLE}")) == 9


def test_def006_probe_transport_exception_lets_the_query_succeed():
    client = _client(_rows(9), probe_raises=True)
    assert len(_run(client, f"EVALUATE {TABLE}")) == 9


def test_def006_probe_empty_response_lets_the_query_succeed():
    client = _client(_rows(9), probe=None)
    assert len(_run(client, f"EVALUATE {TABLE}")) == 9


@needs_fix
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": []},
        {"results": [{}]},
        {"results": [{"tables": []}]},
        {"results": [{"tables": [{"rows": []}]}]},
        {"results": [{"tables": [{"rows": [{}]}]}]},
        {"results": [{"tables": [{"rows": [{"[x]": None}]}]}]},
        {"results": "not-a-list"},
    ],
)
def test_def006_malformed_probe_payload_returns_none(payload):
    client = _client(_rows(1))
    client._request = lambda *a, **k: _Resp(payload)
    assert client._probe_true_row_count(WORKSPACE, DATASET, f"'{TABLE}'") is None


# --- 5. an explicit max_rows is a declared limit, not the defect -------------


def test_def006_explicit_max_rows_returns_the_head_without_raising():
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    df = client.execute_query(
        f"EVALUATE {TABLE}", dataset_id=DATASET, workspace_id=WORKSPACE, max_rows=10
    )
    assert len(df) == 10


def test_def006_explicit_max_rows_skips_the_probe():
    client = _client(_rows(50))
    client.execute_query(
        f"EVALUATE {TABLE}", dataset_id=DATASET, workspace_id=WORKSPACE, max_rows=10
    )
    assert _probe_calls(client) == []


def test_def006_max_rows_zero_is_not_a_declared_limit():
    """0 means "unset" here, so the guard must still run."""
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, f"EVALUATE {TABLE}", max_rows=0)


# --- 6. the flag, and the "off"-is-truthy trap -------------------------------


@pytest.fixture
def guard_flag(monkeypatch):
    from app.settings.config import settings

    def _set(value):
        monkeypatch.setattr(settings, "hybrid_pbi_truncation_guard", value, raising=False)

    return _set


def test_def006_guard_disabled_restores_the_old_silent_behavior(guard_flag):
    guard_flag(False)
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    df = _run(client, f"EVALUATE {TABLE}")
    assert len(df) == POWERBI_EXECUTE_QUERIES_ROW_CAP


def test_def006_guard_enabled_by_default(guard_flag):
    guard_flag(True)
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    with pytest.raises(PowerBIResultTruncatedError):
        _run(client, f"EVALUATE {TABLE}")


@needs_fix
@pytest.mark.parametrize("value", ["off", "OFF", " Off ", "false", "no", "0", ""])
def test_def006_string_off_does_not_enable_the_guard(guard_flag, value):
    """`if value:` is True for the string "off". Three past defects, one rule."""
    guard_flag(value)
    assert _truncation_guard_enabled() is False


@needs_fix
@pytest.mark.parametrize("value", ["on", "ON", " True ", "yes", "1", "true"])
def test_def006_string_on_enables_the_guard(guard_flag, value):
    guard_flag(value)
    assert _truncation_guard_enabled() is True


@needs_fix
def test_def006_string_off_is_honored_end_to_end(guard_flag):
    guard_flag("off")
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    assert len(_run(client, f"EVALUATE {TABLE}")) == POWERBI_EXECUTE_QUERIES_ROW_CAP


@needs_fix
@pytest.mark.parametrize("value", [None, 1, object()])
def test_def006_unreadable_flag_value_defaults_the_guard_on(guard_flag, value):
    """Fail safe: an unrecognized value protects the data, it does not open the gate."""
    guard_flag(value)
    assert _truncation_guard_enabled() is True


@needs_fix
def test_def006_missing_setting_defaults_the_guard_on(monkeypatch):
    from app.settings.config import settings

    monkeypatch.delattr(settings, "hybrid_pbi_truncation_guard", raising=False)
    assert _truncation_guard_enabled() is True


# --- 7. the message has to be actionable by the model that reads it ----------


@needs_fix
def test_def006_message_names_the_real_numbers_when_known():
    msg = _truncation_message(48_222, 300_086, f"EVALUATE {TABLE}")
    assert "48,222" in msg and "300,086" in msg


@needs_fix
def test_def006_message_names_the_cap_when_the_true_count_is_unknown():
    msg = _truncation_message(POWERBI_EXECUTE_QUERIES_ROW_CAP, None, f"EVALUATE {TABLE}")
    assert f"{POWERBI_EXECUTE_QUERIES_ROW_CAP:,}" in msg


@needs_fix
def test_def006_message_forbids_the_two_wrong_reactions():
    """Retrying reproduces the defect; aggregating the prefix IS the defect."""
    msg = _truncation_message(48_222, 300_086, f"EVALUATE {TABLE}").lower()
    assert "do not retry" in msg
    assert "do not aggregate" in msg


@needs_fix
def test_def006_message_hands_over_dax_to_write_instead():
    msg = _truncation_message(48_222, 300_086, f"EVALUATE {TABLE}")
    for construct in ("SUMMARIZECOLUMNS", "TOPN", "DISTINCTCOUNT"):
        assert construct in msg


@needs_fix
def test_def006_message_quotes_the_offending_query():
    msg = _truncation_message(48_222, 300_086, f"  EVALUATE {TABLE}  ")
    assert f"EVALUATE {TABLE}" in msg


@needs_fix
def test_def006_message_does_not_dump_an_unbounded_query():
    msg = _truncation_message(1, 2, "EVALUATE " + "X" * 5_000)
    assert len(msg) < 2_000


def test_def006_raised_message_is_the_actionable_one():
    client = _client(_rows(48_222, wide=True), probe=300_086)
    with pytest.raises(PowerBIResultTruncatedError) as excinfo:
        _run(client, f"EVALUATE {TABLE}")
    text = str(excinfo.value)
    assert "48,222" in text and "300,086" in text
    assert "SUMMARIZECOLUMNS" in text


def test_def006_error_is_an_exception_not_a_warning():
    """A warning would let the partial frame reach pandas — the original failure."""
    assert issubclass(PowerBIResultTruncatedError, Exception)
    client = _client(_rows(POWERBI_EXECUTE_QUERIES_ROW_CAP))
    with pytest.raises(Exception):
        _run(client, f"EVALUATE {TABLE}")


# --- 8. no false alarms on the shapes that are simply small ------------------


@pytest.mark.parametrize("n", [0, 1, 2, 500])
def test_def006_ordinary_aggregate_results_pass_through(n):
    client = _client(_rows(n))
    df = _run(client, f"EVALUATE SUMMARIZECOLUMNS({TABLE}[Code])")
    assert len(df) == n
    assert _probe_calls(client) == []


def test_def006_empty_result_is_an_empty_frame():
    client = _client([])
    assert _run(client, f"EVALUATE {TABLE}").empty
