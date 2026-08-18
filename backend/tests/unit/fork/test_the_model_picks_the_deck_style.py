"""The model picks the deck's design system, and the pick is visible.

Before this, a deck's look was decided entirely behind the model's back.
`create_artifact` resolved a theme from the prompt text, the report's saved
theme name and the org brand — a keyword match over the request — and the model,
which had just been handed the index of all 81 themes in its own instructions,
had nowhere to say which one it wanted. It could describe a look in prose and
watch the keyword matcher pick something else. Nothing anywhere recorded which
theme a deck ended up in or whether anybody chose it.

Three things had to become true:

1. The model can NAME a theme (`theme_id`), and a valid name WINS. An id it
   gets wrong must not cost the deck — the deck falls back to resolution and
   says that it did, because a silent fallback teaches the model nothing.

2. The choice is RECORDED — id, display name, and the method that produced it —
   on the artifact, in the tool observation, and in the sentence the agent
   reads.

3. The saved deck gets the theme's furniture painted on and its prohibitions
   enforced, in that order, and neither pass may ever cost a deck that built.

RED PROOF — this repo has scars from guards that could never fail (a stripper
that read the docstring quoting its own bug; a layout check with no caller that
named itself a dozen times). So every checker below is fed a reconstruction of
the PRE-CHANGE source as well as the live source, and `*_is_still_detected`
forces it to prove it can say no.
"""
import ast
import sys
import textwrap
import types
from pathlib import Path

import pytest

from app.ai.tools.implementations import create_artifact as ca
from app.ai.tools.schemas import create_artifact as ca_schema
from app.ai.tools.schemas.create_artifact import CreateArtifactInput

SOURCE = Path(ca.__file__).read_text()
TREE = ast.parse(SOURCE)
SCHEMA_SOURCE = Path(ca_schema.__file__).read_text()
SCHEMA_TREE = ast.parse(SCHEMA_SOURCE)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


RUN_STREAM = _func(TREE, "run_stream")


def _dump(nodes) -> str:
    return ast.dump(ast.Module(body=list(nodes), type_ignores=[]))


# ═══════════════════════════════════════════════════════════════════════════
# The checkers. Each is fed the live source AND a reconstruction of the
# pre-change source, so it has to be able to say no.
# ═══════════════════════════════════════════════════════════════════════════


def model_choice_is_wired(node) -> bool:
    """True when the deck's theme comes from a selector that is GIVEN the
    model's `theme_id`.

    ★Not a name scan. The pre-change source already contained `deck_theme`, the
    word `theme` sixty times and a call that resolves a theme — what it did not
    contain was the model's own argument reaching that call. So the test is the
    argument, on the call whose result becomes `deck_theme`.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        targets = _dump(sub.targets) if isinstance(sub.targets, list) else ""
        if "deck_theme" not in targets:
            continue
        for call in ast.walk(sub.value):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name != "_select_deck_theme":
                continue
            for kw in call.keywords:
                if kw.arg == "requested_theme_id":
                    return True
    return False


def schema_offers_theme_id(tree) -> bool:
    """True when CreateArtifactInput declares a `theme_id` field."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "CreateArtifactInput":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and getattr(stmt.target, "id", None) == "theme_id":
                return True
    return False


def _pass_call(node, func_name):
    """The Call node for one post-pass, or None."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
            if name == func_name:
                return sub
    return None


def post_passes_are_wired(node) -> bool:
    """True when BOTH post-passes are actually called."""
    return (
        _pass_call(node, "paint_theme_furniture") is not None
        and _pass_call(node, "enforce_theme_rules") is not None
    )


def a_pass_cannot_fail_the_deck(node, func_name) -> bool:
    """True when the named pass sits inside a try whose handlers never re-raise."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Try):
            continue
        if func_name not in _dump(sub.body):
            continue
        if not sub.handlers:
            return False
        for handler in sub.handlers:
            if "Raise(" in _dump(handler.body):
                return False
        return True
    return False


