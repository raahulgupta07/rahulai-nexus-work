"""DEF-011 — a coerced parse may not delete rows silently.

What this exists to stop
------------------------
Generated analysis code opens with two lines that look completely ordinary::

    df["Txn Date"] = pd.to_datetime(df["Txn Date"], errors="coerce")
    ...
    df = df.dropna(subset=["Txn Date", "Revenue (MMK)"])

`errors="coerce"` turns every value pandas could not parse into `NaT`, and the
`dropna` a few lines later deletes those rows. Neither line raises. Nothing in
the result says a row ever existed.

Measured on a 15,000-row extract whose `Txn Date` column was written by three
upstream POS systems in three spellings (`05/04/2025`, `2025-04-05`,
`5-Apr-2025` — which is what a real extract looks like): pandas inferred ONE
format for the whole column, and

    NaT after coerce   : 13,033 of 15,001   (86.9%)
    rows after dropna  : 1,947
    unique dates left  : 60                  ( = 5 months x 12 days )

Sixty distinct dates across a five-month span is the signature: only rows whose
day-of-month was 12 or lower survived, because those are the only `dd/mm` values
that are also valid `mm/dd`. The survivors then had day and month TRANSPOSED —
the parsed span came out as 2025-01-03 .. 2025-12-07 for a workbook that
contains nothing outside March-July.

So the failure is not "some rows were dropped". It is that **87% of the sheet
was deleted and the remainder was silently mis-dated**, and every total, chart
and headline built downstream was confident, plausible and wrong.

Why the fix is here and not in the prompt
----------------------------------------
A prompt rule telling the model to avoid `errors="coerce"` does not hold — this
codebase has measured that before (the chat model kept deriving numbers badly
until the maths moved into tools). The guard has to sit where the parse actually
happens, so it applies whatever the model wrote.

★The hook is the IMPORT, not the namespace. `local_namespace['pd']` is only one
of the two ways generated code reaches pandas: `import pandas as pd` is legal
(see `_guarded_import`) and most generated bodies open with it. Instrumenting
only the namespace entry would leave the common path uninstrumented, which is
the same class of miss as a helper that exists but has no caller.

What it does
------------
1. **Infers the date order from evidence rather than defaulting.** A slash-form
   token whose FIRST slot exceeds 12 can only be day-first; one whose SECOND
   slot exceeds 12 can only be month-first. On the workbook above: 3,032 tokens
   prove day-first, 0 prove month-first. When the evidence is absent or
   contradictory this returns None and nothing is assumed.

2. **Parses per value instead of inferring one format for the column**
   (`format="mixed"`), which is what lets three spellings coexist. Same column,
   same call site: 15,000 of 15,001 values parse. The one remaining `NaT` is the
   sheet's trailing bold TOTAL row — the trap 9.4 is about — so an honest date
   parse identifies it for free.

3. **Records what it could not parse.** The report travels with the step, so a
   loss that used to be invisible is now something a caller can refuse on. This
   module only records; deciding what a loss means is the caller's job.

★It reports even when it recovers everything. A guard that is silent on success
gives nobody a way to tell "nothing was lost" from "nothing was checked".
"""
from __future__ import annotations

import logging
import re
import types
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: A `d/m/Y` or `m/d/Y` token — the only spelling whose meaning is ambiguous.
#: ISO (`2025-04-05`) and month-name (`5-Apr-2025`) forms are unambiguous and
#: are deliberately not consulted for evidence.
_SLASH_TOKEN = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$")

#: Below this many parsed values, a loss ratio is noise rather than a signal.
_MIN_SAMPLE = 20


@dataclass
class CoercionEvent:
    """One coerced parse and what it cost."""

    kind: str                       # "datetime" | "numeric"
    total: int
    unparsed: int
    recovered: int = 0              # values a re-parse rescued (see below)
    dayfirst: Optional[bool] = None
    evidence: Optional[str] = None  # why that direction was chosen
    samples: List[str] = field(default_factory=list)

    @property
    def loss_ratio(self) -> float:
        return (self.unparsed / self.total) if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "kind": self.kind,
            "total": self.total,
            "unparsed": self.unparsed,
            "loss_ratio": round(self.loss_ratio, 4),
        }
        if self.recovered:
            out["recovered"] = self.recovered
        if self.dayfirst is not None:
            out["dayfirst"] = self.dayfirst
        if self.evidence:
            out["evidence"] = self.evidence
        if self.samples:
            out["unparsed_samples"] = self.samples
        return out


