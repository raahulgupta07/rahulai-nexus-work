from sqlalchemy import Column, String, ForeignKey, DateTime, Index
from sqlalchemy.orm import relationship

from app.models.base import BaseSchema


class OwnershipTransfer(BaseSchema):
    """One row per object whose owner changed. The ledger behind every handover.

    Ownership on this product is a single non-null column per object
    (``reports.user_id`` and friends), so a transfer is a destructive write: the
    previous owner is simply gone from the row. This table is what makes that
    reversible, auditable and explainable on screen — it is not a duplicate of
    ``audit_logs``, which records that a request happened, whereas this records
    what the state was before it did.

    Three things it powers:

      * **undo** — a transfer destroys nothing, so any batch can be walked back
        inside the org's window (``transfer_undo_days``, default 30);
      * **the "transferred to you" badge** and the ownership history panel;
      * **attribution** — who moved it, which is a different question from who
        has it now.

    ★``actor_user_id`` is deliberately a THIRD column, not a reuse of
    ``to_user_id``. Superset's documented mistake is that transferring silently
    makes the acting user an owner; keeping the two apart means "who did this"
    can never be confused with "who has it". The three paths differ exactly
    here: a self-handover has ``actor == from_user``, an admin transfer has an
    actor who is neither, and the automatic successor path has **no actor at
    all** (NULL) because nobody clicked anything.

    ★All three user columns are nullable and carry no cascade. A user row can be
    deactivated but is never deleted on this product, so these stay resolvable —
    but the ledger must survive even if that ever stops being true. A history
    that disappears when someone is cleaned up is not a history.
    """

    __tablename__ = 'ownership_transfers'

    organization_id = Column(String(36), ForeignKey('organizations.id'), nullable=False, index=True)

    # What moved. Deliberately a soft reference (type + id) rather than five
    # nullable FK columns: the set of transferable things grows, and a table
    # with report_id/artifact_id/project_id/... columns is one more place to
    # forget a type. Values: 'report' | 'artifact' | 'query' |
    # 'scheduled_prompt' | 'project' | 'data_source'.
    resource_type = Column(String(32), nullable=False)
    resource_id = Column(String(36), nullable=False)

    from_user_id = Column(String(36), ForeignKey('users.id'), nullable=True, index=True)
    to_user_id = Column(String(36), ForeignKey('users.id'), nullable=True, index=True)
    # NULL when nothing human triggered it — the successor path. See the class
    # docstring: this is not the same question as to_user_id.
    actor_user_id = Column(String(36), ForeignKey('users.id'), nullable=True)

    # 'self_handover' | 'admin_transfer' | 'offboarding' | 'successor' |
    # 'claim' | 'undo'. Read by the UI to choose its wording ("you handed this
    # over" vs "this was transferred to you") and by the audit view.
    reason = Column(String(32), nullable=False)

    # Groups the objects moved by one operation so they undo together. A bulk
    # handover of 23 items is 23 rows and one batch_id; undoing half of a
    # handover would leave a state nobody chose.
    batch_id = Column(String(36), nullable=False, index=True)

    # ★Set by undo; the original row is NEVER deleted. Undo is itself recorded
    # as a new row with reason='undo', so the history reads forwards.
    reverted_at = Column(DateTime, nullable=True)

    # What this transfer changed beyond the owner column, so undo can put it
    # back exactly. Today: {"shared_run_identity": "creator"|"viewer",
    # "granted_share_to_previous_owner": true|false}. A dict rather than columns
    # because undo needs to restore whatever a future release starts moving,
    # and a migration per field would be a migration per field.
    previous_state = Column(String, nullable=True)  # JSON-encoded

    organization = relationship("Organization")
    from_user = relationship("User", foreign_keys=[from_user_id])
    to_user = relationship("User", foreign_keys=[to_user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])

    __table_args__ = (
        # The two queries this table actually serves: "what is the history of
        # this object" and "what did this batch do".
        Index('ix_ownership_transfers_resource', 'organization_id', 'resource_type', 'resource_id'),
    )
