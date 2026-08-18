from typing import Optional, List
from pydantic import Field, BaseModel

from app.ai.tools.schemas.create_widget import TablesBySourceList

class InspectDataInput(BaseModel):
    user_prompt: str = Field(
        ...,
        description="Description of what to inspect. E.g. 'Check distinct values in status column', 'Preview the uploaded Excel file', 'Check for nulls in revenue'."
    )
    # ★★★This was `Optional[List[Dict[str, Any]]]` — a free-form dict list with
    # no shape at all, so ANY dict validated. Measured on the live instance
    # 2026-08-17, the planner sent `[{"name": "LK_CFC_Sales.dbo.cfc_champion"}]`
    # on every call; it passed validation, and every consumer downstream reads
    # `group["tables"]`, found nothing, and handed the code generator an EMPTY
    # list. So the "Resolved Target Tables (authoritative)" block never rendered
    # for inspect_data at all — the coder was left to pick a lakehouse out of
    # four, and picked the plausible sibling. `create_data` uses the typed
    # lenient field and succeeded in 12.8s; this one failed three times over 79s
    # on the same question, in the same turn.
    #
    # An untyped argument is not permissive, it is silent: it accepts the wrong
    # shape and discards the meaning.
    tables_by_source: TablesBySourceList = Field(
        default=None,
        description=(
            "Tables to resolve and load for inspection, as "
            "[{data_source_id, tables: [\"Catalog.Schema.Table\", ...]}, ...]. "
            "Use the EXACT fully-qualified names from the schema context — the "
            "leading catalog/database segment is what binds a table to the right "
            "database, and a table with the same name often exists under several. "
            "Omit entirely when inspecting uploaded files."
        ),
    )
    source_file_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "File IDs to inspect, e.g. the file_id returned by execute_mcp. "
            "Pass it whenever you mean a specific file: it pins the generated "
            "code to those files and tells it the exact path, reader and "
            "column shape, instead of leaving it to guess which attachment you meant."
        ),
    )

class InspectDataOutput(BaseModel):
    execution_log: str = Field(..., description="The standard output (stdout) from the inspection code.")
    success: bool = Field(..., description="Whether the inspection code ran without fatal errors.")
    error_message: Optional[str] = Field(default=None, description="Error message if execution failed.")
