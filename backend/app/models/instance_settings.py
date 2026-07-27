from sqlalchemy import Column, JSON
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.base import BaseSchema


class InstanceSettings(BaseSchema):
    """Global, instance-wide settings singleton.

    Holds configuration that must exist before any organization is selected
    (e.g. instance-level SSO login). Unlike OrganizationSettings this is not
    scoped to an organization — there is exactly one row for the whole instance.
    """
    __tablename__ = "instance_settings"

    config = Column(JSON, default=dict, nullable=True)

    @classmethod
    async def get_or_create(cls, db: AsyncSession):
        """Return the singleton instance settings row, creating it if missing."""
        result = await db.execute(select(cls))
        settings = result.scalars().first()

        if settings is None:
            settings = cls(config={})
            db.add(settings)
            await db.commit()
            await db.refresh(settings)

        return settings

    def get_config(self, key, default=None):
        """Get a configuration value by key."""
        if self.config and key in self.config:
            return self.config.get(key)
        return default

    def set_config(self, key, value):
        """Set a configuration value by key."""
        if self.config is None:
            self.config = {}
        self.config[key] = value
