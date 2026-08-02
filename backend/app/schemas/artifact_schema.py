from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class SlideContent(BaseModel):
    """Content for a single slide in slides mode."""
    code: str
    title: Optional[str] = None
    order: int = 0


class ArtifactContentPage(BaseModel):
    """Content structure for page mode artifacts."""
    code: str


class ArtifactContentSlides(BaseModel):
    """Content structure for slides mode artifacts."""
    slides: List[SlideContent]


class ArtifactContentDoc(BaseModel):
    """Content structure for doc mode artifacts (markdown documents)."""
    markdown: str
    visualization_ids: List[str] = Field(default_factory=list)


class ArtifactBase(BaseModel):
    """Base schema for Artifact."""
    title: Optional[str] = "Untitled Artifact"
    mode: Literal["page", "slides", "doc"] = "page"


class ArtifactCreate(ArtifactBase):
    """Schema for creating a new artifact."""
    report_id: str
    content: dict  # Either ArtifactContentPage or ArtifactContentSlides
    generation_prompt: Optional[str] = None
    completion_id: Optional[str] = None


class ArtifactUpdate(BaseModel):
    """Schema for updating an existing artifact."""
    title: Optional[str] = None
    content: Optional[dict] = None
    generation_prompt: Optional[str] = None


class ArtifactSchema(ArtifactBase):
    """Full artifact schema for API responses."""
    id: str
    report_id: str
    user_id: str
    organization_id: str
    version: int
    content: dict
    generation_prompt: Optional[str] = None
    completion_id: Optional[str] = None
    status: str = "completed"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ArtifactListSchema(BaseModel):
    """Schema for listing artifacts (lighter weight)."""
    id: str
    report_id: str
    title: Optional[str]
    mode: str
    version: int
    status: str = "completed"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ArtifactBrowseSchema(BaseModel):
    """One artifact as it appears on the Dashboards page.

    Distinct from ArtifactListSchema, which lists the artifacts *inside* one
    report the caller already has open. This one is cross-report, so it has to
    carry where it came from: `report_title` is the subtitle on the card, and
    `report_id` is what the card links to. `content` is deliberately absent —
    the grid renders a thumbnail, never the artifact itself.
    """
    id: str
    report_id: str
    report_title: Optional[str] = None
    title: Optional[str] = None
    mode: str
    version: int = 1
    status: str = "completed"
    thumbnail_url: Optional[str] = None
    owner_name: Optional[str] = None
    owner_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ArtifactBrowseResponse(BaseModel):
    artifacts: List[ArtifactBrowseSchema]
    meta: "PaginationMeta"
    # Per-mode totals for the All / Dashboards / Docs / Slides chips. Counted
    # over the whole filtered set, not the current page — a chip that counted
    # only the page would change its number as you paginate.
    mode_counts: dict = {}


from app.schemas.report_schema import PaginationMeta  # noqa: E402  (cycle-safe: report_schema does not import this module)

ArtifactBrowseResponse.model_rebuild()


