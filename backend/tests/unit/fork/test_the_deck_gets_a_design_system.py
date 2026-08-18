"""Three things a deck was promised and never got.

1. THE LAYOUT CHECK HAD NEVER RUN. `pptx_lint.check_deck_layout_detailed` is 442
   lines of working, tested code with no production caller anywhere in the tree.
   In `create_artifact`, `layout_check_result` was assigned exactly once — as
   `None` — and every consumer of it is guarded `if layout_check_result is not
   None`. So the stored verdict, the user-facing warning and the planner
   reflection were all dead, and `layout_issues` never left `[]`. A deck could
   ship with a paragraph sitting on the card below it and nothing anywhere said
   so.

2. THE REPORT'S THEME HAD NEVER REACHED GENERATION. Both call sites read
   `getattr(report, "theme", None)`. There is no `theme` attribute on `Report` —
   the model carries `theme_name` and `theme_overrides`. A `getattr` with a
   default against an attribute that does not exist is decided when you TYPE it,
   so that expression was permanently None, and the slides prompt documented a
   `report['theme']` field that was always empty.

3. THE DECK HAD NO DESIGN SYSTEM. The slides prompt described colour and layout
   in prose and left the model to invent a palette per deck.

RED PROOF — this repo has a scar from guards that could never fail (a stripper
that read the docstring quoting the very bug; a scan that removed the string it
was searching for). So for each dead-code fix below the PRE-FIX expression is
reconstructed here and fed to the same checker the live source is fed to. A
checker that cannot reject the original defect is a comment with a test's
salary, and `test_*_is_still_detected` is what forces it to prove otherwise.
"""
import ast
import inspect
import sys
import textwrap
import types
from pathlib import Path

import pytest

from app.ai.tools.implementations import create_artifact as ca

BACKEND = Path(ca.__file__).resolve().parents[4]
SOURCE = Path(ca.__file__).read_text()
TREE = ast.parse(SOURCE)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


RUN_STREAM = _func(TREE, "run_stream")


# ═══════════════════════════════════════════════════════════════════════════
# The checkers. Each is fed the live source AND a reconstruction of the
# pre-fix source, so it has to be able to say no.
# ═══════════════════════════════════════════════════════════════════════════


def layout_check_is_wired(node) -> bool:
    """True when `layout_check_result` is ever assigned something other than a
    bare None, AND the value comes from the layout check.

    The pre-fix file DOES contain the name `layout_check_result` a dozen times
    and DOES contain the word "layout check" in prose, so neither a name scan
    nor a text scan can decide this — it has to be the assigned VALUE.
    """
    called = False
    assigned_non_none = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "check_deck_layout_detailed":
                called = True
        targets = []
        if isinstance(sub, ast.Assign):
            targets = sub.targets
        elif isinstance(sub, ast.AnnAssign):
            targets = [sub.target]
        else:
            continue
        value = sub.value
        for target in targets:
            if getattr(target, "id", None) != "layout_check_result":
                continue
            if value is None:
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            assigned_non_none = True
    return called and assigned_non_none


def layout_issues_are_populated(node) -> bool:
    """True when `layout_issues` is ever assigned from the check's own issues."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "layout_issues" for t in sub.targets):
            continue
        if "issues" in ast.dump(sub.value):
            return True
    return False


def reads_a_theme_attribute_that_does_not_exist(node) -> bool:
    """True when anything reads `report.theme` — directly or through getattr."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "theme":
            if getattr(sub.value, "id", None) == "report":
                return True
        if isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "getattr":
            if len(sub.args) >= 2 and getattr(sub.args[0], "id", None) == "report":
                second = sub.args[1]
                if isinstance(second, ast.Constant) and second.value == "theme":
                    return True
    return False


def slides_prompt_takes_the_report(node) -> bool:
    args = node.args
    names = {a.arg for a in list(args.args) + list(args.kwonlyargs)}
    return "report" in names


# ═══════════════════════════════════════════════════════════════════════════
# The pre-fix reconstructions. These are the expressions that were in the file.
# ═══════════════════════════════════════════════════════════════════════════

PRE_FIX_LAYOUT = textwrap.dedent(
    '''
    async def run_stream(self):
        layout_issues: List[Dict[str, Any]] = []
        # The check's own verdict, kept separate from `layout_issues` above so
        # "we could not check" is never indistinguishable from "we checked and
        # it was clean". None when the flag is off or the check never ran.
        layout_check_result: Optional[Any] = None

        if data.mode == "slides":
            if pptx_success and pptx_path:
                try:
                    preview_images = preview_service.generate_previews(
                        pptx_path=Path(pptx_path), artifact_id=str(artifact.id),
                    )
                except Exception as e:
                    logger.warning(f"PPTX preview generation failed: {e}")

        if layout_issues:
            content["layout_issues"] = layout_issues
        if layout_check_result is not None:
            content["layout_check"] = {
                "status": layout_check_result.status,
                "reason": layout_check_result.reason,
                "slides_total": layout_check_result.slides_total,
                "slides_measured": layout_check_result.slides_measured,
                "issues": layout_issues,
            }
    '''
)

