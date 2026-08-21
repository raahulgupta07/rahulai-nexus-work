"""DEF-011 — `errors="coerce"` may not delete 87% of a sheet without saying so.

Measured 2026-08-20 on a rebuilt 15,000-row extract whose `Txn Date` column is
written in three spellings by three upstream POS systems::

    pd.to_datetime(df["Txn Date"], errors="coerce")   ->  13,033 NaT of 15,001
    df.dropna(subset=["Txn Date", ...])               ->  1,947 rows left

87% of the sheet gone, no exception, nothing on any screen saying so. The
survivors were mis-dated too: the parsed span came out 2025-01-03 .. 2025-12-07
for a workbook containing nothing outside March-July, because only rows whose
day-of-month was 12 or lower can be read under both `dd/mm` and `mm/dd`.

★RED-PROOF, carried in the file rather than done once at a shell prompt:
`test_the_original_defect_still_reproduces_unguarded` builds the same
three-spelling column and requires the UNGUARDED call to still lose most of it.
If pandas ever changes its inference and that stops being true, this test says
so directly instead of the rest of the file quietly passing for a new reason.

★The positive controls are what stop a cheap fix. A single-format column, an
already-typed column and a column of genuine rubbish must all come through
unchanged — a "fix" that always retries, or that repairs by inventing dates,
fails those and not the headline one.
"""
import warnings

import pandas as pd
import pytest

from app.ai.code_execution.coercion_guard import (
    CoercionRecorder,
    build_pandas_proxy,
    infer_dayfirst,
)


def _three_spellings(n: int = 300) -> pd.Series:
    """A `Txn Date` column as three POS systems actually write it.

    Every third value is `dd/mm/YYYY` with a day above 12, which is what makes
    the column resolvable — and what a single inferred format destroys.
    """
    out = []
    for i in range(n):
        day = (i % 28) + 1
        month = (i % 5) + 3           # March..July, as in the measured sheet
        if i % 3 == 0:
            out.append(f"{day:02d}/{month:02d}/2025")
        elif i % 3 == 1:
            out.append(f"2025-{month:02d}-{day:02d}")
        else:
            out.append(f"{day}-{pd.Timestamp(2025, month, 1).strftime('%b')}-2025")
    return pd.Series(out)


def _guarded():
    rec = CoercionRecorder()
    return build_pandas_proxy(pd, rec), rec


def _quiet(fn, *a, **kw):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*a, **kw)


class TestTheDefect:
    def test_the_original_defect_still_reproduces_unguarded(self):
        """★The red proof. Real pandas, the line the model wrote."""
        col = _three_spellings()
        parsed = _quiet(pd.to_datetime, col, errors="coerce")
        assert parsed.isna().sum() > len(col) / 2, (
            "the unguarded coerce no longer loses most of a three-spelling "
            "column — re-read this file before trusting the rest of it"
        )

    def test_the_guarded_parse_keeps_the_rows(self):
        gpd, _ = _guarded()
        col = _three_spellings()
        parsed = _quiet(gpd.to_datetime, col, errors="coerce")
        assert parsed.isna().sum() == 0

    def test_the_guarded_parse_keeps_the_rows_in_the_right_months(self):
        """Serializable is not the point — a parse that recovers the rows and
        transposes day and month is the WORSE outcome, because the totals then
        look complete."""
        gpd, _ = _guarded()
        parsed = _quiet(gpd.to_datetime, _three_spellings(), errors="coerce")
        assert set(parsed.dt.month.unique()) == {3, 4, 5, 6, 7}
        assert parsed.dt.day.max() > 12, "day and month are transposed"

    def test_the_loss_is_reported_even_though_it_was_recovered(self):
        """A guard that goes quiet on success gives nobody a way to tell
        'nothing was lost' from 'nothing was checked'."""
        gpd, rec = _guarded()
        _quiet(gpd.to_datetime, _three_spellings(), errors="coerce")
        report = rec.report()
        assert report is not None
        assert report["recovered_total"] > 0


