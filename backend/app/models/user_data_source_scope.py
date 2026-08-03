"""Which workspaces one member wants synced from one federated source.

A member with access to twenty Fabric workspaces usually works in three. Before
this, every sync crawled all twenty — bounded at six concurrent endpoints
(`_FABRIC_CRAWL_CONCURRENCY`), so twenty workspaces is four sequential rounds of
whatever the slowest workspace costs, on every sign-in and every retry, for
tables the member never opens.

Per **user**, not per organization: two people with access to the same twenty
workspaces care about different three, and the crawl already runs under each
member's own token.

★``selected_endpoints IS NULL`` and ``selected_endpoints == []`` are different
answers and must never collapse into one branch:

- **NULL** — the member has never chosen. Sync everything. This is the
  behaviour every existing install has today, and it is what an absent row
  means, so nothing changes for anyone who does not use the picker.
- **[]** — the member deselected everything. Sync *nothing*.

Reading the empty list as "no filter" is the specific bug this docstring exists
to prevent: deselecting every workspace would trigger the full twenty-workspace
crawl, which is exactly the cost the feature was built to avoid, at exactly the
moment the member asked for the least work possible.
"""
from sqlalchemy import Column, String, JSON, ForeignKey, UniqueConstraint

from app.models.base import BaseSchema


class UserDataSourceScope(BaseSchema):
    __tablename__ = "user_data_source_scopes"
    __table_args__ = (
        UniqueConstraint("data_source_id", "user_id", name="uq_user_ds_scope"),
    )

    data_source_id = Column(
        String(36), ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    # List of endpoint keys to sync, or NULL for "all". An endpoint key is the
    # discovery name — the same string `connection_sync_progress.detail[].name`
    # reports and the picker displays, so a member selects the thing they saw.
    selected_endpoints = Column(JSON, nullable=True)