class CoercionRecorder:
    """Collects every coerced parse made by one execution of generated code.

    One recorder per run. Never raises: an instrumented parse that fails to
    record still returns the value the model asked for, because a guard that can
    break the thing it guards is worse than no guard.

    ★It also carries DEF-012's trailer removals. Both are the same kind of fact
    — "the rows you are computing over are not the rows in the file, and here is
    why" — and a caller that has to consult two disclosures will consult one.
    """

    def __init__(self, significant_loss: float = 0.05) -> None:
        self.events: List[CoercionEvent] = []
        self.trailers: List[Dict[str, Any]] = []
        self.significant_loss = significant_loss

    def record_trailer(self, disclosure: Dict[str, Any]) -> None:
        self.trailers.append(disclosure)

    def record(self, event: CoercionEvent) -> None:
        self.events.append(event)

    def losses(self) -> List[CoercionEvent]:
        """Events that lost a meaningful share of a meaningfully sized column."""
        return [
            e for e in self.events
            if e.total >= _MIN_SAMPLE and e.loss_ratio > self.significant_loss
        ]

    def report(self) -> Optional[Dict[str, Any]]:
        """The disclosure to attach to a step, or None if there was nothing to
        disclose. Recoveries are reported too — see the module note.

        ★`_MIN_SAMPLE` and the loss threshold gate the NOTICE, not the events.
        They exist so a tiny or barely-lossy column does not raise an alarm; a
        value that failed to parse is still a fact, and suppressing the record
        of it is the very silence this module was built to remove. Recording
        everything and shouting selectively are different decisions.
        """
        losses = self.losses()
        recovered = sum(e.recovered for e in self.events)
        unparsed = sum(e.unparsed for e in self.events)
        if not unparsed and not recovered and not self.trailers:
            return None
        out: Dict[str, Any] = {"events": [e.to_dict() for e in self.events]}
        if self.trailers:
            out["total_rows_excluded"] = self.trailers
        if recovered:
            out["recovered_total"] = recovered
        if losses:
            worst = max(losses, key=lambda e: e.loss_ratio)
            out["worst_loss_ratio"] = round(worst.loss_ratio, 4)
            out["notice"] = (
                f"A coerced parse could not read {worst.unparsed:,} of "
                f"{worst.total:,} values ({worst.loss_ratio:.1%}). Rows holding "
                f"them are dropped by any later dropna WITHOUT an error, so "
                f"totals over the remainder are PARTIAL."
            )
        return out


def infer_dayfirst(values: Any) -> tuple[Optional[bool], Optional[str]]:
    """Decide day-first vs month-first from the data, or decline to decide.

    Returns `(dayfirst, evidence)`. `dayfirst` is None when the column carries
    no proof either way — an all-ambiguous column (every slot <= 12) genuinely
    cannot be resolved from the values alone, and guessing there is how a
    "fixed" parse produces a confident wrong answer.

    ★Only slash/dot/dash numeric tokens are consulted. An ISO or month-name
    spelling parses the same under either direction, so counting it as evidence
    would dilute the signal without adding any.
    """
    first_gt12 = 0
    second_gt12 = 0
    try:
        for raw in values:
            if raw is None:
                continue
            m = _SLASH_TOKEN.match(str(raw))
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12:
                first_gt12 += 1
            if b > 12:
                second_gt12 += 1
    except Exception:  # pragma: no cover - defensive; never break the parse
        return None, None

    if first_gt12 and not second_gt12:
        return True, f"{first_gt12:,} values have a first component above 12"
    if second_gt12 and not first_gt12:
        return False, f"{second_gt12:,} values have a second component above 12"
    if first_gt12 and second_gt12:
        # Both directions are contradicted by some row. The column mixes two
        # incompatible conventions and no single answer is right for all of it.
        return None, (
            f"contradictory: {first_gt12:,} values imply day-first and "
            f"{second_gt12:,} imply month-first"
        )
    return None, None


def _sample_unparsed(values: Any, mask: Any, limit: int = 5) -> List[str]:
    """A few of the values that would not parse, for the disclosure."""
    out: List[str] = []
    try:
        for raw in values[mask][:limit]:
            text = str(raw)
            out.append(text if len(text) <= 60 else text[:57] + "...")
    except Exception:  # pragma: no cover - defensive
        return []
    return out


