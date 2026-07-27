from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from app.models.base import BaseSchema


class UserHiddenDataSource(BaseSchema):
    """Per-user 'hide from my chat picker' preference.

    Personal scope only: hiding an agent here removes it from *this* user's
    composer picker. It does NOT disable the agent for anyone else and does NOT
    touch the AI context — that is the global publish_status='disabled' control.
    """
    __tablename__ = "user_hidden_data_sources"

    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    data_source_id = Column(String(36), ForeignKey('data_sources.id', ondelete='CASCADE'), nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'data_source_id', name='uq_user_hidden_data_source'),
    )

    def __repr__(self):
        return f"<UserHiddenDataSource(user_id={self.user_id}, data_source_id={self.data_source_id})>"