class TestWhatItRefusesToGuess:
    def test_the_direction_comes_from_evidence(self):
        dayfirst, why = infer_dayfirst(["05/04/2025", "25/04/2025", "2025-04-05"])
        assert dayfirst is True
        assert "first component" in why

    def test_the_other_direction_is_read_the_same_way(self):
        dayfirst, why = infer_dayfirst(["04/05/2025", "04/25/2025"])
        assert dayfirst is False
        assert "second component" in why

    def test_an_all_ambiguous_column_is_not_guessed(self):
        """★Every slot <= 12, so nothing in the data resolves it. Guessing here
        is exactly how a 'fixed' parse produces a confident wrong answer."""
        dayfirst, why = infer_dayfirst(["05/04/2025", "06/07/2025", "01/02/2025"])
        assert dayfirst is None
        assert why is None

    def test_a_contradictory_column_is_reported_not_resolved(self):
        dayfirst, why = infer_dayfirst(["25/04/2025", "04/25/2025"])
        assert dayfirst is None
        assert "contradictory" in why

    def test_iso_and_month_names_are_not_counted_as_evidence(self):
        """They parse the same either way, so counting them would dilute the
        signal without adding any."""
        assert infer_dayfirst(["2025-04-25", "25-Apr-2025"]) == (None, None)


class TestPositiveControls:
    def test_a_clean_single_format_column_is_untouched(self):
        gpd, rec = _guarded()
        col = pd.Series([f"2025-04-{d:02d}" for d in range(1, 29)])
        guarded = _quiet(gpd.to_datetime, col, errors="coerce")
        plain = _quiet(pd.to_datetime, col, errors="coerce")
        assert guarded.equals(plain)
        assert rec.report() is None, "nothing was lost — do not manufacture a notice"

    def test_genuine_rubbish_is_still_dropped_and_counted(self):
        """★The half a 'recover everything' fix would break. Values that are
        not dates must stay NaT — the guard rescues spellings, not nonsense."""
        gpd, rec = _guarded()
        col = pd.Series(["25/04/2025", "not a date", "", "n/a"])
        parsed = _quiet(gpd.to_datetime, col, errors="coerce")
        assert parsed.isna().sum() == 3
        assert rec.report()["events"][0]["unparsed"] == 3

    def test_an_explicit_format_is_passed_straight_through(self):
        """A caller who named a format has stated the contract. Overriding it
        with `format='mixed'` would silently accept rows they meant to reject."""
        gpd, _ = _guarded()
        col = pd.Series(["25/04/2025", "2025-04-25"])
        parsed = _quiet(gpd.to_datetime, col, format="%d/%m/%Y", errors="coerce")
        assert parsed.isna().sum() == 1

    def test_a_non_coercing_call_still_raises(self):
        """`errors='raise'` already fails loudly; the guard must not soften it."""
        gpd, _ = _guarded()
        with pytest.raises(Exception):
            _quiet(gpd.to_datetime, pd.Series(["not a date"]), errors="raise")

    def test_to_numeric_reports_but_does_not_repair(self):
        """A column of "N/A" and "-" beside real numbers is CORRECTLY coerced to
        NaN. Repairing here would silently change figures."""
        gpd, rec = _guarded()
        out = _quiet(gpd.to_numeric, pd.Series(["1250", "N/A", "-", "7"]), errors="coerce")
        assert out.isna().sum() == 2
        event = [e for e in rec.report()["events"] if e["kind"] == "numeric"][0]
        assert event["unparsed"] == 2
        assert "recovered" not in event


class TestTheProxyIsStillPandas:
    def test_the_untouched_surface_is_the_real_one(self):
        gpd, _ = _guarded()
        for name in ("DataFrame", "Series", "concat", "merge", "NaT", "read_csv"):
            assert getattr(gpd, name) is getattr(pd, name), name

    def test_exactly_three_names_are_wrapped(self):
        """★Pin the blast radius. Every wrapped name is a place behaviour can
        diverge from pandas, so the set is small and stated."""
        gpd, _ = _guarded()
        wrapped = {n for n in dir(pd)
                   if getattr(gpd, n, None) is not getattr(pd, n, None)}
        assert wrapped == {"to_datetime", "to_numeric", "read_excel"}

    def test_isinstance_still_works_through_the_proxy(self):
        gpd, _ = _guarded()
        assert isinstance(pd.DataFrame({"a": [1]}), gpd.DataFrame)

    def test_submodules_still_resolve(self):
        gpd, _ = _guarded()
        assert gpd.api.types.is_numeric_dtype(pd.Series([1, 2]))