def passes_run_only_on_a_saved_deck(node) -> bool:
    """True when both passes are under a guard naming pptx_success AND pptx_path.

    A pass that runs before `pptx_path` is recorded is a pass operating on a
    file that may not be there — and worse, one that sits on the path a deck
    has to survive to ship.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        test = ast.dump(sub.test)
        if "pptx_success" not in test or "pptx_path" not in test:
            continue
        body = _dump(sub.body)
        if "paint_theme_furniture" in body and "enforce_theme_rules" in body:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# The pre-change reconstructions — the code that was actually in the file.
# ═══════════════════════════════════════════════════════════════════════════

PRE_CHANGE_SELECTION = textwrap.dedent(
    '''
    async def run_stream(self):
        # The deck's design system, resolved once: the same object is injected
        # into the slides prompt below and handed to the generated code as
        # `report['theme']`.
        deck_theme = (
            _resolve_deck_theme(
                user_text=data.prompt or "",
                report=report,
                organization_settings=organization_settings,
            )
            if data.mode == "slides"
            else None
        )
        prompt = self._build_prompt(
            user_prompt=data.prompt,
            mode=data.mode,
            report=report,
            resolved_theme=deck_theme,
        )
    '''
)

PRE_CHANGE_SCHEMA = textwrap.dedent(
    '''
    class CreateArtifactInput(BaseModel):
        prompt: str = Field(..., description="## Theme\\nColors, dark/light, spacing, typography, design feel.")
        title: Optional[str] = Field(None, description="Title for the artifact")
        mode: Literal["page", "slides"] = Field(default="page", description="'page' or 'slides'")
        visualization_ids: List[str] = Field(default_factory=list, description="Ordered viz ids")
    '''
)

PRE_CHANGE_POST_PASSES = textwrap.dedent(
    '''
    async def run_stream(self):
        if pptx_success:
            pptx_path = str(output_path)

        if pptx_success and pptx_path:
            try:
                preview_images = preview_service.generate_previews(
                    pptx_path=Path(pptx_path), artifact_id=str(artifact.id),
                )
            except Exception as e:
                logger.warning(f"PPTX preview generation failed: {e}")
    '''
)

PRE_CHANGE_PASSES_BEFORE_SAVE = textwrap.dedent(
    '''
    async def run_stream(self):
        # The wrong shape: painted and enforced before the deck is even known
        # to have been written, and re-raising, so a theme problem kills a deck.
        paint_theme_furniture(output_path, theme, layout, logger=logger)
        enforce_theme_rules(output_path, theme, logger=logger)
        if pptx_success:
            pptx_path = str(output_path)
    '''
)


# ═══════════════════════════════════════════════════════════════════════════
# RED PROOFS — the checkers must reject the code as it was.
# ═══════════════════════════════════════════════════════════════════════════


def test_a_theme_the_model_cannot_choose_is_still_detected():
    """The pre-change block resolves a theme, assigns `deck_theme` and passes it
    to the prompt — everything except letting the model have a say. If the
    checker cannot fail this, it is measuring the word 'theme'."""
    pre = _func(ast.parse(PRE_CHANGE_SELECTION), "run_stream")
    assert model_choice_is_wired(pre) is False


def test_a_schema_with_no_theme_field_is_still_detected():
    """The pre-change schema TALKS about themes at length inside `prompt` — a
    text scan for 'theme' passes it. Only the declared field decides."""
    assert "Theme" in PRE_CHANGE_SCHEMA
    assert schema_offers_theme_id(ast.parse(PRE_CHANGE_SCHEMA)) is False


def test_unwired_post_passes_are_still_detected():
    pre = _func(ast.parse(PRE_CHANGE_POST_PASSES), "run_stream")
    assert post_passes_are_wired(pre) is False


def test_passes_on_the_critical_path_are_still_detected():
    """Both passes ARE called here, so a 'are they called' check would pass it.
    They run before the deck is known saved and outside any try — which is the
    shape that costs a deck that built."""
    pre = _func(ast.parse(PRE_CHANGE_PASSES_BEFORE_SAVE), "run_stream")
    assert post_passes_are_wired(pre) is True
    assert passes_run_only_on_a_saved_deck(pre) is False
    assert a_pass_cannot_fail_the_deck(pre, "paint_theme_furniture") is False
    assert a_pass_cannot_fail_the_deck(pre, "enforce_theme_rules") is False


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the model can name a theme, and a valid name wins
# ═══════════════════════════════════════════════════════════════════════════


def test_the_schema_offers_the_model_a_theme_id():
    assert schema_offers_theme_id(SCHEMA_TREE), (
        "CreateArtifactInput has no `theme_id` field — the model has nowhere to "
        "name the theme it was just shown the index of"
    )


def test_the_theme_field_is_optional_and_describes_the_index():
    field = CreateArtifactInput.model_fields["theme_id"]
    assert field.default is None, "a required theme_id would fail every page-mode call"
    description = (field.description or "").lower()
    for word in ("index", "id"):
        assert word in description, f"the description never mentions {word!r}"


def test_a_theme_the_model_named_wins_over_resolution():
    """Explicit beats deterministic. The prompt text and the report's saved
    theme both name OTHER themes here, so a selector that ignored `theme_id`
    would return one of those and this would fail."""

    class _Report:
        theme_name = "midnight-pitch"
        theme_overrides = None

    theme, choice = ca._select_deck_theme(
        requested_theme_id="mckinsey-style",
        user_text="build me a boardroom style deck",
        report=_Report(),
    )
    assert theme is not None
    assert theme.id == "mckinsey-style"
    assert choice["id"] == "mckinsey-style"
    assert choice["name"] == "McKinsey Style"
    assert choice["method"] == "model"
    assert choice["requested_id"] == "mckinsey-style"


def test_resolution_still_decides_when_the_model_says_nothing():
    """Positive control on the fallback: with no theme_id the old path must
    still run, or the test above would only prove the selector always echoes
    its argument."""

    class _Report:
        theme_name = "midnight-pitch"
        theme_overrides = None

    theme, choice = ca._select_deck_theme(requested_theme_id=None, report=_Report())
    assert theme is not None
    assert theme.id == "midnight-pitch"
    assert choice["method"] == "resolved"
    assert "requested_id" not in choice


def test_an_unknown_id_falls_back_instead_of_failing_the_deck():
    class _Report:
        theme_name = "midnight-pitch"
        theme_overrides = None

    theme, choice = ca._select_deck_theme(
        requested_theme_id="mckinsy-styel", report=_Report()
    )
    assert theme is not None, "a typo cost the deck its theme entirely"
    assert theme.id == "midnight-pitch", "the fallback did not run"
    assert choice["method"] == "resolved_unknown_id", (
        "a fallback that records itself as a deliberate choice is a lie the "
        "console cannot see through"
    )
    assert choice["requested_id"] == "mckinsy-styel"


@pytest.mark.parametrize(
    "sent",
    [
        "mckinsey-style",
        "McKinsey Style",
        "MCKINSEY_STYLE",
        "  mckinsey style  ",
        "mckinsey",
        "McKinsey",
        "use the mckinsey-style theme please",
    ],
)
def test_the_shapes_models_actually_send_still_resolve(sent):
    """★`get()` is an exact id lookup and models do not send exact ids. This
    instance measured 79% of live `clarify` calls thrown away purely on
    argument shape — a deck theme must not repeat it."""
    theme, choice = ca._select_deck_theme(requested_theme_id=sent)
    assert theme.id == "mckinsey-style", f"{sent!r} lost the model its choice"
    assert choice["method"] == "model"


def test_a_display_name_from_the_index_resolves():
    theme, choice = ca._select_deck_theme(requested_theme_id="Midnight Pitch")
    assert theme.id == "midnight-pitch"
    assert choice["method"] == "model"


def test_nonsense_is_not_quietly_turned_into_the_default_theme():
    """★The lookup's last tier uses `resolve()` for its alias table, and
    `resolve()` NEVER returns None — it falls back to `boardroom`. Without the
    verification step, every unknown id would come back as a confident,
    deliberate-looking choice of the default theme."""
    assert ca._theme_by_id(ca._load_pptx_themes(), "zzzz not a theme at all") is None
    assert ca._theme_by_id(ca._load_pptx_themes(), "qqq") is None
    # Positive control: the same function does say yes to a real id.
    assert ca._theme_by_id(ca._load_pptx_themes(), "boardroom").id == "boardroom"


def test_a_theme_named_inside_a_sentence_is_honoured_not_defaulted():
    theme, choice = ca._select_deck_theme(requested_theme_id="please use the atelier look")
    assert theme.id == "atelier"
    assert choice["method"] == "model"


def test_no_registry_means_exactly_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(ca, "_load_pptx_themes", lambda: None)
    assert ca._select_deck_theme(requested_theme_id="mckinsey-style") == (None, None)


def test_the_selection_is_wired_into_the_tool():
    assert model_choice_is_wired(RUN_STREAM), (
        "the model's theme_id never reaches the selector — the field exists and "
        "is ignored, which is worse than not having it"
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1b — the shapes arrive intact through the schema
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "sent,expected",
    [
        ("mckinsey-style", "mckinsey-style"),
        ("  McKinsey Style  ", "McKinsey Style"),
        ({"theme_id": "atelier"}, "atelier"),
        ({"name": "Atelier"}, "Atelier"),
        (["atelier"], "atelier"),
        ('{"theme_id": "atelier"}', "atelier"),
        ("", None),
        ("   ", None),
        (None, None),
        (123, None),
        ({}, None),
        ([], None),
    ],
)
def test_the_field_accepts_what_models_send(sent, expected):
    """A theme is a preference, so an unusable shape must degrade to "no
    preference" — never to a validation error that throws the whole deck away."""
    data = CreateArtifactInput(prompt="build a deck", mode="slides", theme_id=sent)
    assert data.theme_id == expected


