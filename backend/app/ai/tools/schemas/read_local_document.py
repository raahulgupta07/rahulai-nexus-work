from typing import Optional
from pydantic import BaseModel, Field


class ReadLocalDocumentInput(BaseModel):
    """Input schema for the read_local_document tool.

    Reads a document (pdf/docx/pptx/txt/md) that lives inside a local folder
    the user attached from their own computer. The text is extracted ON the
    user's device by their paired helper — the file itself is never uploaded.
    """

    folder: str = Field(
        ...,
        description=(
            "The attached local folder's name, exactly as shown in "
            "<local_folders> (e.g. 'AA-Medical')."
        ),
    )
    file: str = Field(
        ...,
        description=(
            "The document's file name inside that folder, exactly as listed in "
            "<local_folders> (e.g. 'Enablement Program.pptx')."
        ),
    )


class ReadLocalDocumentOutput(BaseModel):
    """Output schema for the read_local_document tool response."""

    success: bool = Field(..., description="Whether the read succeeded")
    folder: Optional[str] = Field(None, description="Folder the document was read from")
    file: Optional[str] = Field(None, description="Document file name")
    text: Optional[str] = Field(None, description="Extracted document text (capped)")
    chars: Optional[int] = Field(None, description="Number of characters returned")
    runtime_name: Optional[str] = Field(None, description="Device the text was extracted on")
    message: Optional[str] = Field(None, description="Status or error message")
