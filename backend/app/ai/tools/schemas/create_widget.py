from typing import Dict, Any, Optional, List
from pydantic import BaseModel, BeforeValidator, Field
from typing_extensions import Annotated

from ._lenient import one_object_from_scalars
from .create_data_model import DataModel


# ★Models keep sending `tables_by_source` as a FLAT LIST OF TABLE NAMES —
# `["fact_sales", "dim_date", "dim_channel"]` — instead of the nested
# `[{data_source_id, tables: [...]}]` the schema names. Measured live: 11 calls
# lost to `tables_by_source.0 is not a dict` plus 4 to a dict missing `tables`.
# The retry usually recovers, at the cost of a round trip and a red failed step
# in the user's transcript.
#
# ★`one_object_from_scalars`, NOT `objects_from_scalars`: those five names are
# five tables in ONE grouping. Collapsing them per-item would build five
# groupings of one table each — valid against the schema and wrong about the
# request, which is worse than the error it replaces.
#
# ★The alias lives on the FIELD, never on `TablesBySource` itself: the model
# stays strict, so `tables` remains required and genuine nonsense is still
# rejected. Applied to every field that holds a list of them so the two tools
# behave identically.
#
# ★Declared BELOW the model rather than above it — a forward reference here
# would have to be resolved in whatever module builds the field, and this alias
# is imported by create_data.py.


class TablesBySource(BaseModel):
    """Per-source table filters to focus schema loading.

    - data_source_id: scope to a specific data source (UUID). If omitted/null, applies across all sources.
    - tables: list of table names. Names are always treated as literal (escaped).
      Matching is case-insensitive and names match with or without a schema/dataset prefix (. or / separator).
      Examples: "film", "public.inventory", "Regional Sales Sample (2)/Opportunities".
    """

    data_source_id: Optional[str] = Field(
        default=None,
        description="UUID of the data source to scope these tables. If null, applies to all sources.",
    )
    tables: List[str] = Field(
        ..., description="Table names (literal, case-insensitive). Schema or dataset prefix (. or /) is optional."
    )


TablesBySourceList = Annotated[
    Optional[List[TablesBySource]],
    BeforeValidator(
        one_object_from_scalars(
            list_key="tables",
            # ★Every name a model has actually reached for instead of `tables`,
            # observed in live tool calls. `table_names` is the common one and
            # arrives with a stray `connection_id` alongside it, which pydantic
            # drops harmlessly under the default extra="ignore".
            aliases={
                "table_names": "tables",
                "tableNames": "tables",
                "table": "tables",
                "names": "tables",
                # ★Measured on the live instance 2026-08-17: every inspect_data
                # call that day sent `[{"name": "LK_CFC_Sales.dbo.cfc_champion"},
                # …]`. Singular, one table per dict — the shape a model reaches
                # for when the field is described as "a list of tables".
                "name": "tables",
                "table_name": "tables",
                "tableName": "tables",
                "qualified_name": "tables",
                "full_name": "tables",
            },
        )
    ),
]


class CreateWidgetInput(BaseModel):
    """Input for end-to-end widget creation.

    The tool will generate a data_model, then code, then execute it to populate the widget.
    """

    widget_title: str = Field(..., description="Title for the widget to create")
    user_prompt: str = Field(..., description="Original user instruction")
    interpreted_prompt: str = Field(..., description="LLM-interpreted, clarified version of the user prompt")

    tables_by_source: TablesBySourceList = Field(
        default=None,
        description=(
            "Compact per-source table targeting: [{data_source_id, tables:[...]}, ...]. "
            "Avoids repeating ds_id per table and supports cross-source patterns when data_source_id is null."
        ),
    )
    schema_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Max tables to include per data source when rendering the schema excerpt.",
    )


class CreateWidgetOutput(BaseModel):
    """Output of end-to-end widget creation."""

    success: bool = Field(..., description="Whether the overall operation succeeded")
    data_model: Optional[DataModel] = Field(default=None, description="Final normalized data model")
    code: Optional[str] = Field(default=None, description="Final code used to compute widget data")
    widget_data: Optional[Dict[str, Any]] = Field(default=None, description="Rendered data structure for the widget")
    data_preview: Optional[Dict[str, Any]] = Field(default=None, description="Privacy-safe preview for UI/LLM")
    stats: Optional[Dict[str, Any]] = Field(default=None, description="Execution stats/metadata")
    execution_log: Optional[str] = Field(default=None, description="Execution log or trace output if available")