def test_an_omitted_theme_id_is_none():
    assert CreateArtifactInput(prompt="build a deck").theme_id is None


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the choice is visible
# ═══════════════════════════════════════════════════════════════════════════


def test_the_choice_is_recorded_on_the_artifact_and_in_the_observation():
    body = _dump(RUN_STREAM.body)
    assert "'deck_theme'" in body or '"deck_theme"' in body
    for target in ('content["deck_theme"]', 'observation["deck_theme"]', 'output["deck_theme"]'):
        assert target in SOURCE, f"{target} is never written — the choice is invisible"


def test_the_summary_names_the_theme_and_how_it_was_chosen():
    """The agent reads `summary`. A theme mentioned nowhere in it is a theme
    nobody can hold to account, and a model that is never told its id was
    rejected will send the same wrong id forever."""
    assert "Deck theme:" in SOURCE
    assert "resolved_unknown_id" in SOURCE
    # The wording must distinguish the three methods, not print one sentence.
    assert "the theme you named" in SOURCE
    assert "is not a theme in the index" in SOURCE
    assert "no theme_id was given" in SOURCE


def test_the_recorded_method_can_only_be_one_of_three_known_values():
    for kwargs, expected in (
        ({"requested_theme_id": "mckinsey-style"}, "model"),
        ({"requested_theme_id": "not-a-real-theme"}, "resolved_unknown_id"),
        ({"requested_theme_id": None}, "resolved"),
    ):
        _, choice = ca._select_deck_theme(**kwargs)
        assert choice["method"] == expected


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the two post-passes
# ═══════════════════════════════════════════════════════════════════════════


