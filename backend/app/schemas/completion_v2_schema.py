from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

from .widget_schema import WidgetSchema
from .step_schema import StepSchema
from .tool_execution_schema import ToolExecutionSchema
from .agent_execution_schema import PlanDecisionReducedSchema
from .visualization_schema import VisualizationSchema
from .completion_feedback_schema import CompletionFeedbackSchema
from .file_schema import FileSchema


class ToolExecutionDataSourceSchema(BaseModel):
    """Lightweight data source info for display in tool execution UI."""
    id: str
    name: Optional[str] = None
    type: Optional[str] = None  # connection type e.g. 'postgres', 'bigquery'


class ToolExecutionUISchema(ToolExecutionSchema):
    """UI-focused tool execution with embedded created artifacts when available."""
    created_widget: Optional[WidgetSchema] = None
    created_step: Optional[StepSchema] = None
    created_visualizations: Optional[list[VisualizationSchema]] = None
    data_sources: Optional[list[ToolExecutionDataSourceSchema]] = None


class ArtifactChangeSchema(BaseModel):
    """Delta describing incremental updates to a step/widget during this block (optional)."""
    type: Literal["step", "widget", "visualization"]
    step_id: Optional[str] = None
    widget_id: Optional[str] = None
    visualization_id: Optional[str] = None
    revision: Optional[int] = None
    partial: Optional[bool] = True
    changed_fields: List[str] = []
    fields: Dict[str, Any] = {}


class BlockTextDeltaSchema(BaseModel):
    """Tiny text delta for progressive token/char streaming on a block field."""
    block_id: str
    field: Literal["reasoning", "content"]
    text: str
    token_index: Optional[int] = None
    is_final_chunk: Optional[bool] = None

class PromptSchema(BaseModel):
    content: str = ""
    widget_id: Optional[str] = None
    step_id: Optional[str] = None
    mentions: Optional[List[dict]] = None
    mode: Optional[str] = 'chat'
    model_id: Optional[str] = None
    platform: Optional[str] = None  # 'excel', 'slack', 'teams', etc. None = web
    platform_context: Optional[Dict[str, Any]] = None  # Platform-specific context (e.g. Excel selection data)
    # Folders attached from the user's own device (names only, e.g. ["Sales"]).
    # Persisted with the rest of the prompt into completions.prompt, which is
    # what makes the attachment sticky for later turns — see
    # app/ai/agents/local_folders_context.py. An explicit [] means "detach".
    local_folders: Optional[List[str]] = None
    # Uploaded-file names the composer held when this message was sent. PURE
    # DISPLAY metadata for the chat bubbles: document uploads are report-scoped
    # (only images ride completion.files), so without this stamp a turn has no
    # record of which files it was asked against. Never used for execution.
    attached_files: Optional[List[str]] = None
    # Per-completion override for extended-thinking effort. Resolution order:
    #   per-completion > trigger words > LLMModel.config default > "off"
    # Currently honored on Anthropic only; ignored on other providers.
    reasoning_effort: Optional[str] = None  # off|low|medium|high

    class Config:
        from_attributes = True

class CompletionBase(BaseModel):
    prompt: Optional[PromptSchema]

class CompletionCreate(CompletionBase):
    stream: Optional[bool] = False
    # When true and a completion is already running on the report, the prompt
    # is persisted as a role='user' status='queued' row instead of starting a
    # second concurrent agent run. The queue dispatcher starts it when the
    # running turn finishes successfully.
    queue: Optional[bool] = False


class CompactionStateSchema(BaseModel):
    tokens_compacted_total: int = 0
    covered_turns: int = 0
    last_compaction_at: Optional[str] = None
    can_compact: bool = False
    # Fold boundary: the transcript renders the compaction divider directly
    # after this completion (state-derived — there are no divider rows).
    covers_until_completion_id: Optional[str] = None


class CompletionContextEstimateSchema(BaseModel):
    model_id: str
    model_name: Optional[str] = None
    prompt_tokens: int
    model_limit: Optional[int] = None
    remaining_tokens: Optional[int] = None
    near_limit: bool = False
    context_usage_pct: Optional[float] = None
    compaction: Optional[CompactionStateSchema] = None


