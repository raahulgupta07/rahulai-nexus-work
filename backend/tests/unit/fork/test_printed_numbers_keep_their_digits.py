"""What the model prints of its own result must not lose digits.

Generated code routinely ends with `print("df head:", df.head())`, and that
printed text is the model's PRIMARY view of what its query returned — the
stdout log is fed straight back to it.

Pandas prints floats at six significant figures and switches to scientific
notation for large magnitudes, so a real total came back as:

    0       City Mart     2.332757e+09      (true value 2,332,757,360)
    1    City Express     1.333483e+09      (true value 1,333,482,931)
    2           Ocean     9.470862e+08      (true value 947,086,167)

Measured consequence, three runs against the live product asking for net sales
by banner: ranks 3, 4 and 5 came back exact every time and ranks 1 and 2 never
did — once as "largest (see chart)", once reconstructed from a rounded
millions column (out by 2,640), once as the word "highest". The model said so
itself in its trace: *"a couple of large figures were hard to read back
cleanly."*

Money in this product is Myanmar Kyat, where ordinary totals run to ten
digits — so the threshold for this is crossed by everyday questions, not
edge cases.

The fix sets a display format on the sandbox's pandas so printed frames carry
their full value. It changes only what is PRINTED; the DataFrame, the stored
step data and every number the API returns are untouched.
"""
import io
import re

import pandas as pd
import pytest

from app.ai.code_execution.code_execution import apply_readable_number_printing

TRUE_TOTALS = {
    "City Mart": 2332757360.0,
    "City Express": 1333482931.0,
    "Ocean": 947086167.0,
    "Marketplace": 348347680.0,
    "Seasons Bakery": 174935445.0,
}


@pytest.fixture
def frame():
    return pd.DataFrame(
        {"banner": list(TRUE_TOTALS), "total_net_sales": list(TRUE_TOTALS.values())}
    )


@pytest.fixture(autouse=True)
def _restore():
    """Never leak a display option out of a test."""
    before = pd.get_option("display.float_format")
    yield
    pd.set_option("display.float_format", before)


# --- the defect ------------------------------------------------------------

def test_the_default_printing_really_does_lose_digits(frame):
    """★Guard the guard. If pandas ever stops doing this, the fix below is
    pointless and this test says so instead of passing silently."""
    pd.set_option("display.float_format", None)
    assert "e+09" in frame.to_string(), "the premise of this file no longer holds"


def test_a_ten_digit_total_prints_every_digit(frame):
    apply_readable_number_printing()
    printed = frame.to_string()
    assert "e+09" not in printed and "e+08" not in printed
    for banner, value in TRUE_TOTALS.items():
        assert f"{value:,.2f}" in printed, f"{banner} lost precision when printed"


def test_the_exact_value_is_recoverable_from_the_text(frame):
    """★The real requirement. Not "looks nicer" — a reader must be able to get
    the number back out of the text without inventing anything."""
    apply_readable_number_printing()
    printed = frame.to_string()
    found = {float(m.replace(",", "")) for m in re.findall(r"[\d,]+\.\d{2}", printed)}
    for value in TRUE_TOTALS.values():
        assert value in found


def test_print_of_head_is_covered_too(frame):
    """`print("df head:", df.head())` is the exact line generated code writes."""
    apply_readable_number_printing()
    buf = io.StringIO()
    print("df head:", frame.head(), file=buf)
    assert "e+09" not in buf.getvalue()
    assert "2,332,757,360.00" in buf.getvalue()


# --- what must NOT change --------------------------------------------------

def test_the_dataframe_itself_is_untouched(frame):
    """Only PRINTING changes. The values, the dtype and anything serialised
    downstream stay exactly as they were."""
    apply_readable_number_printing()
    assert frame["total_net_sales"].tolist() == list(TRUE_TOTALS.values())
    assert str(frame["total_net_sales"].dtype) == "float64"
    assert frame["total_net_sales"].max() == 2332757360.0


def test_small_numbers_stay_readable():
    """A rate or a ratio must not turn into noise."""
    apply_readable_number_printing()
    df = pd.DataFrame({"share": [0.25, 0.5, 0.125]})
    printed = df.to_string()
    assert "0.25" in printed and "0.50" in printed


def test_integers_are_left_alone():
    """The float format must not touch integer columns — a year or an id
    printed as 2,026.00 is worse than what we started with."""
    apply_readable_number_printing()
    df = pd.DataFrame({"year": [2024, 2025, 2026], "outlet_id": [7, 8, 9]})
    printed = df.to_string()
    assert "2,024.00" not in printed
    assert "2024" in printed


def test_applying_it_twice_is_harmless():
    apply_readable_number_printing()
    apply_readable_number_printing()
    assert pd.get_option("display.float_format") is not None


def test_nulls_do_not_blow_up():
    apply_readable_number_printing()
    df = pd.DataFrame({"total": [1234567890.0, None]})
    printed = df.to_string()
    assert "1,234,567,890.00" in printed
    assert "NaN" in printed


# --- it is actually wired into the sandbox ---------------------------------

def test_the_executor_applies_it_before_running_generated_code():
    """★A helper nothing calls fixes nothing — the recurring shape in this
    codebase. It must be applied on the path that builds the sandbox."""
    import inspect
    from app.ai.code_execution.code_execution import StreamingCodeExecutor
    body = inspect.getsource(StreamingCodeExecutor.execute_code)
    assert "apply_readable_number_printing()" in body
    i_apply = body.index("apply_readable_number_printing()")
    assert i_apply < body.index("exec(code, local_namespace)"), (
        "the format is set after the code has already printed"
    )
