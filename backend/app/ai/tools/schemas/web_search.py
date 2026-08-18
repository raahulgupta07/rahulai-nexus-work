from typing import List, Optional

from pydantic import BaseModel, Field


class WebSearchResult(BaseModel):
    """One result row.

    ★`title` and `url` are the two fields `WebSearchTool.vue` renders — the
    component that already exists for the provider-executed search. Keeping
    those names is what lets this tool reuse that UI untouched.
    """

    title: Optional[str] = Field(None, description="Result heading as the search engine wrote it.")
    url: str = Field(..., description="Absolute https URL of the result.")
    snippet: Optional[str] = Field(
        None, description="The engine's summary line. Not the page body — use web_fetch for that."
    )


class WebSearchInput(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=400,
        description="What to search for, phrased as a person would type it into a search box.",
    )
    max_results: int = Field(
        8, ge=1, le=20, description="How many results to return. Ten is plenty for most questions."
    )


class WebSearchOutput(BaseModel):
    success: bool = False
    query: str = ""
    sources: List[WebSearchResult] = Field(
        default_factory=list, description="Results, best first."
    )
    error_message: Optional[str] = None