def guarded_to_datetime(pd_module: Any, recorder: Optional[CoercionRecorder],
                        arg: Any, *args: Any, **kwargs: Any) -> Any:
    """`pd.to_datetime` that parses per value and reports what it lost.

    Behaviour is changed ONLY for the case that was silently destructive: a
    coerced parse of a column of strings with no explicit `format`. An explicit
    `format=`, or `errors=` anything but "coerce", is passed straight through
    untouched — a caller who named a format has stated the contract and a caller
    who did not ask for coercion already gets an exception.
    """
    real = pd_module.to_datetime
    errors = kwargs.get("errors")
    if errors != "coerce" or "format" in kwargs or args:
        return real(arg, *args, **kwargs)

    try:
        baseline = real(arg, **kwargs)
    except Exception:
        # The unguarded call is the contract; if it raises, that is the answer.
        raise

    try:
        n = len(baseline)
    except Exception:
        return baseline
    if not n:
        return baseline

    base_bad = int(baseline.isna().sum())
    if base_bad == 0:
        # Nothing was lost, so there is nothing to improve and nothing to
        # disclose beyond the fact that it was checked.
        if recorder is not None:
            recorder.record(CoercionEvent(kind="datetime", total=n, unparsed=0))
        return baseline

    # Something failed to parse. Re-read it per value instead of under one
    # inferred format, with the direction taken from the column's own evidence.
    dayfirst, evidence = infer_dayfirst(arg)
    retry_kwargs = dict(kwargs)
    retry_kwargs["format"] = "mixed"
    if dayfirst is not None:
        retry_kwargs["dayfirst"] = dayfirst
    elif "dayfirst" in kwargs:
        retry_kwargs["dayfirst"] = kwargs["dayfirst"]

    try:
        retried = real(arg, **retry_kwargs)
        retry_bad = int(retried.isna().sum())
    except Exception as exc:
        logger.debug("DEF-011: per-value re-parse failed (%s) — keeping the original", exc)
        retried, retry_bad = baseline, base_bad

    if retry_bad < base_bad:
        result, unparsed, recovered = retried, retry_bad, base_bad - retry_bad
    else:
        result, unparsed, recovered = baseline, base_bad, 0

    if recorder is not None:
        recorder.record(CoercionEvent(
            kind="datetime",
            total=n,
            unparsed=unparsed,
            recovered=recovered,
            dayfirst=dayfirst,
            evidence=evidence,
            samples=_sample_unparsed(arg, result.isna()) if unparsed else [],
        ))
    return result


def guarded_to_numeric(pd_module: Any, recorder: Optional[CoercionRecorder],
                       arg: Any, *args: Any, **kwargs: Any) -> Any:
    """`pd.to_numeric` that reports what a coerced parse could not read.

    Deliberately does NOT try to repair anything. A column holding "N/A" and "-"
    beside real numbers is correctly coerced to NaN, and stripping separators or
    currency marks here would silently change figures. Recording is the whole
    job: the loss stops being invisible, and what it means is the caller's call.
    """
    real = pd_module.to_numeric
    result = real(arg, *args, **kwargs)
    if kwargs.get("errors") != "coerce" or recorder is None:
        return result
    try:
        n = len(result)
        if n:
            recorder.record(CoercionEvent(
                kind="numeric",
                total=n,
                unparsed=int(result.isna().sum()),
                samples=_sample_unparsed(arg, result.isna()),
            ))
    except Exception:  # pragma: no cover - defensive
        pass
    return result


def build_pandas_proxy(pd_module: Any, recorder: Optional[CoercionRecorder]) -> Any:
    """A stand-in for the pandas module with the two coercions instrumented.

    Everything else — `DataFrame`, `read_excel`, `concat`, submodules — is the
    real attribute off the real module, so `isinstance(x, pd.DataFrame)` and
    `import pandas.api.types` keep working exactly as before. Only the two names
    that can delete data without saying so are wrapped.

    A `types.ModuleType` (not a bare object) because generated code and pandas'
    own internals both read module dunders off it.
    """
    proxy = types.ModuleType(getattr(pd_module, "__name__", "pandas"))
    proxy.__dict__.update(pd_module.__dict__)
    proxy.__wrapped_pandas__ = pd_module

    def _to_datetime(arg, *args, **kwargs):
        return guarded_to_datetime(pd_module, recorder, arg, *args, **kwargs)

    def _to_numeric(arg, *args, **kwargs):
        return guarded_to_numeric(pd_module, recorder, arg, *args, **kwargs)

    def _read_excel(*args, **kwargs):
        # DEF-012. Wrapped here for the same reason as the coercions: this is
        # where generated code actually reads the sheet. Only the single-frame
        # result is examined — `sheet_name=None` returns a dict of frames and a
        # trailer in one sheet says nothing about another, so that shape is left
        # alone rather than half-handled.
        from app.ai.code_execution.sheet_trailer import strip_total_row

        result = pd_module.read_excel(*args, **kwargs)
        if isinstance(result, dict):
            return result
        try:
            trimmed, disclosure = strip_total_row(result)
        except Exception:  # pragma: no cover - never break a read
            return result
        if disclosure is not None and recorder is not None:
            recorder.record_trailer(disclosure)
        return trimmed

    _to_datetime.__name__ = "to_datetime"
    _to_numeric.__name__ = "to_numeric"
    _read_excel.__name__ = "read_excel"
    proxy.to_datetime = _to_datetime
    proxy.to_numeric = _to_numeric
    proxy.read_excel = _read_excel
    return proxy
