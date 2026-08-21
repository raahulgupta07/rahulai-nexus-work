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
    # DEF-017. A REFUSAL is not a FAILURE, and the two used to render as the
    # same orange "Web search failed" — so "an administrator turned this off"
    # looked exactly like "the search is broken", and the member had nothing to
    # act on. This says which happened, so the label can too.
    #
    # ★A boolean rather than the UI matching on `error_message` text: a screen
    # that decides what a state MEANS by pattern-matching a sentence breaks the
    # moment the sentence is reworded or translated.
    blocked_by_policy: bool = False
