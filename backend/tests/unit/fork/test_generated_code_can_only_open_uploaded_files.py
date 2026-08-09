"""A reader in generated code may open an uploaded file, and nothing else.

MEASURED DEFECT (2026-08-09, isolated container, fake key — never the live app).
`CodeSecurityVisitor` rejected a hardcoded path only when it was written as ONE
`ast.Constant` or an f-string. Any other expression producing the same string was
a different node type and passed:

    pd.read_csv('/proc/self/' + 'environ')            # ast.BinOp
    pd.read_csv(''.join(['/proc/self/','environ']))   # ast.Call
    p = '/app/backend/.env'; pd.read_csv(p)           # ast.Name

Proven end to end: the validator passed the first form with zero errors, and
`pd.read_csv('/proc/self/environ', sep='\\x00')` recovered a marker planted in the
container's LAUNCH environment. `DASH_ENCRYPTION_KEY` is set at launch, so it sits
in that same file — and that key also signs session JWTs, so one prompt-injected
`generate_df` yields every stored credential AND the ability to forge any session,
superuser included.

WHY PROVENANCE RATHER THAN A BETTER DENYLIST
A denylist of dangerous path SHAPES cannot be completed — there is always another
way to build a string. The rule is therefore inverted to the one the module's own
comment already claimed: a path is acceptable only when it DERIVES FROM
`excel_files[i].path`. That is the same default-deny shape as
`services/file_formats.py`, which exists because three hand-maintained blocklists
disagreed and let `.rtf` through as a frame of nonsense.

★If a legitimate new path shape ever needs allowing, add it to
`_is_sanctioned_path` WITH a case in `test_the_sanctioned_forms_still_work` — never
by loosening the rule. Widening this widens what generated code may open.
"""
import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.ai.code_execution.code_execution import CodeSecurityVisitor  # noqa: E402


def _file_read_errors(src: str):
    """Only the file-read errors — other rules have their own tests."""
    visitor = CodeSecurityVisitor()
    visitor.visit(ast.parse(src))
    return [e for e in visitor.errors if "file read" in e.lower()]


# ── the exploit, and every variant of it ─────────────────────────────────────
REFUSED = [
    ("the proven exfil, a BinOp path", "pd.read_csv('/proc/self/' + 'environ')"),
    ("a literal path", "pd.read_csv('/app/backend/.env')"),
    ("an f-string path", "pd.read_csv(f'/app/{name}.env')"),
    ("a path built with join()", "pd.read_csv(''.join(['/proc/self/', 'environ']))"),
    ("a name bound to a literal", "p = '/proc/self/environ'\npd.read_csv(p)"),
    # The one a naive tracker gets wrong: sanction the name, then re-point it.
    ("a sanctioned name rebound to a literal",
     "p = excel_files[0].path\np = '/app/backend/.env'\npd.read_csv(p)"),
    ("numpy reading a system file", "np.loadtxt('/etc/passwd')"),
    ("duckdb reading a literal path", "duckdb.read_csv_auto('/app/backend/.env')"),
    ("pyarrow reading a literal path", "pa.read_table('/app/secret.parquet')"),
    ("a path from a function call", "pd.read_parquet(get_path())"),
]


