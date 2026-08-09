"""read_local_document — read a document from a user-attached LOCAL folder.

Local folders (paperclip → Folders) are directories on the user's own laptop,
shared by their paired CityAgent Helper. Tabular files in them are queried in
place via DuckDB; documents (pdf/docx/pptx/txt/md) are listed in the
<local_folders> block by name only. When the user's question needs one of those
documents, the agent calls this tool: the server queues a read_document job,
the helper extracts the text ON the user's device, and only that text (capped)
comes back. The file itself is never uploaded.

Gates (all must hold, mirroring the query lane):
  - HYBRID_LOCAL_RUNTIME + HYBRID_LOCAL_FOLDER_ATTACH flags on,
  - the requesting user has a paired + online helper,
  - the folder is actually shared from that device (server-side backstop; the
    helper validates the path again on its side).
"""

from typing import AsyncIterator, Dict, Any, Type
import logging

from pydantic import BaseModel

from app.ai.tools.base import Tool
from app.ai.tools.metadata import ToolMetadata
from app.ai.tools.schemas.read_local_document import (
    ReadLocalDocumentInput,
    ReadLocalDocumentOutput,
)
from app.ai.tools.schemas.events import (
    ToolEvent,
    ToolStartEvent,
    ToolEndEvent,
    ToolErrorEvent,
)

logger = logging.getLogger(__name__)

# The planner consumes the OBSERVATION — the extracted text must be in
# observation.details (bounded) or the model sees only a summary line.
_OBS_DETAILS_MAX_CHARS = 8000


class ReadLocalDocumentTool(Tool):
    """Read a document from an attached local folder, extracted on-device."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="read_local_document",
            description=(
                "RESEARCH: Read the text of a document (pdf/docx/pptx/txt/md) that "
                "lives inside a LOCAL folder the user attached from their own "
                "computer — documents are listed by name in <local_folders>. The "
                "text is extracted on the user's device by their helper; the file "
                "is never uploaded. Use when the user's question is about one of "
                "those documents. NOT for uploaded report files (use read_file) or "
                "for tabular files in the folder (query them via "
                "ds_clients['local:<folder>'] in create_data instead)."
            ),
            category="research",
            version="1.0.0",
            input_schema=ReadLocalDocumentInput.model_json_schema(),
            output_schema=ReadLocalDocumentOutput.model_json_schema(),
            max_retries=1,
            timeout_seconds=90,
            idempotent=True,
            required_permissions=[],
            tags=["local", "document", "read", "folder"],
            allowed_modes=["chat"],
            examples=[
                {
                    "input": {"folder": "AA-Medical", "file": "Enablement Program.pptx"},
                    "description": "Read a deck from the user's attached local folder",
                },
            ],
        )

    @property
    def input_model(self) -> Type[BaseModel]:
        return ReadLocalDocumentInput

    @property
    def output_model(self) -> Type[BaseModel]:
        return ReadLocalDocumentOutput

    async def run_stream(
        self, tool_input: Dict[str, Any], runtime_ctx: Dict[str, Any]
    ) -> AsyncIterator[ToolEvent]:
        try:
            data = ReadLocalDocumentInput(**tool_input)
        except Exception as e:
            yield ToolErrorEvent(
                type="tool.error",
                payload={"error": f"Invalid input: {e}", "code": "INVALID_INPUT"},
            )
            return

        folder = (data.folder or "").strip()
        file = (data.file or "").strip()
        yield ToolStartEvent(type="tool.start", payload={"folder": folder, "file": file})

        from app.settings.config import settings
        if not (getattr(settings, "hybrid_local_runtime", False)
                and getattr(settings, "hybrid_local_folder_attach", False)):
            yield self._end_error("Local folders are not enabled on this server.")
            return

        db = runtime_ctx.get("db")
        organization = runtime_ctx.get("organization")
        user = runtime_ctx.get("user")
        if not all([db, organization, user]):
            yield ToolErrorEvent(
                type="tool.error",
                payload={"error": "Missing required runtime context (db, organization, user)",
                         "code": "MISSING_CONTEXT"},
            )
            return

        try:
            from app.services.local_runtime_exec import read_local_document_remote
            result = await read_local_document_remote(
                db=db,
                user_id=str(user.id),
                org_id=str(organization.id),
                folder=folder,
                file=file,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("read_local_document failed: %s", e)
            yield ToolErrorEvent(
                type="tool.error",
                payload={"error": f"Read failed: {e}", "code": "READ_FAILED"},
            )
            return

        if not result.get("ok"):
            yield self._end_error(result.get("error") or "Reading the document failed.")
            return

        text = result.get("text") or ""
        output = ReadLocalDocumentOutput(
            success=True,
            folder=folder,
            file=file,
            text=text,
            chars=len(text),
            runtime_name=result.get("runtime_name"),
            message=f"Read {file} from local folder {folder} on the user's device",
        )
        details = text[:_OBS_DETAILS_MAX_CHARS]
        if len(text) > _OBS_DETAILS_MAX_CHARS:
            details += f"\n\n[+{len(text) - _OBS_DETAILS_MAX_CHARS} more chars in tool output]"
        yield ToolEndEvent(
            type="tool.end",
            payload={
                "output": output.model_dump(),
                "observation": {
                    "summary": (
                        f"Read '{file}' ({len(text)} chars) from local folder "
                        f"'{folder}' — extracted on the user's device"
                    ),
                    "details": details,
                    "artifacts": [
                        {"type": "local_document_read", "folder": folder, "file": file,
                         "chars": len(text)}
                    ],
                },
            },
        )

    def _end_error(self, message: str) -> ToolEndEvent:
        return ToolEndEvent(
            type="tool.end",
            payload={
                "output": ReadLocalDocumentOutput(success=False, message=message).model_dump(),
                "observation": {"summary": message},
            },
        )


# Flag gate at registration time: with local folders off this module exposes no
# Tool subclass, so the registry's auto-discovery skips it and the catalog is
# byte-identical to a build without this file.
from app.settings.config import settings as _settings  # noqa: E402

if not (getattr(_settings, "hybrid_local_runtime", False)
        and getattr(_settings, "hybrid_local_folder_attach", False)):
    del ReadLocalDocumentTool