def test_both_post_passes_are_called():
    assert post_passes_are_wired(RUN_STREAM), (
        "paint_theme_furniture / enforce_theme_rules have no caller — the same "
        "dead-code shape the layout check shipped in for months"
    )


def test_the_passes_run_only_on_a_deck_that_was_saved():
    assert passes_run_only_on_a_saved_deck(RUN_STREAM)


def test_enforcement_runs_last():
    """The painter adds shapes; enforcement is what squares and de-shadows them.
    Run the other way round, the pass that guarantees the theme's rules is the
    one thing on the deck exempt from them."""
    paint = _pass_call(RUN_STREAM, "paint_theme_furniture")
    enforce = _pass_call(RUN_STREAM, "enforce_theme_rules")
    assert paint.lineno < enforce.lineno, "prohibitions are enforced before the painter runs"


def test_the_passes_run_before_the_previews_and_the_layout_check():
    """LibreOffice renders the file as it stands and the layout check measures
    it as it stands. Painting after either means shipping images of a deck
    nobody gets, and a verdict on a file two passes out of date."""
    enforce = _pass_call(RUN_STREAM, "enforce_theme_rules")
    previews = _pass_call(RUN_STREAM, "generate_previews")
    layout = _pass_call(RUN_STREAM, "check_deck_layout_detailed")
    assert previews is not None and layout is not None
    assert enforce.lineno < previews.lineno
    assert enforce.lineno < layout.lineno


@pytest.mark.parametrize("func", ["paint_theme_furniture", "enforce_theme_rules"])
def test_neither_pass_can_fail_a_deck_that_built(func):
    assert a_pass_cannot_fail_the_deck(RUN_STREAM, func), (
        f"{func} is unwrapped or re-raises — an ImportError from the module "
        "would take a deck that built with it"
    )


