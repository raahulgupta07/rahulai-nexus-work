from typing import Any, Optional, Literal, Dict, List
from pydantic import BaseModel, Field, field_validator, model_validator

from ._lenient import _maybe_json


#: The keys a model reaches for when it sends the theme as an object instead of
#: a bare string. `_lenient` exists because this instance measured `clarify`
#: failing 79% of live calls purely on argument SHAPE; a deck theme is a design
#: choice, so a shape it does not recognise must degrade to "no preference"
#: rather than throw the whole create_artifact call away.
_THEME_OBJECT_KEYS = ("theme_id", "id", "theme", "name", "style", "value")

#: The one answer that is not a theme id. A deck must say which design system it
#: is built in; `"auto"` is how the model says "somebody else has already
#: decided" — the report's saved theme, the organisation brand, or the default.
#: It is a real answer, so it survives coercion as the exact lowercase string
#: rather than degrading to None, and the model validator below accepts it.
_THEME_AUTO = "auto"


def _coerce_theme_id(value: Any) -> Optional[str]:
    """Reduce whatever arrived to a single non-empty string, or None.

    Never raises and never rejects: an unusable value becomes None, which means
    "the model expressed no preference" and hands selection back to the
    deterministic resolver. Matching the string to a real theme is the
    implementation's job — this only fixes the container.

    One string is not a theme id and is not "no preference": the sentinel
    ``"auto"`` (case-insensitive, whitespace-tolerant, and recognised inside the
    object/list shapes above — ``{"theme_id": "AUTO"}`` surfaces as ``"auto"``)
    passes through as the exact lowercase string. It is the deliberate way to
    hand the choice to the saved theme / brand / default, so a slides deck that
    sends it is complete; one that sends nothing at all is refused by
    ``CreateArtifactInput``.
    """
    value = _maybe_json(value)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.lower() == _THEME_AUTO:
            return _THEME_AUTO
        return text or None
    if isinstance(value, dict):
        for key in _THEME_OBJECT_KEYS:
            if key in value:
                found = _coerce_theme_id(value[key])
                if found:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _coerce_theme_id(item)
            if found:
                return found
        return None
    return None


