"""Adding a reference in the knowledge explorer must not delete it again.

The explorer's autosave (`saveMeta`) PUT the new reference correctly, then
re-seeded the open pane from the refreshed LIST row. Those rows are the light
projection, which drops `text` and `references` by design — so `syncDraft` set
`draft.references` to `[]` and `draft.text` to `''`, and the draft is exactly
what the NEXT autosave sends. `replace_for_instruction` treats a non-None
`references` as replace-all, so the following metadata change deleted every
reference for real, and `text: ""` was applied with no guard.

The same projection quietly emptied the tree: every table node filtered list
rows by their `references`, so "instructions under this table" was always zero
and each node read "No rules attached" however many were pinned. The light row
now carries `table_ref_ids` and the grouping reads that.

★These are TEXT scans of a .vue file, so every one of them strips comments
first. The fix's own comments quote the broken shapes verbatim; a scanner that
reads them reports a defect that is not there.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
HELPERS = REPO / "frontend" / "composables" / "useInstructionHelpers.ts"
SCHEMA = REPO / "backend" / "app" / "schemas" / "instruction_schema.py"
SERVICE = REPO / "backend" / "app" / "services" / "instruction_service.py"


def strip_comments(src: str) -> str:
    """Drop HTML comments and whole-line JS comments.

    Deliberately conservative: only comments occupying a line of their own are
    removed, so nothing inside a string literal (a `//` in a URL, say) can be
    eaten and turn real code invisible to the scan.
    """
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    kept = []
    for line in src.splitlines():
        bare = line.strip()
        if bare.startswith("//") or bare.startswith("/*") or bare.startswith("*"):
            continue
        kept.append(line)
    return "\n".join(kept)


@pytest.fixture(scope="module")
def explorer() -> str:
    return strip_comments(EXPLORER.read_text(encoding="utf-8"))


def _function_body(src: str, header: str) -> str:
    """Source of the arrow function starting at `header`, by brace balance."""
    start = src.index(header)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


# ── The clobber itself ───────────────────────────────────────────────────────

def test_savemeta_does_not_reseed_the_open_pane_from_a_list_row(explorer):
    """The PUT response is the only post-save shape carrying text+references."""
    body = _function_body(explorer, "const saveMeta = async () => {")
    assert "allInstructions.value.find" not in body, (
        "saveMeta is re-seeding the detail pane from the tree cache — those rows "
        "are the light projection and carry neither `text` nor `references`."
    )


def test_savemeta_syncs_the_draft_from_the_put_response(explorer):
    """Not syncing at all would leave the draft stale; syncing from the wrong
    place is the bug. It has to be the response."""
    body = _function_body(explorer, "const saveMeta = async () => {")
    assert "syncDraft" in body, "saveMeta never re-syncs the draft after saving"
    put_index = body.index("data.value")
    assert body.index("syncDraft") > put_index, (
        "syncDraft runs before the PUT response is merged into `detail`"
    )


def test_the_editor_sees_its_references_exactly_once(explorer):
    """Chips and the picker both rendered the same references. The picker shows
    its own selection, so for an editor the chips beside it were a duplicate."""
    chip = re.search(r"""<span v-for="\(r, i\) in ([^"]+)"[^>]*'ref'\+i""", explorer)
    assert chip, "the reference chip loop is gone entirely"
    assert "metaEditable" in chip.group(1), (
        "the reference chips render for editors as well as read-only viewers, "
        f"beside the picker that already shows them: {chip.group(1)!r}"
    )


# ── The tree that always read empty ──────────────────────────────────────────

def test_the_tree_files_instructions_by_the_light_rows_table_ids(explorer):
    """`listForTable` filters LIST rows, which carry `table_ref_ids` and no
    `references`. Reading only the latter is what emptied every table node."""
    line = next(l for l in explorer.splitlines() if l.startswith("const listForTable"))
    assert "tableRefIds" in line, (
        "listForTable still reads `references` off a light list row: " + line.strip()
    )
    helper = _function_body(explorer, "const tableRefIds = (ins: any): string[] => {")
    assert "table_ref_ids" in helper, "tableRefIds does not read the light field"
    assert "references" in helper, (
        "tableRefIds must still handle a hydrated row — the open instruction "
        "carries `references` and no `table_ref_ids`."
    )


def test_the_light_schema_declares_the_referenced_table_ids():
    src = SCHEMA.read_text(encoding="utf-8")
    start = src.index("class InstructionListItemSchema")
    end = src.index("class ", start + 10)
    assert "table_ref_ids" in src[start:end], (
        "InstructionListItemSchema does not carry table_ref_ids"
    )


def test_the_service_populates_the_referenced_table_ids():
    src = SERVICE.read_text(encoding="utf-8")
    assert "table_ref_ids=table_ref_ids.get(" in src, (
        "the light projection declares table_ref_ids but never fills it — a "
        "field that is always [] files every instruction nowhere."
    )
    assert 'InstructionReference.object_type == "datasource_table"' in src, (
        "the flat SELECT that feeds table_ref_ids is missing"
    )


def test_the_client_type_knows_the_field():
    src = HELPERS.read_text(encoding="utf-8")
    # ★The brace, not the prefix: `export interface Instruction` also matches
    # `InstructionLabel`, whose body ends six lines later and carries nothing.
    start = src.index("export interface Instruction {")
    end = src.index("\n}", start)
    assert "table_ref_ids" in src[start:end], (
        "the Instruction interface omits table_ref_ids, so every reader of a "
        "light row is typed as if the field did not exist"
    )


# ── The scanner itself ───────────────────────────────────────────────────────

def test_the_comment_stripper_removes_the_explanations_it_must(explorer):
    """A guard whose own scan reads the fix's prose can never fail. The fix
    quotes `allInstructions.value.find` and `references` in its comments."""
    raw = EXPLORER.read_text(encoding="utf-8")
    assert "instead (what this used to do) silently emptied both" in raw, (
        "the explanatory comment this test guards against reading is gone — "
        "re-read the stripper before trusting the scans above"
    )
    assert "instead (what this used to do) silently emptied both" not in explorer
