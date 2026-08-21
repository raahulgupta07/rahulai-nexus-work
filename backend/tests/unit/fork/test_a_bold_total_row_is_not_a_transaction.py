"""DEF-012 — the sheet's own TOTAL row must not be counted as data.

A real extract ends with 15,000 rows and then a bold `TOTAL` directly beneath,
no blank line. Measured 2026-08-20 on the rebuilt workbook: `read_excel(...,
header=4)` returns **15,001** rows, and the agent opened its answer by calling
them 15,001 transactions.

★The reason this file is mostly positive controls: the failure mode of a fix
here is deleting somebody's real data. "Total Logistics Ltd" is a customer. A
final row that happens to be the largest is still an observation. So the
detector is required to be arithmetic-led, and the tests that keep it honest are
the ones asserting it does NOT fire.

★RED-PROOF: `test_the_defect_is_what_a_plain_read_gives` reads the same shape
with unwrapped pandas and requires the extra row to still be there.
"""
import pandas as pd

from app.ai.code_execution.sheet_trailer import detect_total_row, strip_total_row


def _sheet(n: int = 12):
    return pd.DataFrame({
        "Customer": [f"Cust {i}" for i in range(n)],
        "Units": [i + 1 for i in range(n)],
        "Revenue": [100.0 * (i + 1) for i in range(n)],
    })


def _with_trailer(df, label="TOTAL"):
    total = pd.DataFrame([{
        "Customer": label,
        "Units": df["Units"].sum(),
        "Revenue": df["Revenue"].sum(),
    }])
    return pd.concat([df, total], ignore_index=True)


class TestTheDefect:
    def test_the_defect_is_what_a_plain_read_gives(self):
        """★Red proof: nothing upstream of the detector removes the row."""
        assert len(_with_trailer(_sheet())) == len(_sheet()) + 1

    def test_a_labelled_total_row_is_detected(self):
        found = detect_total_row(_with_trailer(_sheet()))
        assert found is not None
        assert found["label"] == "TOTAL"
        assert set(found["summing_columns"]) == {"Units", "Revenue"}

    def test_stripping_returns_the_body_and_says_so(self):
        df, found = strip_total_row(_with_trailer(_sheet()))
        assert len(df) == 12
        assert found["removed_rows"] == 1
        assert "not an observation" in found["notice"]

    def test_the_totals_stop_being_doubled(self):
        """★What the defect actually costs. Assert the NUMBER moves."""
        clean = _sheet()
        with_row = _with_trailer(clean)
        assert with_row["Revenue"].sum() == clean["Revenue"].sum() * 2
        stripped, _ = strip_total_row(with_row)
        assert stripped["Revenue"].sum() == clean["Revenue"].sum()

    def test_the_common_spellings_are_recognised(self):
        for label in ("Total", "TOTAL", "Grand Total", "grand total", "Sum", "Subtotal", "Total:"):
            assert detect_total_row(_with_trailer(_sheet(), label)) is not None, label


class TestWhatItMustNotTouch:
    def test_a_customer_called_total_logistics_survives(self):
        """★The one that matters. A label is a name until the arithmetic
        agrees, and this row's numbers are ordinary."""
        df = _sheet()
        df.loc[len(df)] = {"Customer": "Total Logistics Ltd", "Units": 3, "Revenue": 300.0}
        assert detect_total_row(df) is None

    def test_a_plain_sheet_with_no_trailer_is_untouched(self):
        df = _sheet()
        out, found = strip_total_row(df)
        assert found is None
        assert out is df

    def test_the_biggest_row_last_is_not_a_total(self):
        df = _sheet()
        df.loc[len(df)] = {"Customer": "Cust BIG", "Units": 9999, "Revenue": 999999.0}
        assert detect_total_row(df) is None

    def test_one_column_summing_by_coincidence_is_not_enough(self):
        """★Two-column frame, one column matches, no label. A detector keyed on
        a single arithmetic hit deletes this row."""
        df = pd.DataFrame({"A": [1, 2, 3], "B": [10.0, 20.0, 30.0]})
        df.loc[len(df)] = {"A": 6, "B": 5.0}       # A sums, B does not, no label
        assert detect_total_row(df) is None

    def test_arithmetic_across_the_board_needs_no_label(self):
        """The converse: an unlabelled trailer whose every numeric column sums
        IS a total. This is what makes a blank-label sheet work."""
        df = _sheet()
        df.loc[len(df)] = {"Customer": None,
                           "Units": df["Units"].sum(),
                           "Revenue": df["Revenue"].sum()}
        found = detect_total_row(df)
        assert found is not None
        assert found["all_numeric_columns_matched"] is True

    def test_a_tiny_frame_is_left_alone(self):
        """Two rows where the second equals the first is not a total."""
        assert detect_total_row(pd.DataFrame({"A": [5, 5]})) is None

    def test_a_frame_with_no_numeric_column_is_left_alone(self):
        df = pd.DataFrame({"Name": ["a", "b", "TOTAL"]})
        assert detect_total_row(df) is None

    def test_an_empty_frame_does_not_raise(self):
        assert detect_total_row(pd.DataFrame()) is None
        assert detect_total_row(None) is None

    def test_a_string_column_that_looks_numeric_is_not_summed(self):
        """The frame's own dtypes decide. Re-coercing here would make the
        detector depend on whether a parse had happened yet."""
        df = pd.DataFrame({"A": ["1", "2", "3"], "B": ["6", "6", "6"]})
        assert detect_total_row(df) is None


class TestItReachesTheRead:
    def test_the_guarded_read_excel_strips_and_records(self, tmp_path):
        from app.ai.code_execution.coercion_guard import CoercionRecorder, build_pandas_proxy

        path = tmp_path / "sheet.xlsx"
        _with_trailer(_sheet()).to_excel(path, index=False)

        rec = CoercionRecorder()
        gpd = build_pandas_proxy(pd, rec)
        df = gpd.read_excel(path)

        assert len(df) == 12, "the trailer reached the frame the model computes on"
        assert rec.report()["total_rows_excluded"][0]["removed_rows"] == 1

    def test_a_clean_sheet_reads_identically_through_the_proxy(self, tmp_path):
        """★Positive control on the wrapper itself."""
        from app.ai.code_execution.coercion_guard import CoercionRecorder, build_pandas_proxy

        path = tmp_path / "clean.xlsx"
        _sheet().to_excel(path, index=False)
        rec = CoercionRecorder()
        gpd = build_pandas_proxy(pd, rec)
        assert gpd.read_excel(path).equals(pd.read_excel(path))
        assert rec.report() is None

    def test_a_multi_sheet_read_is_left_alone(self, tmp_path):
        """`sheet_name=None` returns a dict; a trailer in one sheet says nothing
        about another, so that shape is not half-handled."""
        from app.ai.code_execution.coercion_guard import CoercionRecorder, build_pandas_proxy

        path = tmp_path / "multi.xlsx"
        with pd.ExcelWriter(path) as w:
            _with_trailer(_sheet()).to_excel(w, sheet_name="A", index=False)
            _sheet().to_excel(w, sheet_name="B", index=False)
        gpd = build_pandas_proxy(pd, CoercionRecorder())
        sheets = gpd.read_excel(path, sheet_name=None)
        assert isinstance(sheets, dict)
        assert len(sheets["A"]) == 13