class CreateArtifactInput(BaseModel):
    """Input for create_artifact tool.

    - prompt: user's goal/instruction for the artifact (can include style preferences)
    - title: optional title for the artifact
    - mode: 'page' for dashboards, 'slides' for presentations
    - visualization_ids: ordered list of visualization IDs to include
    """

    prompt: str = Field(..., description=(
        "PRECONDITION: If existing viz_ids in `past_observations` or message history already cover the user's dashboard ask, call this tool directly with those viz_ids. Only call `create_data` first when the dashboard needs data those viz_ids don't provide.\n\n"
        "Structured build plan for the dashboard. This prompt drives the entire code generation — be specific and use these sections:\n\n"
        "## Layout\n"
        "Overall structure and viz placement. For each viz in `visualization_ids`: title, chart type, position, local filter yes/no. "
        "If a `current_artifact` exists, describe how existing vizs remain on the canvas AND where new additions go — do not describe a layout that silently drops prior vizs.\n"
        "Example: 'KPI row at top from Dashboard KPIs. Below: 2-col grid — Sales Trend as line chart left, Top Artists as horizontal bar right.'\n\n"
        "## Filters\n"
        "Global filters if 2+ vizs share a filterable column. Which columns, which vizs, column name mappings if names differ. Omit if none.\n"
        "Example: 'Global year filter across all 3 vizs. Column is `year` in all.'\n\n"
        "## Theme\n"
        "Colors, dark/light, spacing, typography, design feel. Capture the user's style request verbatim — this section overrides all system defaults.\n"
        "Example: 'Flat BI style — white bg, no shadows, no gradients, subtle borders, tight spacing, neutral typography. NOT executive/marketing.'\n\n"
        "CONTRACT: If your prompt specifies a cross-viz behavior (global filter, time comparison, slice/groupby, rank-across, drill-down), you MUST have already completed the Dashboard Contract preflight (see planner instructions): every viz_id in `visualization_ids` satisfies that contract directly, or you rebuilt its data via `create_data` this turn, or it was dropped/substituted because it was meaningless under the contract. Do NOT include a viz that can't participate in the declared contract — that ships a dashboard where the filter works on some charts and not others.\n\n"
        "CONTINUITY: When a `current_artifact` exists and the user is asking to improve/enhance/rework it, your prompt describes a CHANGE to that artifact, not a fresh build. "
        "Preserve the existing title (don't invent 'Enhanced X' / 'Improved Y') unless the user asked to rename. Describe ALL existing vizs in the layout (they're still on the canvas) plus the new additions. "
        "Prefer `edit_artifact` for small/additive changes; only use `create_artifact` when the change is structurally too large for surgical diffs — and even then, carry all prior viz_ids forward.\n\n"
        "Do NOT use this tool to modify an existing artifact; use edit_artifact instead. "
        "For a WRITTEN report/document/memo (not an interactive dashboard), use create_doc instead."
    ))
    title: Optional[str] = Field(None, description="Title for the artifact, make it concise and descriptive for end users. Should be in the same language as the user/prompt.")
    mode: Literal["page", "slides"] = Field(default="page", description="Artifact mode: 'page' for dashboards or 'slides' for presentations")
    file_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of File IDs (UUIDs) to embed as visual assets — generated images "
            "(from generate_image) or uploaded images/PDFs. Reference them in the code with the "
            "<BowFile id=\"<file_id>\" /> component (see the sandbox runtime docs). Their content "
            "type (image vs pdf) is resolved from the file automatically; do NOT inline base64."
        ),
    )
    visualization_ids: List[str] = Field(default_factory=list, description=(
        "Ordered list of visualization IDs (UUIDs) to include. Find these in previous create_data results as 'viz_id: <uuid>'. "
        "Required for data dashboards; may be empty when `file_ids` is provided (an image/PDF-only artifact) "
        "or when `mode` is 'slides' (a deck may open with a title/agenda slide and may be narrative-only). "
        "CONTINUITY: When a `current_artifact` exists in context, this list MUST be a superset of its existing viz_ids — carry forward every viz unless the user explicitly asked to remove one. "
        "Phrases like 'improve', 'add KPIs', 'make it amazing', 'redesign', 'add a chart' are ADDITIVE — they never imply removal. "
        "Drop a viz only on explicit instruction ('remove the customers chart', 'get rid of the KPI row') OR when the Dashboard Contract preflight classified it as meaningless under the contract (e.g., `Total Customers` under a customer filter = 1). "
        "Every viz in this list must be able to participate in any cross-viz contract your prompt declares (filter/compare/slice/rank/drill) — if it can't, rebuild its data via `create_data` first and swap in the new viz_id, or drop it."
    ))
    theme_id: Optional[str] = Field(default=None, description=(
        "SLIDES ONLY — the design system to build this deck in. Name any id from the theme index "
        "printed in the slides instructions (e.g. `boardroom`, `mckinsey-style`, `midnight-pitch`); "
        "the full spec of the theme you name is what the deck is built against. "
        "Pick the theme whose 'use when' fits the audience and the occasion — a board update, a sales "
        "pitch and a technical readout are not the same deck. "
        "If the user named a look themselves ('make it McKinsey style', 'dark and minimal'), name the theme that serves it. "
        "NAME ONE ON EVERY DECK. There are exactly two exceptions, and both are a case where the choice has "
        "already been made deliberately by someone else, so naming an id here would OVERRIDE it: the report "
        "already carries a saved theme you have no reason to change, or the organisation's brand is what should "
        "decide the look. Outside those two, omitting this is not neutrality — it hands the deck to a default "
        "picked for nobody in particular. Copy the id exactly as the index prints it: a misspelling is not the "
        "theme you meant, and it is resolved from the request instead. "
        "If one of those two exceptions applies, send the literal string `auto` — that is how you say "
        "'someone else has already decided'; omitting the field entirely is not a legal answer for a deck."
    ))

    @field_validator("theme_id", mode="before")
    @classmethod
    def _accept_any_theme_shape(cls, value: Any) -> Optional[str]:
        return _coerce_theme_id(value)

    @model_validator(mode="after")
    def _a_deck_must_answer_the_theme_question(self) -> "CreateArtifactInput":
        """A slides deck has to say which design system it is built in.

        Measured: the model omitted `theme_id` on every deck, twice over, and
        every one of them silently fell through to a default picked for nobody
        in particular. Two rewrites of the field description did not move it, so
        the requirement lives in the SHAPE instead — and the message names both
        legal answers, because the planner replays a validation error back to
        the model and that sentence is what it reads. Page mode is untouched:
        a dashboard has no theme index to choose from.
        """
        if self.mode == "slides" and self.theme_id is None:
            raise ValueError(
                "theme_id is required for a slides deck: send an id from the theme index "
                "(e.g. 'boardroom') or the literal string 'auto' to let the deck's saved theme, "
                "the organisation brand, or the default decide."
            )
        return self


class CreateArtifactOutput(BaseModel):
    """Output from create_artifact tool.

    - artifact_id: ID of the created artifact
    - code: the generated React/JSX code
    - mode: the artifact mode (page/slides)
    - title: the artifact title
    """

    artifact_id: str = Field(..., description="ID of the created artifact in the database")
    code: str = Field(..., description="The generated React/JSX code")
    mode: str = Field(..., description="Artifact mode, eiither 'page' for dashboards/reports or 'slides' for presentation, deck or powerpoint export")
    title: Optional[str] = Field(None, description="Artifact title")
    visualization_ids: List[str] = Field(default_factory=list, description="All visualization IDs included in this artifact. Use these when making further edits with edit_artifact.")
    version: int = Field(default=1, description="Version number of the artifact")