def test_the_new_switches_default_on_and_can_be_switched_off():
    """Corrective passes, so ON. Positive control on the reader itself: it must
    be able to say False, or the True above proves only that it always says
    True. ★"off" is TRUTHY in Python — this repo shipped a deny that was
    allowed three times over exactly that."""
    assert ca._deck_theme_furniture_enabled() is True
    assert ca._deck_theme_enforcement_enabled() is True

    class _Settings:
        hybrid_deck_theme_furniture = False
        hybrid_deck_theme_enforcement = "off"

    import app.settings.config as cfg

    original = cfg.settings
    cfg.settings = _Settings()
    try:
        assert ca._deck_theme_furniture_enabled() is False
        assert ca._deck_theme_enforcement_enabled() is False
    finally:
        cfg.settings = original


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3b — behaviour, not shape: run the block the tool actually contains
# ═══════════════════════════════════════════════════════════════════════════


def _extract_post_pass_block() -> str:
    """The live `if pptx_success and pptx_path and deck_theme ...` block, as
    runnable source. Extracted from the file rather than retyped — a copy of
    the block would pass forever after the real one was deleted."""
    # ★The INNERMOST match. `if data.mode == "slides":` also contains the calls,
    # and lifting that whole branch out drags an `async for` with it — which
    # fails as a SyntaxError and reads like the block being malformed.
    candidates = [
        node
        for node in ast.walk(RUN_STREAM)
        if isinstance(node, ast.If)
        and any(
            isinstance(n, ast.Name) and n.id == "deck_theme"
            for n in ast.walk(node.test)
        )
        and "paint_theme_furniture" in _dump(node.body)
    ]
    if not candidates:
        raise AssertionError("the post-pass block is gone from create_artifact")
    node = min(candidates, key=lambda n: n.end_lineno - n.lineno)
    segment = " " * node.col_offset + ast.get_source_segment(SOURCE, node)
    return textwrap.dedent(segment)


class _FakeTheme:
    id = "boardroom"
    name = "Boardroom"
    category = "corporate"
    fonts = ("Cambria", "Calibri")
    palette = {"background": "#FFFFFF"}
    when_to_use = "board decks"


def _run_post_passes(monkeypatch, *, furniture, enforcement):
    """Execute the real block with both post-pass modules faked."""
    motifs = types.ModuleType("app.ai.decks.motifs")
    motifs.paint_theme_furniture = furniture
    executor = types.ModuleType("app.ai.code_execution.pptx_executor")
    executor.enforce_theme_rules = enforcement
    monkeypatch.setitem(sys.modules, "app.ai.decks.motifs", motifs)
    monkeypatch.setitem(sys.modules, "app.ai.code_execution.pptx_executor", executor)

    namespace = {
        "pptx_success": True,
        "pptx_path": "/tmp/deck.pptx",
        "deck_theme": _FakeTheme(),
        "furniture_result": None,
        "enforcement_result": None,
        "logger": ca.logger,
        "_theme_as_dict": ca._theme_as_dict,
        "_theme_layout_for": ca._theme_layout_for,
        "_deck_theme_furniture_enabled": ca._deck_theme_furniture_enabled,
        "_deck_theme_enforcement_enabled": ca._deck_theme_enforcement_enabled,
    }
    exec(compile(_extract_post_pass_block(), "<post_passes>", "exec"), namespace)
    return namespace


def test_both_passes_report_what_they_did(monkeypatch):
    calls = {}

    def _paint(path, theme, layout, logger=None):
        calls["paint"] = (path, theme, layout)
        return {"status": "painted", "shapes_added": 4}

    def _enforce(path, theme, logger=None):
        calls["enforce"] = (path, theme)
        return {"status": "enforced", "shapes_changed": 2}

    ns = _run_post_passes(monkeypatch, furniture=_paint, enforcement=_enforce)

    assert ns["furniture_result"] == {"status": "painted", "shapes_added": 4}
    assert ns["enforcement_result"] == {"status": "enforced", "shapes_changed": 2}
    # The contract is `theme: dict` — a dataclass would make every lookup
    # inside the passes a getattr that answers None instead of raising.
    assert isinstance(calls["paint"][1], dict)
    assert calls["paint"][1]["id"] == "boardroom"
    assert isinstance(calls["paint"][2], dict)
    assert isinstance(calls["enforce"][1], dict)
    assert calls["paint"][0] == "/tmp/deck.pptx"