PRE_FIX_THEME = textwrap.dedent(
    '''
    def build(self):
        report_data = {
            "id": str(report.id) if report else None,
            "title": getattr(report, "title", None) if report else None,
            "theme": getattr(report, "theme", None) if report else None,
        }
    '''
)

PRE_FIX_SLIDES_SIGNATURE = textwrap.dedent(
    '''
    def _build_slides_prompt(
        self,
        user_prompt,
        title,
        viz_profiles,
        instructions_context,
        report_title,
        allow_llm_see_data,
        messages_context="",
        image_count=0,
        organization_settings=None,
        files=None,
    ):
        pass
    '''
)


# ═══════════════════════════════════════════════════════════════════════════
# RED PROOFS — the checkers must reject the code as it was.
# ═══════════════════════════════════════════════════════════════════════════


def test_the_dead_layout_check_is_still_detected():
    """The pre-fix block names `layout_check_result` twelve times and still has
    no caller. The checker has to see that, or it is measuring nothing."""
    pre = _func(ast.parse(PRE_FIX_LAYOUT), "run_stream")
    assert layout_check_is_wired(pre) is False
    assert layout_issues_are_populated(pre) is False


def test_the_nonexistent_theme_attribute_is_still_detected():
    pre = _func(ast.parse(PRE_FIX_THEME), "build")
    assert reads_a_theme_attribute_that_does_not_exist(pre) is True


def test_a_slides_prompt_that_never_sees_the_report_is_still_detected():
    pre = _func(ast.parse(PRE_FIX_SLIDES_SIGNATURE), "_build_slides_prompt")
    assert slides_prompt_takes_the_report(pre) is False


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the layout check has a caller, and it cannot cost a good deck
# ═══════════════════════════════════════════════════════════════════════════


def test_the_layout_check_is_called_and_its_verdict_kept():
    assert layout_check_is_wired(RUN_STREAM), (
        "check_deck_layout_detailed still has no production caller, or its "
        "result never reaches layout_check_result"
    )
    assert layout_issues_are_populated(RUN_STREAM), (
        "layout_issues never leaves [] — the model is never told what to reflow"
    )


def test_the_check_runs_only_after_the_deck_is_saved():
    """It must be guarded on the deck having been executed AND written. A check
    that runs before `pptx_path` is set measures a file that is not there."""
    call = None
    for node in ast.walk(RUN_STREAM):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "pptx_success" in test and "pptx_path" in test:
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                if "check_deck_layout_detailed" in body:
                    call = node
    assert call is not None, (
        "the layout check is not guarded on `pptx_success and pptx_path` — it "
        "would run against a deck that never got written"
    )


def test_a_layout_check_problem_cannot_fail_a_deck_that_built():
    """Off the critical path: the call sits inside a try whose handler swallows
    (logs) rather than re-raises, and leaves the verdict as None so nothing
    downstream can mistake a failed check for a clean deck."""
    guarded = False
    for node in ast.walk(RUN_STREAM):
        if not isinstance(node, ast.Try):
            continue
        if "check_deck_layout_detailed" not in ast.dump(
            ast.Module(body=node.body, type_ignores=[])
        ):
            continue
        assert node.handlers, "the layout check is in a try with no handler"
        for handler in node.handlers:
            dumped = ast.dump(ast.Module(body=handler.body, type_ignores=[]))
            assert "Raise(" not in dumped, (
                "the layout check re-raises — a layout problem would fail a deck "
                "that built fine"
            )
        guarded = True
    assert guarded, "the layout check is not wrapped at all"


def test_the_check_stays_off_by_default():
    """Positive control on the flag reader: the same helper must be able to say
    yes, or `False` here would prove only that it always returns False."""
    assert ca._deck_layout_check_enabled() is False

    class _Settings:
        hybrid_deck_layout_check = True

    import app.settings.config as cfg

    original = cfg.settings
    cfg.settings = _Settings()
    try:
        assert ca._deck_layout_check_enabled() is True
    finally:
        cfg.settings = original


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the theme the report actually stores
# ═══════════════════════════════════════════════════════════════════════════


def test_nothing_reads_the_theme_attribute_that_does_not_exist():
    assert reads_a_theme_attribute_that_does_not_exist(TREE) is False


def test_the_report_model_is_why():
    """The fact the defect rested on: `Report` has theme_name/theme_overrides
    and no `theme`. If that ever changes, this guard should be re-read rather
    than deleted."""
    model = (BACKEND / "app" / "models" / "report.py").read_text()
    assert "theme_name = Column(" in model
    assert "theme_overrides = Column(" in model
    assert "\n    theme = Column(" not in model


