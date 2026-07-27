from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.schemas.file_tag_schema import FileTagSchema
from app.schemas.sheet_schema_schema_ import SheetSchema

class FileBase(BaseModel):
    pass

class FileCreate(FileBase):
    pass

class FileSchema(FileBase):
    id: str
    filename: str
    content_type: str
    path: str
    created_at: datetime
    # "upload" (readable knowledge file) | "connector" (ephemeral) |
    # "table_backing" (data materialized into a queryable table — the agent
    # queries the table, not this raw file). The UI badges table_backing files.
    source_kind: str = "upload"
    # Derived (not a column): what the smart intake did with this file.
    # "table_backing" (materialized into a queryable table) | "instruction"
    # (produced grounding instruction(s)/skill) | "knowledge" (chunked into the
    # retrievable metadata index) | "upload" (parked, not ingested). Populated by
    # get_files_by_data_source; None on endpoints that don't compute it.
    fate: Optional[str] = None

    class Config:
        from_attributes = True


class FileSchemaWithCompletionId(FileSchema):
    """File schema that includes completion_id from the report_file_association."""
    completion_id: str | None = None
    # True when the file is attached to the report only because it was
    # auto-snapshotted from one of the report's data sources. The chat
    # prompt box uses this to hide inherited files from per-turn chips.
    from_data_source: bool = False


class FileSchemaWithMetadata(FileSchema):
    schemas: list[SheetSchema]
    tags: list[FileTagSchema]
