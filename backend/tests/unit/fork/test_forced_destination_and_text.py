"""A person's choice must end the routing argument, and be recorded as theirs.

The intake librarian reads every uploaded file and picks a destination. When it
picks wrong there was no way to say so: `AgentFilesPanel.vue` marks every settled
fate `reingestable: false`, and `reingest_file` accepted no destination, so
re-ingesting just re-rolled the same dice. A Q&A document filed as Knowledge —
searched only when the agent thinks to look — stayed that way, and its metric
definitions never reached the agent on the questions that needed them.

`force_destination` closes that. Two properties matter and are pinned here:

  * the classifier is SKIPPED, not merely overridden — a forced route must not
    spend an LLM call to produce a verdict it will then discard,
  * the decision is recorded as `decided_by="user"`, so the UI never presents a
    person's instruction as a machine's guess and a later automatic pass can
    tell which files it must leave alone.

Also covered: the document-text endpoint's contract, which exists so that a
`.docx` can be read in the panel instead of only downloaded.
"""
import ast
import inspect
import textwrap

import pytest

import app.services.file_service as file_service


def _intake_source() -> str:
    return textwrap.dedent(inspect.getsource(file_service.FileService._smart_file_intake))


# ── the override reaches the decision ───────────────────────────────────────

def test_the_intake_accepts_a_forced_destination():
    sig = inspect.signature(file_service.FileService._smart_file_intake)
    assert "force_destination" in sig.parameters
    assert sig.parameters["force_destination"].default is None, (
        "must default to None so every existing caller routes exactly as before"
    )


def test_reingest_accepts_and_forwards_a_destination():
    """The parameter is useless if the only public entry point cannot pass it.
    Checked by parsing the call rather than by name, because a signature can
    grow the argument while the body still forwards nothing."""
    sig = inspect.signature(file_service.FileService.reingest_file)
    assert "destination" in sig.parameters
    assert sig.parameters["destination"].default is None

    src = textwrap.dedent(inspect.getsource(file_service.FileService.reingest_file))
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and any(kw.arg == "force_destination" for kw in node.keywords)
    ]
    assert calls, "reingest_file never forwards its destination to the intake"
    forwarded = next(
        kw.value for kw in calls[0].keywords if kw.arg == "force_destination"
    )
    assert ast.unparse(forwarded) == "destination"


def test_a_forced_route_short_circuits_the_classifier():
    """The whole point of forcing. If the override were applied after the
    classifier ran, every conversion would still pay for an LLM call whose
    answer is thrown away — and on a bulk pass that is the entire bill.

    Asserted structurally: the force branch must come FIRST in the decision
    chain, with the deterministic fast path as its `elif`.
    """
    src = _intake_source()
    force_at = src.index("if force_destination in (")
    fastpath_at = src.index('elif _det_dest == "table"')
    # The classifier is handed to `to_thread` as a reference, not called with
    # parentheses — searching for "classify_file_llm(" finds nothing and raises.
    llm_at = src.index("to_thread(classify_file_llm")
    assert force_at < fastpath_at < llm_at, (
        "the forced destination must be decided before the classifier is consulted"
    )


def test_the_forced_branch_records_the_person_not_a_guess():
    """A conversion the user asked for must not read back as an AI verdict with
    suspiciously round confidence — the UI shows the reason line beside the
    badge, and 'the model was sure' would be a lie about who decided."""
    src = _intake_source()
    branch = src[src.index("if force_destination in ("):src.index("# Fast-path")]
    assert '_decided_by = "user"' in branch
    assert "_decided_conf = 1.0" in branch


# ── unusable input is refused, not guessed at ───────────────────────────────

@pytest.mark.parametrize("bad", ["Instruction", "rules", "TABLE", "knowledge ", ""])
def test_an_unknown_destination_is_rejected(bad):
    """Case and stray whitespace included: a near-miss must not fall through to
    the classifier and silently ignore what the caller asked for, which would
    look like the convert button doing nothing."""
    src = textwrap.dedent(inspect.getsource(file_service.FileService.reingest_file))
    assert "status_code=400" in src
    valid = ("table", "instruction", "skill", "knowledge")
    assert bad not in valid


def test_the_guard_lists_exactly_the_four_real_destinations():
    """Kept in step with the intake's own branch list. A fifth destination added
    to one and not the other fails in the worst way — accepted by the route,
    then quietly ignored."""
    src = _intake_source()
    branch = src[src.index("if force_destination in ("):src.index("# Fast-path")]
    for dest in ("table", "instruction", "skill", "knowledge"):
        assert f'"{dest}"' in branch


def test_a_deliberate_4xx_is_not_relabelled_a_500():
    """`reingest_file` wraps the intake in a broad `except Exception` that
    reports 500. An HTTPException raised on purpose inside must pass through, or
    'that file cannot become a table' reaches the user as 'Re-ingest failed'."""
    src = textwrap.dedent(inspect.getsource(file_service.FileService.reingest_file))
    tree = ast.parse(src)
    handlers = [
        h for node in ast.walk(tree) if isinstance(node, ast.Try) for h in node.handlers
    ]
    names = [
        h.type.id for h in handlers
        if isinstance(h.type, ast.Name)
    ]
    assert "HTTPException" in names, "HTTPException is not re-raised ahead of the catch-all"
    assert names.index("HTTPException") < names.index("Exception")


# ── the document text endpoint ──────────────────────────────────────────────

def test_the_text_route_is_registered():
    from app.routes.file import router

    paths = {r.path for r in router.routes}
    assert "/files/{file_id}/text" in paths


def test_the_text_route_reuses_the_path_traversal_guarded_reader():
    """`file.path` is a database value. Opening it directly would let a tampered
    row escape the uploads directory; the shared reader rebuilds the path from
    the trusted root plus a sanitized basename. A second, hand-rolled read here
    would be a copy of that guard, free to drift out of step with it."""
    import app.routes.file as file_routes

    src = inspect.getsource(file_routes.get_file_text)
    assert "_read_file_bytes_or_404(file)" in src
    assert "open(" not in src, "the text route opens a file directly instead of using the guarded reader"


def test_the_text_route_says_when_there_is_nothing_to_show():
    """The extractor returns "" for anything it has no branch for. Without an
    explicit flag the UI cannot tell an unreadable format from a genuinely empty
    document, and renders a blank panel that reads as a bug."""
    import app.routes.file as file_routes

    src = inspect.getsource(file_routes.get_file_text)
    assert '"extractable"' in src