def test_the_stored_theme_name_reaches_the_payload():
    class _Report:
        theme_name = "boardroom"
        theme_overrides = {"accent": "#123456"}

    payload = ca._report_theme_payload(_Report())
    assert payload["name"] == "boardroom"
    assert payload["overrides"] == {"accent": "#123456"}


def test_a_report_with_no_theme_yields_nothing_rather_than_a_lie():
    class _Report:
        theme_name = None
        theme_overrides = {}

    assert ca._report_theme_payload(_Report()) is None
    assert ca._report_theme_payload(None) is None


def test_the_payload_is_not_taken_from_a_theme_attribute():
    """A report carrying a stray `theme` attribute and no theme_name must still
    resolve to nothing — otherwise the old read is back by another route."""

    class _Report:
        theme = "midnight"
        theme_name = None
        theme_overrides = None

    assert ca._report_theme_payload(_Report()) is None


def test_a_resolved_theme_is_carried_into_the_generated_code():
    class _Theme:
        id = "boardroom"
        name = "Boardroom"
        fonts = {"display": "Cambria"}
        palette = {"dominant": "#0F172A"}

    class _Report:
        theme_name = None
        theme_overrides = None

    payload = ca._report_theme_payload(_Report(), _Theme())
    assert payload["id"] == "boardroom"
    assert payload["fonts"] == {"display": "Cambria"}
    assert payload["palette"] == {"dominant": "#0F172A"}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the theme system in the slides prompt
# ═══════════════════════════════════════════════════════════════════════════

INDEX_LINES = "\n".join(f"theme{i}  category{i}  when to use {i}" for i in range(81))


class _FakeTheme:
    id = "boardroom"
    name = "Boardroom"
    category = "corporate"
    fonts = {"display": "Cambria", "body": "Calibri"}
    palette = {"dominant": "#0F172A"}
    when_to_use = "board and executive decks"
    prompt_text = "SPEC FOR BOARDROOM — dominant #0F172A, accent #C8A96A"


class _FakeRegistry(types.ModuleType):
    DEFAULT_THEME_ID = "boardroom"

    def __init__(self):
        super().__init__("app.ai.decks.pptx_themes")
        self.spec_calls = []
        self.resolve_calls = []

    def get(self, theme_id):
        return _FakeTheme() if theme_id == "boardroom" else None

    def resolve(self, user_text=None, report_theme_name=None, org_brand=None, agent_default=None):
        self.resolve_calls.append(
            {
                "user_text": user_text,
                "report_theme_name": report_theme_name,
                "org_brand": org_brand,
                "agent_default": agent_default,
            }
        )
        return _FakeTheme()

    def index_lines(self):
        return INDEX_LINES

    def spec_block(self, theme):
        self.spec_calls.append(theme)
        return f"THEME: {theme.id}\n{theme.prompt_text}"


@pytest.fixture
def registry():
    """Stand the registry up under its real import path.

    `from app.ai.decks import pptx_themes` resolves through sys.modules, so the
    package need not exist on disk — which is the point: a parallel agent owns
    that module and this suite must pass whether it has landed or not.
    """
    fake = _FakeRegistry()
    package = types.ModuleType("app.ai.decks")
    package.pptx_themes = fake
    package.__path__ = []
    saved = {k: sys.modules.get(k) for k in ("app.ai.decks", "app.ai.decks.pptx_themes")}
    sys.modules["app.ai.decks"] = package
    sys.modules["app.ai.decks.pptx_themes"] = fake
    try:
        yield fake
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