class CompletionBlockV2Schema(BaseModel):
    id: str
    completion_id: str
    agent_execution_id: Optional[str]

    # Ordering
    seq: Optional[int] = None
    block_index: int
    loop_index: Optional[int]

    # Phase tag (e.g. 'knowledge_harness'); None for regular main-loop blocks
    phase: Optional[str] = None

    # Render fields
    title: str
    status: str  # in_progress | completed | error | planning
    icon: Optional[str]
    content: Optional[str]
    reasoning: Optional[str]

    # Source objects
    plan_decision: Optional[PlanDecisionReducedSchema] = None
    tool_execution: Optional[ToolExecutionUISchema] = None

    # Optional artifact deltas for progressive UIs
    artifact_changes: Optional[List[ArtifactChangeSchema]] = None

    # Timing
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_ms: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CompletionV2Schema(BaseModel):
    id: str
    role: str
    status: str
    model: str
    turn_index: int
    parent_id: Optional[str]
    report_id: str
    # 'steering' for user messages injected into a running completion
    message_type: Optional[str] = None

    agent_execution_id: Optional[str] = None

    prompt: Optional[Dict[str, Any]] = None

    completion_blocks: List[CompletionBlockV2Schema] = []

    # Final artifacts for quick render
    created_widgets: List[WidgetSchema] = []
    created_steps: List[StepSchema] = []
    created_visualizations: List[VisualizationSchema] = []

    # Files attached to this completion (images, etc.)
    files: List[FileSchema] = []

    # Small summary for UI
    summary: Dict[str, Any] = {}

    # Suggested instructions produced during this agent execution (optional, outside blocks)
    instruction_suggestions: Optional[List[Dict[str, Any]]] = None

    # Suggested follow-up questions (web sessions only). Persisted on the
    # completion; also delivered live via the `completion.follow_ups` SSE event.
    follow_ups: Optional[List[str]] = None

    # Instructions loaded into context during this completion (for UI indicator)
    loaded_instructions: Optional[List[Dict[str, Any]]] = None

    # Knowledge-harness build associated with this completion (if any).
    # Shape: { id, build_number, status, is_main } — authoritative build state
    # so KnowledgeGroup can render publish state without local caches.
    knowledge_harness_build: Optional[Dict[str, Any]] = None

    # Feedback - pre-loaded to avoid N+1 API calls
    feedback_score: int = 0  # Legacy aggregate score from Completion model
    user_feedback: Optional[CompletionFeedbackSchema] = None  # Current user's feedback if any

    # Control & timing
    sigkill: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    # Scheduled prompt
    scheduled_prompt_id: Optional[str] = None

    # Webhook provenance (for compact event-entry rendering + source badge)
    webhook_id: Optional[str] = None
    external_platform: Optional[str] = None
    # Machine-turn provenance ('eval_run', 'wait', ...) for event entries.
    trigger_source: Optional[str] = None

    # Marker rows (context_compaction, error, …) need the type for special rendering
    message_type: Optional[str] = None

    # Fork summary fields
    is_fork_summary: Optional[str] = None
    source_report_id: Optional[str] = None
    fork_asset_refs: Optional[List[Dict[str, Any]]] = None
    # ★DEF-018. This is NOT the turn's answer, and the name says otherwise.
    #
    # `GET /api/reports/{id}/completions` is served by THIS schema — the v1
    # shape moved to `/completions.legacy` — and v1's `completion` field WAS the
    # answer. So an integration written against the documented path now reads a
    # familiar key, gets `null`, and concludes the turn said nothing, while the
    # database column holds thousands of characters. Measured on dev: null here,
    # 2,418 characters there.
    #
    # It is populated only for rows the UI renders specially (fork summary,
    # error, external, context compaction) — see `completion_service` ~line 1611.
    # Filling it for ordinary turns is deliberately NOT the fix: the answer is
    # already in `completion_blocks`, and duplicating it would ship every
    # answer's full text twice on a list endpoint, which is what the note below
    # exists to prevent.
    #
    # The description is carried into the OpenAPI schema so the null is
    # explained where an integrator actually looks, instead of being an
    # invitation to read it.
    completion: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "NOT the turn's answer — the answer is in `completion_blocks`. Raw "
            "content carried only for rows the UI renders specially (fork "
            "summary, error, external, context compaction); `null` on an "
            "ordinary turn does not mean the turn said nothing."
        ),
    )

    # ★Surfaced as explicit fields rather than through `completion` above, which
    # is deliberately None for an ordinary turn so the list does not ship every
    # answer's full text twice. These three are short, and without them the
    # reader is never shown what the agent already recorded: what the turn read,
    # why it ended early, and what it could not reach. A fact that reaches the
    # database and not the screen is the same as no fact at all.
    scope: Optional[Dict[str, Any]] = None       # {kind, label, file_count}
    stop_note: Optional[str] = None              # why it ended, when not normally
    stopped_early: Optional[bool] = None
    evidence_notice: Optional[str] = None        # what it could not reach

    class Config:
        from_attributes = True


class CompletionStopResponse(BaseModel):
    """What the stop button gets back.

    ★The route had no ``response_model``, so FastAPI ran `jsonable_encoder`
    over the raw `Completion` ORM row. That walks relationships — completion →
    report → completions → report — and the stop returned **500 RecursionError**
    even though the run had been stopped correctly. The worst shape of bug: the
    thing worked and said it had failed, so the user pressed stop again.

    Deliberately four fields. Neither caller reads the body; they set their own
    local state. Anything wider is another chance to serialise a graph.
    """

    id: str
    report_id: str
    status: str
    sigkill: Optional[datetime] = None

    class Config:
        from_attributes = True


class CompletionsV2Response(BaseModel):
    report_id: str
    completions: List[CompletionV2Schema]
    total_completions: int
    total_blocks: int
    total_widgets_created: int
    total_steps_created: int
    earliest_completion: Optional[datetime] = None
    latest_completion: Optional[datetime] = None
    # Cursor pagination. next_before is an opaque cursor ("<ISO8601>~<completion id>")
    # the client echoes back verbatim as the `before` query param.
    has_more: bool = False
    next_before: Optional[str] = None
    # Rolling-compaction state so the transcript can place the divider on load
    compaction: Optional[CompactionStateSchema] = None