class TestBothWaysIntoPandasAreGuarded:
    """★The seam that makes this stick.

    `local_namespace['pd']` is only one of the two ways generated code reaches
    pandas — `import pandas as pd` is legal and most generated bodies open with
    it. A guard wired to the namespace alone would miss the common path, which
    is the same class of miss as a helper with no caller.
    """

    def test_the_namespace_hands_out_the_guarded_module(self):
        import inspect
        from app.ai.code_execution.code_execution import StreamingCodeExecutor
        src = inspect.getsource(StreamingCodeExecutor.execute_code)
        assert "build_pandas_proxy" in src
        assert "'pd': _guarded_pd" in src

    def test_the_import_statement_resolves_to_the_same_module(self):
        from app.ai.code_execution.code_execution import _build_safe_builtins
        gpd, _ = _guarded()
        builtins_dict = _build_safe_builtins(pandas_module=gpd)
        assert builtins_dict["__import__"]("pandas", None, None, (), 0) is gpd

    def test_a_from_import_resolves_to_the_guarded_function(self):
        """`from pandas import to_datetime` — CPython returns the MODULE here
        and getattrs the name off it, so this path must land on the proxy too."""
        from app.ai.code_execution.code_execution import _build_safe_builtins
        gpd, _ = _guarded()
        builtins_dict = _build_safe_builtins(pandas_module=gpd)
        module = builtins_dict["__import__"]("pandas", None, None, ("to_datetime",), 0)
        assert module.to_datetime is gpd.to_datetime
        assert module.to_datetime is not pd.to_datetime

    def test_a_forbidden_import_is_still_refused(self):
        """★Positive control on the security half. The DEF-011 branch sits
        inside `_guarded_import`; a mistake there would open the sandbox."""
        from app.ai.code_execution.code_execution import _build_safe_builtins
        gpd, _ = _guarded()
        builtins_dict = _build_safe_builtins(pandas_module=gpd)
        with pytest.raises(ImportError):
            builtins_dict["__import__"]("os", None, None, (), 0)

    def test_a_dotted_pandas_import_is_left_to_the_real_machinery(self):
        from app.ai.code_execution.code_execution import _build_safe_builtins
        gpd, _ = _guarded()
        builtins_dict = _build_safe_builtins(pandas_module=gpd)
        mod = builtins_dict["__import__"]("pandas.api.types", None, None, (), 0)
        assert mod is not gpd

    def test_the_disclosure_reaches_the_step_payload(self):
        """The report has to travel with the rows, at the same place truncation
        is declared, or a caller has no way to refuse on it."""
        import inspect
        from app.ai.code_execution.code_execution import StreamingCodeExecutor
        src = inspect.getsource(StreamingCodeExecutor.format_df_for_widget)
        assert "_coercion_recorder" in src
        assert '"coercion"' in src


class TestTheDisclosureReachesTheArtifact:
    """★A record nobody reads is the same silence in a new place.

    The completeness gate already narrates truncation onto the artifact. A
    coercion loss and an excluded TOTAL row are the same kind of fact — the
    artifact is built on fewer rows than the file holds — and must arrive
    beside it rather than sitting unread on the step.
    """

    def _source(self):
        import inspect
        from app.ai.tools.implementations.create_artifact import CreateArtifactTool
        return inspect.getsource(CreateArtifactTool)

    def test_the_artifact_build_reads_the_coercion_record(self):
        src = self._source()
        assert '_sdata.get("coercion")' in src

    def test_the_excluded_total_row_is_narrated_too(self):
        assert '"total_rows_excluded"' in self._source()

    def test_it_warns_rather_than_refusing(self):
        """★The positive control that keeps this usable. A revenue column with
        "N/A" lines is CORRECTLY coerced to NaN; refusing every artifact over
        such data is how a guard gets switched off. Only truncation refuses."""
        src = self._source()
        gate = src[src.index("if _completeness_gate_enabled():"):]
        assert "coercion" not in gate.split("yield ToolEndEvent")[0], (
            "a coercion loss must not reach the refusal condition — it is a "
            "warning, and the gate refuses only on truncation"
        )