@pytest.fixture
def no_registry():
    """The module is NOT importable — the state the tree is in until it lands."""
    package = types.ModuleType("app.ai.decks")
    package.__path__ = []
    saved = {k: sys.modules.get(k) for k in ("app.ai.decks", "app.ai.decks.pptx_themes")}
    sys.modules["app.ai.decks"] = package
    sys.modules.pop("app.ai.decks.pptx_themes", None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class _Report:
    theme_name = "boardroom"
    theme_overrides = None
    title = "Q3 review"


def _prompt(**kwargs):
    tool = ca.CreateArtifactTool.__new__(ca.CreateArtifactTool)
    defaults = dict(
        user_prompt="a deck on Q3 revenue",
        title="Q3",
        viz_profiles=[],
        instructions_context="",
        report_title="Q3 review",
        allow_llm_see_data=True,
    )
    defaults.update(kwargs)
    return ca.CreateArtifactTool._build_slides_prompt(tool, **defaults)


def test_the_slides_prompt_takes_the_report_and_a_resolved_theme():
    sig = inspect.signature(ca.CreateArtifactTool._build_slides_prompt)
    assert "report" in sig.parameters
    assert "resolved_theme" in sig.parameters
    assert slides_prompt_takes_the_report(_func(TREE, "_build_slides_prompt"))


def test_the_resolved_theme_spec_reaches_the_prompt(registry):
    prompt = _prompt(report=_Report())
    assert "SPEC FOR BOARDROOM" in prompt
    assert "DESIGN SYSTEM" in prompt


def test_the_index_of_every_theme_reaches_the_prompt(registry):
    prompt = _prompt(report=_Report())
    for line in ("theme0  category0", "theme40  category40", "theme80  category80"):
        assert line in prompt, f"the theme index is truncated or missing: {line}"


def test_only_one_full_spec_is_injected(registry):
    """81 full specs is ~32k tokens for 80 themes the deck will not use. The
    index is the cheap half; the spec is the expensive one and there is one."""
    _prompt(report=_Report())
    assert len(registry.spec_calls) == 1


def test_the_reports_own_theme_name_is_what_gets_resolved(registry):
    _prompt(report=_Report(), user_prompt="a deck on Q3 revenue")
    assert registry.resolve_calls, "resolve() was never called"
    call = registry.resolve_calls[-1]
    assert call["report_theme_name"] == "boardroom"
    assert call["user_text"] == "a deck on Q3 revenue"
    assert call["agent_default"] == "boardroom"


def test_a_caller_that_already_resolved_is_not_resolved_again(registry):
    """The tool resolves once and hands the same object to the prompt and to the
    generated code. Resolving a second time here could name a different theme in
    the prompt than the one the deck is built with."""
    prompt = _prompt(report=_Report(), resolved_theme=_FakeTheme())
    assert registry.resolve_calls == []
    assert "SPEC FOR BOARDROOM" in prompt


def test_the_prompt_still_builds_with_no_theme_registry(no_registry):
    """The module is owned by another agent and may land late. Without it the
    prompt must be exactly what it was — not an exception, not a half-section."""
    prompt = _prompt(report=_Report())
    assert "Role: presentation author using python-pptx." in prompt
    assert "DESIGN SYSTEM" not in prompt
    assert "SPEC FOR BOARDROOM" not in prompt


def test_a_registry_that_explodes_costs_only_the_theme(registry):
    def _boom(*_a, **_k):
        raise RuntimeError("registry is broken")

    registry.resolve = _boom
    registry.index_lines = _boom
    prompt = _prompt(report=_Report())
    assert "Role: presentation author using python-pptx." in prompt
    assert "DESIGN SYSTEM" not in prompt


def test_the_archetype_is_bound_to_the_data_shape(registry):
    prompt = _prompt(report=_Report())
    shape_rules = [
        "ONE metric",
        "2-4 categories",
        "A time series",
        "3 comparable entities",
        "DIFFERENT scales",
        "No data at all",
    ]
    missing = [rule for rule in shape_rules if rule not in prompt]
    assert not missing, f"the data-shape rule is missing cases: {missing}"
    assert "NEVER two series on one axis" in prompt


def test_the_prompt_no_longer_documents_an_always_empty_theme_field(registry):
    """The old prompt promised `report: Dict with 'id', 'title', 'theme'` while
    that key was permanently None. Whatever it now says about the field has to
    describe what the code is actually handed."""
    prompt = _prompt(report=_Report())
    assert "report['theme']" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# The resolution helper the tool itself uses
# ═══════════════════════════════════════════════════════════════════════════


def test_resolve_returns_none_without_the_registry(no_registry):
    assert ca._resolve_deck_theme(user_text="anything", report=_Report()) is None


def test_resolve_uses_the_registry_when_it_is_there(registry):
    theme = ca._resolve_deck_theme(user_text="dark and formal", report=_Report())
    assert getattr(theme, "id", None) == "boardroom"


def test_an_org_brand_is_read_from_a_dict_config_too(registry):
    """★`getattr` against a dict MISSES rather than raising — the bug class that
    shipped `{'name': …}:None` into a prompt. Both shapes are read."""

    class _Settings:
        config = {"branding": {"accent_color": "#2563eb"}}

    ca._resolve_deck_theme(user_text="", report=_Report(), organization_settings=_Settings())
    assert registry.resolve_calls[-1]["org_brand"] == "#2563eb"

    class _Obj:
        class config:
            class branding:
                accent_color = "#ff0000"
                product_name = None

    ca._resolve_deck_theme(user_text="", report=_Report(), organization_settings=_Obj())
    assert registry.resolve_calls[-1]["org_brand"] == "#ff0000"


def test_an_unreadable_org_config_is_not_a_failure():
    class _Explodes:
        @property
        def config(self):
            raise RuntimeError("no settings row")

    assert ca._org_brand_hint(_Explodes()) is None
    assert ca._org_brand_hint(None) is None