@pytest.mark.parametrize("which", ["furniture", "enforcement"])
def test_a_pass_that_raises_cannot_cost_the_deck(monkeypatch, which):
    """Both are contracted never to raise. They are wrapped anyway, because an
    ImportError comes from the import and not from the call — and because a
    deck that built must ship whatever a design pass does."""

    def _boom(*args, **kwargs):
        raise RuntimeError("python-pptx exploded")

    ok_paint = lambda *a, **k: {"status": "painted"}  # noqa: E731
    ok_enforce = lambda *a, **k: {"status": "enforced"}  # noqa: E731

    ns = _run_post_passes(
        monkeypatch,
        furniture=_boom if which == "furniture" else ok_paint,
        enforcement=_boom if which == "enforcement" else ok_enforce,
    )

    failed = ns[f"{which}_result"]
    assert failed["status"] == "unavailable", (
        "a pass that could not finish is indistinguishable from one that ran clean"
    )
    assert "python-pptx exploded" in failed["reason"]
    # The deck itself is untouched, and the OTHER pass still ran.
    assert ns["pptx_path"] == "/tmp/deck.pptx"
    assert ns["pptx_success"] is True
    other = "enforcement" if which == "furniture" else "furniture"
    assert ns[f"{other}_result"]["status"] in ("painted", "enforced")


def test_a_missing_pass_module_cannot_cost_the_deck(monkeypatch):
    """The modules are new and optional. An ImportError is raised by the import
    statement, which is inside the try for exactly this reason."""
    monkeypatch.setitem(sys.modules, "app.ai.decks.motifs", None)
    monkeypatch.setitem(sys.modules, "app.ai.code_execution.pptx_executor", None)
    namespace = {
        "pptx_success": True,
        "pptx_path": "/tmp/deck.pptx",
        "deck_theme": _FakeTheme(),
        "furniture_result": None,
        "enforcement_result": None,
        "logger": ca.logger,
        "_theme_as_dict": ca._theme_as_dict,
        "_theme_layout_for": ca._theme_layout_for,
        "_deck_theme_furniture_enabled": ca._deck_theme_furniture_enabled,
        "_deck_theme_enforcement_enabled": ca._deck_theme_enforcement_enabled,
    }
    exec(compile(_extract_post_pass_block(), "<post_passes>", "exec"), namespace)
    assert namespace["furniture_result"]["status"] == "unavailable"
    assert namespace["enforcement_result"]["status"] == "unavailable"
    assert namespace["pptx_path"] == "/tmp/deck.pptx"


def test_a_switched_off_pass_did_not_run_rather_than_ran_clean(monkeypatch):
    """★The distinction the layout check paid for: None means "did not run".
    A pass that was never enabled must not leave a record that reads like a
    clean result."""

    class _Settings:
        hybrid_deck_theme_furniture = False
        hybrid_deck_theme_enforcement = False

    import app.settings.config as cfg

    monkeypatch.setattr(cfg, "settings", _Settings())
    ns = _run_post_passes(
        monkeypatch,
        furniture=lambda *a, **k: {"status": "painted"},
        enforcement=lambda *a, **k: {"status": "enforced"},
    )
    assert ns["furniture_result"] is None
    assert ns["enforcement_result"] is None


def test_no_theme_means_no_passes(monkeypatch):
    """Nothing to paint and nothing to enforce when the registry gave no theme —
    and the guard must be the theme, not a truthiness accident."""
    motifs = types.ModuleType("app.ai.decks.motifs")
    motifs.paint_theme_furniture = lambda *a, **k: pytest.fail("painted without a theme")
    executor = types.ModuleType("app.ai.code_execution.pptx_executor")
    executor.enforce_theme_rules = lambda *a, **k: pytest.fail("enforced without a theme")
    monkeypatch.setitem(sys.modules, "app.ai.decks.motifs", motifs)
    monkeypatch.setitem(sys.modules, "app.ai.code_execution.pptx_executor", executor)
    namespace = {
        "pptx_success": True,
        "pptx_path": "/tmp/deck.pptx",
        "deck_theme": None,
        "furniture_result": None,
        "enforcement_result": None,
        "logger": ca.logger,
        "_theme_as_dict": ca._theme_as_dict,
        "_theme_layout_for": ca._theme_layout_for,
        "_deck_theme_furniture_enabled": ca._deck_theme_furniture_enabled,
        "_deck_theme_enforcement_enabled": ca._deck_theme_enforcement_enabled,
    }
    exec(compile(_extract_post_pass_block(), "<post_passes>", "exec"), namespace)
    assert namespace["furniture_result"] is None
    assert namespace["enforcement_result"] is None


def test_a_missing_layout_is_an_empty_dict_not_a_crash():
    assert ca._theme_layout_for("no-such-theme") == {}
    assert ca._theme_layout_for(None) == {}
    assert ca._theme_layout_for("") == {}