@pytest.mark.parametrize("label,src", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_path_that_is_not_an_uploaded_file_is_refused(label, src):
    assert _file_read_errors(src), (
        f"{label} was ACCEPTED — generated code can open an arbitrary server "
        f"file. This is the shape that reached DASH_ENCRYPTION_KEY."
    )


# ── everything the product legitimately does ─────────────────────────────────
# A fix that breaks these is not a fix: every uploaded CSV/Excel analysis goes
# through exactly these forms (see services/file_formats.CODEGEN_READERS and
# the coder's file-access rules).
ALLOWED = [
    ("the sanctioned form", "pd.read_csv(excel_files[0].path)"),
    ("indexed by a variable", "pd.read_csv(excel_files[i].path)"),
    ("read_excel with kwargs", "pd.read_excel(excel_files[2].path, sheet_name=0)"),
    ("a path held in a variable", "p = excel_files[0].path\npd.read_csv(p)"),
    ("a loop over excel_files", "for f in excel_files:\n    df = pd.read_csv(f.path)"),
    ("a reader with no path at all", "duckdb.connect()"),
    ("tsv with a separator", "pd.read_csv(excel_files[1].path, sep='\\t')"),
    ("json lines", "pd.read_json(excel_files[0].path, lines=True)"),
    ("parquet from an upload", "pd.read_parquet(excel_files[0].path)"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_the_sanctioned_forms_still_work(label, src):
    assert not _file_read_errors(src), (
        f"{label} was REFUSED — this is how the product reads uploaded files, "
        f"so refusing it breaks every file-backed analysis."
    )


# The entry point's SECOND parameter is the uploaded-file list, and
# `_invoke_generate_df` passes it POSITIONALLY — `excel_files` is a convention in
# the coder prompt, not a binding. These carry the whole `def generate_df(...)`
# because that is what teaches the validator the local name.
ALIASED_ENTRY_POINTS = [
    ("abbreviated second parameter",
     "def generate_df(ds, ex):\n    return pd.read_csv(ex[0].path)"),
    ("abbreviated, via a variable",
     "def generate_df(ds, ex):\n    p = ex[0].path\n    return pd.read_csv(p)"),
    ("abbreviated, in a loop",
     "def generate_df(ds, xl):\n    for f in xl:\n        df = pd.read_csv(f.path)"),
    ("extra injectable parameters after it",
     "def generate_df(ds_clients, files, load_step):\n    return pd.read_parquet(files[1].path)"),
]


@pytest.mark.parametrize("label,src", ALIASED_ENTRY_POINTS,
                         ids=[a[0] for a in ALIASED_ENTRY_POINTS])
def test_a_renamed_file_parameter_is_still_sanctioned(label, src):
    """★★★This is the regression the first version of the fix shipped with.

    Sanctioning the literal name `excel_files` looked equivalent to sanctioning
    the uploaded-file list, and is not: the model names that parameter freely.
    Measured by the full unit suite 2026-08-09 —
    `tests/unit/test_custom_queries.py::test_uploaded_file_reads_still_allowed`
    failed 4 of 5 cases, all of them spelling it `ex`. In production the symptom
    is the sandbox refusing correct generated code, which reads as the product
    being broken rather than as a guard being wrong.

    The rule is provenance, not spelling: parameter 1 of `generate_df` is
    sanctioned because WE bind it.
    """
    assert not _file_read_errors(src), (
        f"{label} was REFUSED — the file list is passed positionally, so this "
        f"code receives real uploads and has always run."
    )


def test_position_is_what_counts_not_the_word_excel():
    """★The negative control for the test above. Only parameter index 1 is the
    file list: index 0 is `ds_clients`, and anything later is an injectable
    resolved by name. A guard that sanctioned every parameter would pass the
    test above while authorizing far more than it should."""
    refused = _file_read_errors(
        "def generate_df(ds, ex, load_step):\n    return pd.read_csv(ds[0].path)"
    )
    assert refused, "parameter 0 holds the database clients and is not a file list"


def test_the_scanner_still_recognises_its_own_bug_shape():
    """The self-test this guard would be worthless without.

    If `FORBIDDEN_FILE_READERS` or `_FILE_IO_NAMESPACES` is ever renamed or
    emptied, every case above would pass vacuously — nothing would be checked and
    the suite would stay green over a completely open sandbox.
    """
    from app.ai.code_execution.code_execution import (
        FORBIDDEN_FILE_READERS,
        _FILE_IO_NAMESPACES,
        _SANCTIONED_FILE_COLLECTIONS,
    )
    assert "read_csv" in FORBIDDEN_FILE_READERS
    assert "pd" in _FILE_IO_NAMESPACES
    assert "excel_files" in _SANCTIONED_FILE_COLLECTIONS
    # And the check actually fires on the canonical bug.
    assert _file_read_errors("pd.read_csv('/proc/self/' + 'environ')")
