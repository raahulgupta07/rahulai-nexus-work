from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm.attributes import flag_modified

from app.models.organization import Organization
from app.models.user import User
from app.models.organization_settings import OrganizationSettings
from app.ee.license import has_feature
from app.schemas.organization_settings_schema import (
    OrganizationSettingsCreate,
    OrganizationSettingsUpdate,
    OrganizationSettingsConfig,
    FeatureConfig,
    FeatureState,
    SignupPolicySchema,
    AutoProvisionSchema,
)
from datetime import datetime
import os
import hashlib
from PIL import Image
from io import BytesIO
from app.ee.audit.service import audit_service
from app.core.telemetry import telemetry


class OrganizationSettingsService:
    def __init__(self):
        pass

    async def get_settings(
        self, 
        db: AsyncSession, 
        organization: Organization,
        current_user: User
    ):
        """Get settings for an organization"""
        result = await db.execute(
            select(OrganizationSettings)
            .filter(OrganizationSettings.organization_id == organization.id)
        )
        
        settings = result.scalar_one_or_none()
        
        # If settings don't exist yet, create default ones
        if not settings:
            settings = await self.create_default_settings(db, organization, current_user)
        else:
            # Check for any new features in schema that aren't in the DB
            await self._sync_new_features(db, settings)
            
        return settings

    @staticmethod
    def _is_feature_dict(d) -> bool:
        """Matches OrganizationSettings.get_config's FeatureConfig heuristic."""
        return isinstance(d, dict) and all(k in d for k in ('name', 'description'))

    @classmethod
    def _refresh_feature_metadata(cls, stored, schema_feature) -> bool:
        """Overwrite code-owned metadata (name/description/editable/is_lab) on a
        stored feature entry with the schema's current values. ``value`` and
        ``state`` belong to the org and are left untouched. Returns True if
        anything changed."""
        if not (cls._is_feature_dict(stored) and cls._is_feature_dict(schema_feature)):
            return False
        changed = False
        for meta_key in ('name', 'description', 'editable', 'is_lab'):
            if meta_key in schema_feature and stored.get(meta_key) != schema_feature[meta_key]:
                stored[meta_key] = schema_feature[meta_key]
                changed = True
        return changed

    async def _sync_new_features(self, db: AsyncSession, settings: OrganizationSettings):
        """Sync the stored config with the schema: add new features, refresh
        code-owned metadata on existing ones (so renames/description edits and
        editable/is_lab flips reach orgs created before the change), and drop
        feature entries whose schema field was removed."""
        schema_config = OrganizationSettingsConfig()
        # Ensure current_config is mutable and handles potential None
        current_config = dict(settings.config) if settings.config else {}
        config_modified = False

        # Ensure top-level keys from schema exist
        schema_dict = schema_config.dict(exclude={'ai_features'})
        for key, feature_or_value in schema_dict.items():
             if key not in current_config:
                 # Store the dict representation if it's a FeatureConfig
                 current_config[key] = feature_or_value if not isinstance(feature_or_value, FeatureConfig) else feature_or_value.dict()
                 config_modified = True
             elif self._refresh_feature_metadata(current_config[key], feature_or_value):
                 config_modified = True

        # Drop stored feature entries (never plain values or nested blocks like
        # signup_policy/onboarding) whose schema field no longer exists, so
        # removed settings stop rendering for orgs that snapshotted them.
        for key in list(current_config.keys()):
            if key not in schema_dict and key != 'ai_features' and self._is_feature_dict(current_config[key]):
                del current_config[key]
                config_modified = True

        # Ensure 'ai_features' key exists and sync individual AI features
        if 'ai_features' not in current_config:
            current_config['ai_features'] = {}
            config_modified = True # Mark modified if ai_features dict itself was added

        schema_ai_features = schema_config.ai_features
        # Ensure current_config['ai_features'] is a dict
        if not isinstance(current_config.get('ai_features'), dict):
            current_config['ai_features'] = {}
            config_modified = True

        for key, feature in schema_ai_features.items():
            if key not in current_config['ai_features']:
                current_config['ai_features'][key] = feature.dict()
                config_modified = True
            elif self._refresh_feature_metadata(current_config['ai_features'][key], feature.dict()):
                config_modified = True

        # Only update DB if new features were added
        if config_modified:
            settings.config = current_config
            settings.updated_at = datetime.utcnow()
            flag_modified(settings, "config")
            db.add(settings)
            await db.commit()
            await db.refresh(settings)

    async def get_connector_toggles(self, db: AsyncSession, organization: Organization, current_user: User) -> dict:
        """Read the per-org connector enablement toggles (in-app admin switches)."""
        settings = await self.get_settings(db, organization, current_user)
        cfg = dict(settings.config) if isinstance(settings.config, dict) else {}
        conns = cfg.get("connectors") or {}
        return {"fabric_user_enabled": bool(conns.get("fabric_user_enabled", True))}

    async def set_connector_toggle(self, db: AsyncSession, organization: Organization, current_user: User, key: str, value: bool) -> dict:
        """Set a single connector toggle (e.g. fabric_user_enabled)."""
        from sqlalchemy.orm.attributes import flag_modified
        settings = await self.get_settings(db, organization, current_user)
        cfg = dict(settings.config) if isinstance(settings.config, dict) else {}
        conns = dict(cfg.get("connectors") or {})
        conns[key] = bool(value)
        cfg["connectors"] = conns
        settings.config = cfg
        flag_modified(settings, "config")
        await db.commit()
        return {"fabric_user_enabled": bool(conns.get("fabric_user_enabled", True))}

    async def update_settings(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        settings_data: OrganizationSettingsUpdate
    ):
        """Update organization settings"""
        settings = await self.get_settings(db, organization, current_user)
        # Ensure settings.config is a dictionary
        if settings.config is None:
             settings.config = {}
             flag_modified(settings, "config") # Mark as modified if initialized

        update_data = settings_data.dict(exclude_unset=True)

        if 'config' in update_data and update_data['config']:
            # Use dict() to ensure we have a mutable copy
            current_config = dict(settings.config)
            config_changed = False
            changed_ai_features: list = []

            # Handle AI features updates
            if 'ai_features' in update_data['config']:
                ai_features_updates = update_data['config']['ai_features']

                if 'ai_features' not in current_config:
                    current_config['ai_features'] = {}

                for feature_name, feature_data in ai_features_updates.items():
                    # Get current feature config from DB or default from schema
                    current_feature_dict = current_config['ai_features'].get(feature_name)
                    if not current_feature_dict:
                         # Feature not in DB, get default from schema
                         default_feature = OrganizationSettingsConfig().ai_features.get(feature_name)
                         if not default_feature: continue # Skip if feature unknown
                         current_feature_dict = default_feature.dict()
                         current_config['ai_features'][feature_name] = current_feature_dict # Add to config

                    # Create FeatureConfig object from current data to check properties
                    feature = FeatureConfig(**current_feature_dict)

                    if not feature.editable or feature.state == FeatureState.LOCKED:
                         # Allow updating non-editable/locked features only if the update doesn't change 'value' or 'state'
                         can_update = True
                         if 'value' in feature_data and feature_data['value'] != feature.value:
                             can_update = False
                         if 'state' in feature_data and feature_data['state'] != feature.state:
                             can_update = False

                         if not can_update:
                              raise HTTPException(
                                  status_code=403,
                                  detail=f"Feature '{feature_name}' cannot be modified"
                              )

                    # Apply updates from feature_data to the dictionary
                    original_dict = current_feature_dict.copy()
                    for field, value in feature_data.items():
                         if hasattr(feature, field): # Check if field is valid for FeatureConfig
                             current_feature_dict[field] = value

                    # Re-validate and potentially adjust state/value based on changes
                    updated_feature = FeatureConfig(**current_feature_dict)
                    current_config['ai_features'][feature_name] = updated_feature.dict()

                    if current_config['ai_features'][feature_name] != original_dict:
                        config_changed = True
                        changed_ai_features.append((feature_name, current_feature_dict.get('value')))


            # Handle top-level feature updates
            pii_changed = False
            for key, value_update in update_data['config'].items():
                if key != 'ai_features':
                    # PII protection (enterprise). Validated + normalized via the
                    # schema; custom rule regexes are compiled so a bad pattern is
                    # rejected at save time rather than silently skipped at runtime.
                    if key == 'pii_protection':
                        if not has_feature("pii_protection"):
                            raise HTTPException(
                                status_code=402,
                                detail="PII protection requires an enterprise license."
                            )
                        if not isinstance(value_update, dict):
                            raise HTTPException(status_code=400, detail="Invalid PII protection payload.")
                        from app.schemas.organization_settings_schema import PiiProtectionConfig
                        from app.ai.llm.pii.redactor import validate_pattern
                        existing = current_config.get('pii_protection') or {}
                        merged = {**existing, **value_update}
                        try:
                            validated = PiiProtectionConfig(**merged)
                        except Exception as e:
                            raise HTTPException(status_code=400, detail=f"Invalid PII protection config: {e}")
                        for rule in validated.custom_rules:
                            if not rule.patterns:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Rule '{rule.name}' must have at least one pattern."
                                )
                            for pat in rule.patterns:
                                err = validate_pattern(pat)
                                if err:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"Rule '{rule.name}': {err}"
                                    )
                        normalized = validated.dict()
                        if current_config.get('pii_protection') != normalized:
                            current_config['pii_protection'] = normalized
                            config_changed = True
                            pii_changed = True
                        continue
                    # Enterprise check for step_retention_days
                    if key == 'step_retention_days':
                        if not has_feature("step_retention_config"):
                            raise HTTPException(
                                status_code=402,
                                detail="Configuring step retention requires an enterprise license."
                            )
                        # Validate range (7-365 days)
                        new_value = value_update.get('value') if isinstance(value_update, dict) else value_update
                        if not isinstance(new_value, int) or new_value < 7 or new_value > 365:
                            raise HTTPException(
                                status_code=400,
                                detail="Step retention days must be between 7 and 365."
                            )
                    # Enterprise check for the Auto model router. Enabling it
                    # requires the license; turning it OFF is always allowed so a
                    # lapsed license can't strand an org with routing stuck on.
                    if key == 'model_routing':
                        new_value = value_update.get('value') if isinstance(value_update, dict) else value_update
                        if new_value and not has_feature("model_routing"):
                            raise HTTPException(
                                status_code=402,
                                detail="The Auto model router requires an enterprise license."
                            )
                    # Enterprise check for LLM fallback. Same shape as the Auto
                    # router: enabling needs the license, disabling is always
                    # allowed so a lapsed license can't strand it on.
                    if key == 'llm_fallback':
                        new_value = value_update.get('value') if isinstance(value_update, dict) else value_update
                        if new_value and not has_feature("llm_fallback"):
                            raise HTTPException(
                                status_code=402,
                                detail="LLM fallback requires an enterprise license."
                            )
                    # The fallback order itself is managed via POST /llm/fallback_order
                    # (validated against real models there); if it arrives through the
                    # generic settings path, still enforce shape + license.
                    if key == 'llm_fallback_order':
                        new_value = value_update.get('value') if isinstance(value_update, dict) else value_update
                        if not isinstance(new_value, list) or not all(isinstance(x, str) for x in new_value):
                            raise HTTPException(
                                status_code=400,
                                detail="llm_fallback_order must be a list of model ids."
                            )
                        if new_value and not has_feature("llm_fallback"):
                            raise HTTPException(
                                status_code=402,
                                detail="LLM fallback requires an enterprise license."
                            )
                        # Normalize so a {'value': [...]} payload is stored as the bare list.
                        value_update = new_value
                    # Range check for the Teams/WhatsApp/Google Chat conversation
                    # reuse windows (plain-int settings, edited from the Channels page).
                    if key in ('teams_session_max_age_hours', 'whatsapp_session_max_age_hours', 'google_chat_session_max_age_hours'):
                        new_value = value_update.get('value') if isinstance(value_update, dict) else value_update
                        if not isinstance(new_value, int) or isinstance(new_value, bool) or new_value < 1 or new_value > 720:
                            raise HTTPException(
                                status_code=400,
                                detail="Session staleness must be between 1 and 720 hours."
                            )
                        # Normalize so a {'value': N} payload is stored as the bare int.
                        value_update = new_value
                    # Get current config dict from DB or default from schema
                    current_value_dict = current_config.get(key)
                    is_feature = False
                    default_config = getattr(OrganizationSettingsConfig(), key, None)

                    if isinstance(default_config, FeatureConfig):
                         is_feature = True
                         if not current_value_dict:
                             # Feature not in DB, get default from schema
                             current_value_dict = default_config.dict()
                             current_config[key] = current_value_dict # Add to config


                    if is_feature and isinstance(current_value_dict, dict):
                         feature = FeatureConfig(**current_value_dict)

                         if not feature.editable or feature.state == FeatureState.LOCKED:
                            can_update = True
                            if isinstance(value_update, dict):
                                if 'value' in value_update and value_update['value'] != feature.value:
                                    can_update = False
                                if 'state' in value_update and value_update['state'] != feature.state:
                                    can_update = False
                            # Allow updating if it's not a dict (e.g. direct value update) only if value doesn't change
                            elif value_update != feature.value:
                                 can_update = False


                            if not can_update:
                                raise HTTPException(
                                     status_code=403,
                                     detail=f"Feature '{key}' cannot be modified"
                                )

                         original_dict = current_value_dict.copy()
                         if isinstance(value_update, dict):
                             for field, field_value in value_update.items():
                                 if hasattr(feature, field):
                                     current_value_dict[field] = field_value
                         else:
                             # Assume direct update is for the 'value' field
                             current_value_dict['value'] = value_update

                         # Re-validate and potentially adjust state/value
                         updated_feature = FeatureConfig(**current_value_dict)
                         current_config[key] = updated_feature.dict()

                         if current_config[key] != original_dict:
                             config_changed = True
                    elif key in current_config and current_config[key] != value_update : # Handle non-feature config update or addition
                         current_config[key] = value_update
                         config_changed = True
                    elif key not in current_config: # Handle adding new non-feature key
                         current_config[key] = value_update
                         config_changed = True


            if config_changed:
                settings.config = current_config
                settings.updated_at = datetime.utcnow()
                flag_modified(settings, "config")

                db.add(settings) # Add settings to session if changed
                await db.commit()
                await db.refresh(settings)

                # Drop the cached PII redactor so a toggle/rule change takes
                # effect immediately instead of waiting out the loader TTL.
                if pii_changed:
                    try:
                        from app.ai.llm.pii.loader import invalidate as invalidate_pii_cache
                        invalidate_pii_cache(str(organization.id))
                    except Exception:
                        pass

                # Telemetry: never the PII rule patterns/regexes themselves —
                # just whether protection is on and how many rules exist.
                try:
                    for _feature_name, _feature_value in changed_ai_features:
                        await telemetry.capture(
                            "ai_feature_updated",
                            {"feature_name": _feature_name, "new_value": _feature_value},
                            user_id=current_user.id,
                            org_id=organization.id,
                        )
                    if pii_changed:
                        _pii_cfg = current_config.get('pii_protection') or {}
                        await telemetry.capture(
                            "pii_protection_updated",
                            {
                                "enabled": _pii_cfg.get("enabled"),
                                "rule_count": len(_pii_cfg.get("custom_rules") or []),
                            },
                            user_id=current_user.id,
                            org_id=organization.id,
                        )
                except Exception:
                    pass

                # Audit log
                try:
                    await audit_service.log(
                        db=db,
                        organization_id=str(organization.id),
                        action="settings.updated",
                        user_id=str(current_user.id),
                        resource_type="organization_settings",
                        resource_id=str(settings.id),
                        details={"changed_keys": list(update_data.get('config', {}).keys())},
                    )
                except Exception:
                    pass

        return settings

    async def set_general_icon(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        file: UploadFile
    ):
        """Validate, process (resize preserving aspect ratio), store icon on disk and update settings.general.icon fields."""
        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}

        content_type = (file.content_type or "").lower()
        if content_type not in ("image/png", "image/jpeg", "image/jpg"):
            raise HTTPException(status_code=400, detail="Unsupported image type. Use PNG or JPEG")

        raw = await file.read()
        if len(raw) > 512 * 1024:
            raise HTTPException(status_code=400, detail="Icon too large. Max 512KB")

        try:
            image = Image.open(BytesIO(raw))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid image file")

        # Convert to RGBA for consistent output
        image = image.convert("RGBA")
        width, height = image.size

        # Resize to fit within max bounds while preserving aspect ratio
        max_width, max_height = 512, 256
        scale = min(max_width / width, max_height / height, 1.0)  # Don't upscale
        if scale < 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # storage path
        base_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads", "branding"))
        os.makedirs(base_dir, exist_ok=True)

        digest = hashlib.sha256(raw).hexdigest()[:16]
        filename = f"{organization.id}-{digest}.png"
        file_path = os.path.join(base_dir, filename)

        # save as PNG
        with open(file_path, "wb") as f:
            buf = BytesIO()
            image.save(buf, format="PNG")
            f.write(buf.getvalue())

        # update settings
        general = dict(settings.config.get("general", {}))
        general["icon_key"] = filename
        general["icon_url"] = f"/api/general/icon/{filename}"
        settings.config["general"] = general

        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        # Audit log
        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.icon_uploaded",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={"filename": filename},
            )
        except Exception:
            pass

        return settings

    async def remove_general_icon(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User
    ):
        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}

        general = dict(settings.config.get("general", {}))
        icon_key = general.get("icon_key")
        if icon_key:
            base_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads", "branding"))
            file_path = os.path.join(base_dir, icon_key)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        general["icon_key"] = None
        general["icon_url"] = None
        settings.config["general"] = general

        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        # Audit log
        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.icon_removed",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={},
            )
        except Exception:
            pass

        return settings

    async def create_default_settings(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User
    ):
        """Create default settings for a new organization"""
        config = OrganizationSettingsConfig()
        # Use the .dict() method which now correctly handles value/state consistency
        settings = OrganizationSettings(
            organization_id=organization.id,
            config=config.dict()
        )

        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        return settings

    async def get_auto_provision(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> AutoProvisionSchema:
        """Return the role given to people the identity provider admits."""
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("auto_provision") or {}
        return AutoProvisionSchema(role=str(raw.get("role") or "member"))

    async def update_auto_provision(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        payload: AutoProvisionSchema,
    ) -> AutoProvisionSchema:
        """Validate and persist the auto-provisioned role.

        The role must exist — a typo here would hand every future arrival a
        membership with no permissions behind it, which looks like a working
        sign-in and behaves like a broken one.
        """
        from app.models.role import Role
        from sqlalchemy import or_, and_

        role_name = (payload.role or "").strip() or "member"
        role_res = await db.execute(
            select(Role).where(
                Role.name == role_name,
                Role.deleted_at.is_(None),
                or_(
                    and_(Role.is_system == True, Role.organization_id.is_(None)),
                    Role.organization_id == organization.id,
                ),
            )
        )
        if not role_res.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Role '{role_name}' not found")

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}
        current_config = dict(settings.config)
        current_config["auto_provision"] = {"role": role_name}
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.auto_provision_role_updated",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={"role": role_name},
            )
        except Exception:
            pass

        return AutoProvisionSchema(role=role_name)

    async def resolve_auto_provision_role(
        self, db: AsyncSession, organization_id: str
    ) -> str:
        """The role for an auto-provisioned account, resolved WITHOUT a caller.

        ★Deliberately not `get_auto_provision`: this runs inside the sign-in
        path, where there is no `current_user` yet — the whole point is that
        the account is being made right now — and `get_settings` takes one.

        ★Falls back to "member" on anything unexpected. A missing setting must
        still produce a usable account; refusing to place someone is the
        failure this phase exists to remove.
        """
        try:
            row = (await db.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == str(organization_id)
                )
            )).scalars().first()
            raw = ((row.config if row is not None else None) or {}).get("auto_provision") or {}
            role = str(raw.get("role") or "").strip()
            return role or "member"
        except Exception:  # noqa: BLE001
            return "member"

    async def get_signup_policy(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> SignupPolicySchema:
        """Return the org's signup policy, defaulting to an empty/disabled one."""
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("signup_policy") or {}
        return SignupPolicySchema(
            enabled=bool(raw.get("enabled", False)),
            allowed_domains=list(raw.get("allowed_domains", []) or []),
            auto_invite_role=str(raw.get("auto_invite_role") or "member"),
        )

    async def update_signup_policy(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        policy: SignupPolicySchema,
    ) -> SignupPolicySchema:
        """Validate and persist the org's signup policy.

        Validation:
        - domains are normalized (lowercase, trimmed), non-empty, contain a dot,
          no '@' / whitespace / wildcard, deduped
        - auto_invite_role must match an existing system or per-org role
        """
        if not has_feature("domain_signup"):
            raise HTTPException(
                status_code=402,
                detail="Domain-based signup requires an enterprise license.",
            )

        from app.models.role import Role
        from sqlalchemy import or_, and_

        normalized_domains: list[str] = []
        seen: set[str] = set()
        for raw in (policy.allowed_domains or []):
            if not isinstance(raw, str):
                raise HTTPException(status_code=400, detail="Each domain must be a string")
            d = raw.strip().lower()
            if not d:
                continue
            if "@" in d or "*" in d or any(ch.isspace() for ch in d):
                raise HTTPException(status_code=400, detail=f"Invalid domain: {raw!r}")
            if "." not in d or len(d) > 253:
                raise HTTPException(status_code=400, detail=f"Invalid domain: {raw!r}")
            if d in seen:
                continue
            seen.add(d)
            normalized_domains.append(d)

        role_name = (policy.auto_invite_role or "").strip() or "member"
        role_res = await db.execute(
            select(Role).where(
                Role.name == role_name,
                Role.deleted_at.is_(None),
                or_(
                    and_(Role.is_system == True, Role.organization_id.is_(None)),
                    Role.organization_id == organization.id,
                ),
            )
        )
        if not role_res.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Role '{role_name}' not found")

        if policy.enabled and not normalized_domains:
            raise HTTPException(
                status_code=400,
                detail="At least one allowed domain is required when signup policy is enabled",
            )

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}

        current_config = dict(settings.config)
        current_config["signup_policy"] = {
            "enabled": bool(policy.enabled),
            "allowed_domains": normalized_domains,
            "auto_invite_role": role_name,
        }
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.signup_policy_updated",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={
                    "enabled": bool(policy.enabled),
                    "allowed_domains": normalized_domains,
                    "auto_invite_role": role_name,
                },
            )
        except Exception:
            pass

        return SignupPolicySchema(
            enabled=bool(policy.enabled),
            allowed_domains=normalized_domains,
            auto_invite_role=role_name,
        )

    async def get_entra_profile_sync(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> "EntraProfileSyncConfig":
        """Return the org's Entra profile-sync setting (disabled by default)."""
        from app.schemas.organization_settings_schema import (
            EntraProfileSyncConfig,
            ENTRA_PROFILE_SYNC_DEFAULT_FIELDS,
        )
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("entra_profile_sync") or {}
        return EntraProfileSyncConfig(
            enabled=bool(raw.get("enabled", False)),
            fields=list(raw.get("fields") or ENTRA_PROFILE_SYNC_DEFAULT_FIELDS),
        )

    async def update_entra_profile_sync(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        payload: "EntraProfileSyncConfig",
    ) -> "EntraProfileSyncConfig":
        """Validate and persist the org's Entra profile-sync setting.

        Selected fields are filtered to the User.Read-safe allowlist so a
        misconfiguration can't request an admin-consent-only Graph field. When
        the resulting list is empty, fall back to the sensible default subset.
        """
        from app.schemas.organization_settings_schema import (
            EntraProfileSyncConfig,
            ENTRA_PROFILE_SYNC_ALLOWED_FIELDS,
            ENTRA_PROFILE_SYNC_DEFAULT_FIELDS,
        )

        allowed = set(ENTRA_PROFILE_SYNC_ALLOWED_FIELDS)
        # Preserve the admin's ordering; drop anything outside the allowlist.
        seen: set[str] = set()
        fields: list[str] = []
        for f in (payload.fields or []):
            if f in allowed and f not in seen:
                seen.add(f)
                fields.append(f)
        if not fields:
            fields = list(ENTRA_PROFILE_SYNC_DEFAULT_FIELDS)

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}

        current_config = dict(settings.config)
        current_config["entra_profile_sync"] = {
            "enabled": bool(payload.enabled),
            "fields": fields,
        }
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.entra_profile_sync_updated",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={"enabled": bool(payload.enabled), "fields": fields},
            )
        except Exception:
            pass

        return EntraProfileSyncConfig(enabled=bool(payload.enabled), fields=fields)

    async def preview_entra_profile(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Fetch sample values from the current admin's own Graph /me profile.

        Powers the settings UI so the admin sees what each attribute would
        actually contain before choosing which to include in AI context. Reads
        every allowlisted field (unset ones come back as null). Best-effort:
        returns ``connected=False`` with a reason when the admin has no Entra
        login on file or Graph rejects the token, rather than erroring.
        """
        from app.schemas.organization_settings_schema import (
            ENTRA_PROFILE_SYNC_ALLOWED_FIELDS,
        )
        from app.ee.oidc.profile_service import (
            EntraReauthRequired,
            fetch_profile_fields,
            get_entra_graph_token,
        )

        result = {
            "connected": False,
            "samples": {},
            "allowed_fields": ENTRA_PROFILE_SYNC_ALLOWED_FIELDS,
            "error": None,
        }

        token = await get_entra_graph_token(db, current_user)
        if not token:
            result["error"] = "no_entra_login"
            return result

        try:
            samples = await fetch_profile_fields(
                db, current_user, ENTRA_PROFILE_SYNC_ALLOWED_FIELDS, access_token=token
            )
        except EntraReauthRequired:
            result["error"] = "reauth_required"
            return result
        except Exception as e:
            result["error"] = f"graph_error: {e}"
            return result

        result["connected"] = True
        result["samples"] = samples
        return result

    async def get_google_profile_sync(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> "GoogleProfileSyncConfig":
        """Return the org's Google profile-sync setting (disabled by default)."""
        from app.schemas.organization_settings_schema import (
            GoogleProfileSyncConfig,
            GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS,
        )
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("google_profile_sync") or {}
        return GoogleProfileSyncConfig(
            enabled=bool(raw.get("enabled", False)),
            fields=list(raw.get("fields") or GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS),
        )

    async def update_google_profile_sync(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        payload: "GoogleProfileSyncConfig",
    ) -> "GoogleProfileSyncConfig":
        """Validate and persist the org's Google profile-sync setting.

        Selected fields are filtered to the allowlist (everything readable with
        the login-granted scopes). When the resulting list is empty, fall back
        to the sensible default subset.
        """
        from app.schemas.organization_settings_schema import (
            GoogleProfileSyncConfig,
            GOOGLE_PROFILE_SYNC_ALLOWED_FIELDS,
            GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS,
        )

        allowed = set(GOOGLE_PROFILE_SYNC_ALLOWED_FIELDS)
        # Preserve the admin's ordering; drop anything outside the allowlist.
        seen: set[str] = set()
        fields: list[str] = []
        for f in (payload.fields or []):
            if f in allowed and f not in seen:
                seen.add(f)
                fields.append(f)
        if not fields:
            fields = list(GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS)

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}

        current_config = dict(settings.config)
        current_config["google_profile_sync"] = {
            "enabled": bool(payload.enabled),
            "fields": fields,
        }
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.google_profile_sync_updated",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={"enabled": bool(payload.enabled), "fields": fields},
            )
        except Exception:
            pass

        return GoogleProfileSyncConfig(enabled=bool(payload.enabled), fields=fields)

    async def preview_google_profile(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Fetch sample values from the current admin's own Google profile.

        Powers the settings UI so the admin sees what each attribute would
        actually contain before choosing which to include in AI context.
        Best-effort: returns ``connected=False`` with a reason when the admin
        has no Google login on file or the token has expired, rather than
        erroring.
        """
        from app.schemas.organization_settings_schema import (
            GOOGLE_PROFILE_SYNC_ALLOWED_FIELDS,
        )
        from app.ee.oidc.google_profile_service import (
            GoogleReauthRequired,
            fetch_profile_fields,
            get_google_access_token,
        )

        result = {
            "connected": False,
            "samples": {},
            "allowed_fields": GOOGLE_PROFILE_SYNC_ALLOWED_FIELDS,
            "error": None,
        }

        token = await get_google_access_token(db, current_user)
        if not token:
            result["error"] = "no_google_login"
            return result

        try:
            samples = await fetch_profile_fields(
                db, current_user, GOOGLE_PROFILE_SYNC_ALLOWED_FIELDS, access_token=token
            )
        except GoogleReauthRequired:
            result["error"] = "reauth_required"
            return result
        except Exception as e:
            result["error"] = f"google_error: {e}"
            return result

        result["connected"] = True
        result["samples"] = samples
        return result

    async def get_smtp(self, db: AsyncSession, organization: Organization, current_user: User):
        """Return the org's SMTP server config (password redacted)."""
        from app.schemas.organization_settings_schema import OrgSmtpSchema
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("smtp") or {}
        return OrgSmtpSchema(
            enabled=bool(raw.get("enabled", False)),
            host=raw.get("host"),
            port=int(raw.get("port") or 587),
            security=raw.get("security") or "starttls",
            username=raw.get("username"),
            password_set=bool(raw.get("password_enc")),
            from_address=raw.get("from_address"),
            from_name=raw.get("from_name"),
            validate_certs=bool(raw.get("validate_certs", True)),
        )

    async def update_smtp(self, db: AsyncSession, organization: Organization, current_user: User, data):
        """Persist the org's SMTP server; the password is Fernet-encrypted."""
        from app.schemas.organization_settings_schema import OrgSmtpSchema
        from app.services.email.secrets import encrypt_secret

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}
        current_config = dict(settings.config)
        existing = current_config.get("smtp") or {}

        smtp = {
            "enabled": bool(data.enabled),
            "host": (data.host or "").strip() or None,
            "port": int(data.port or 587),
            "security": data.security or "starttls",
            "username": (data.username or "").strip() or None,
            "from_address": (data.from_address or "").strip() or None,
            "from_name": data.from_name,
            "validate_certs": bool(data.validate_certs),
            # Keep the existing encrypted password unless a new one is supplied.
            "password_enc": existing.get("password_enc"),
        }
        if data.password:
            smtp["password_enc"] = encrypt_secret(data.password)

        if smtp["enabled"] and not smtp["host"]:
            raise HTTPException(status_code=400, detail="SMTP host is required when enabled")

        current_config["smtp"] = smtp
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db, organization_id=str(organization.id),
                action="settings.org_smtp_updated", user_id=str(current_user.id),
                resource_type="organization_settings", resource_id=str(settings.id),
                details={"enabled": smtp["enabled"], "host": smtp["host"]},
            )
        except Exception:
            pass

        return await self.get_smtp(db, organization, current_user)

    async def test_smtp(self, db: AsyncSession, organization: Organization, current_user: User) -> dict:
        """Probe the org's saved SMTP server (connect + auth, no send)."""
        from app.services.email_client_resolver import get_org_smtp
        from app.services.email.sender import SmtpConfig, _tls_context
        import aiosmtplib

        smtp = await get_org_smtp(db, organization.id)
        if not (smtp and smtp.get("host")):
            return {"success": False, "smtp": "no SMTP host configured"}
        cfg = SmtpConfig(
            host=smtp["host"], port=int(smtp.get("port") or 587),
            username=smtp.get("username"), password=smtp.get("password"),
            security=smtp.get("security") or "starttls",
            validate_certs=bool(smtp.get("validate_certs", True)),
        ).resolved()
        try:
            kwargs = dict(
                hostname=cfg.host, port=cfg.port,
                use_tls=(cfg.security == "ssl"),
                start_tls=(cfg.security == "starttls"), timeout=15,
            )
            tls_context = _tls_context(cfg)
            if tls_context is not None:
                kwargs["tls_context"] = tls_context
            client = aiosmtplib.SMTP(**kwargs)
            await client.connect()
            if cfg.username and cfg.password:
                await client.login(cfg.username, cfg.password)
            await client.quit()
            return {"success": True, "smtp": "ok"}
        except Exception as e:
            return {"success": False, "smtp": f"failed: {e}"}

    # --- LDAP directory sync (enterprise) ---------------------------------
    #
    # LDAP config is stored per-org in ``config.ldap`` (JSON) with the bind
    # password Fernet-encrypted as ``bind_password_enc`` — mirroring SMTP. When
    # an org has not saved its own block, everything falls back to the global
    # ``bow-config.yaml`` ldap section so existing file-based setups keep working.
    _LDAP_FIELDS = (
        "url", "bind_dn", "use_ssl", "start_tls", "base_dn",
        "user_search_base", "user_search_filter", "user_email_attribute",
        "user_login_attribute",
        "user_name_attribute", "group_search_base", "group_search_filter",
        "group_name_attribute", "group_member_attribute", "group_member_format",
        "sync_interval_minutes", "auto_provision_users", "connection_timeout",
        "page_size",
    )

    async def get_ldap(self, db: AsyncSession, organization: Organization, current_user: User):
        """Return the org's LDAP config (bind password redacted).

        Falls back to bow-config.yaml values when the org has no saved block, so
        the form shows whatever is currently in effect.
        """
        from app.schemas.organization_settings_schema import OrgLdapSchema
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("ldap")
        if raw:
            data = {k: raw.get(k) for k in self._LDAP_FIELDS if raw.get(k) is not None}
            return OrgLdapSchema(
                enabled=bool(raw.get("enabled", False)),
                bind_password_set=bool(raw.get("bind_password_enc")),
                source_db=True,
                **data,
            )
        # Fallback: reflect the file config (never exposes its password).
        from app.settings.config import settings as app_settings
        fc = app_settings.dash_config.ldap
        return OrgLdapSchema(
            enabled=bool(fc.enabled),
            url=fc.url or None,
            bind_dn=fc.bind_dn,
            bind_password_set=bool(fc.bind_password),
            use_ssl=fc.use_ssl,
            start_tls=fc.start_tls,
            base_dn=fc.base_dn or None,
            user_search_base=fc.user_search_base,
            user_search_filter=fc.user_search_filter,
            user_email_attribute=fc.user_email_attribute,
            user_login_attribute=fc.user_login_attribute,
            user_name_attribute=fc.user_name_attribute,
            group_search_base=fc.group_search_base,
            group_search_filter=fc.group_search_filter,
            group_name_attribute=fc.group_name_attribute,
            group_member_attribute=fc.group_member_attribute,
            group_member_format=fc.group_member_format,
            sync_interval_minutes=fc.sync_interval_minutes,
            auto_provision_users=fc.auto_provision_users,
            connection_timeout=fc.connection_timeout,
            page_size=fc.page_size,
            source_db=False,
        )

    async def update_ldap(self, db: AsyncSession, organization: Organization, current_user: User, data):
        """Persist the org's LDAP config; the bind password is Fernet-encrypted.

        ★★★A MERGE, not a replace. It used to rebuild the whole block from the
        payload — `ldap[f] = getattr(data, f)` for every field — so anything the
        caller omitted was written as that field's pydantic DEFAULT and the
        request still answered 200. Not null: the default, which is worse,
        because a reset looks like a decision.

        Measured on 0.0.543.15, both halves inside one hour of testing:

          * a PUT of only ``{"group_search_filter": …}`` wiped ``enabled``,
            ``url``, ``bind_dn`` and ``base_dn``; the next call answered
            ``400 "LDAP is not configured"``. Directory sign-in gone for the
            whole organization, from a request that reported success.
          * a PUT naming 13 fields but omitting ``auto_provision_users`` set it
            to its default of False, so only BRAND-NEW people were refused
            (``ldap_not_provisioned``) while existing accounts kept working —
            which reads as an intermittent directory fault, not a config change.
            ``use_ssl`` flipped to its default True against an ``ldap://`` URL in
            the same request.

        ★The author already knew this shape and solved it for exactly one field:
        ``bind_password_enc`` is preserved on omission. Nothing else was.

        ★``model_fields_set`` is the only thing that separates "field omitted"
        from "field explicitly null" — the same landmine as
        ``ReportScheduleRequest.cron_expression_supplied``, where a pause posting
        only ``is_active`` would otherwise have deleted the schedule and its
        recipients. Anything reasoning off the VALUE alone has already lost the
        distinction, so an explicit ``null`` still CLEARS a field here; only
        absence preserves.

        ★The settings form posts every field, so the UI's behaviour is
        unchanged. This is for scripts, automation and partial API calls.
        """
        from app.services.email.secrets import encrypt_secret

        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None:
            settings.config = {}
        current_config = dict(settings.config)
        existing = current_config.get("ldap") or {}

        # What the caller actually NAMED. Pydantic v2 records this; v1 spells it
        # `__fields_set__`. Falling back to "everything" would restore the old
        # replace semantics silently, so an unknown model is treated as a full
        # payload only because that is what it was before this change.
        sent = getattr(data, "model_fields_set", None)
        if sent is None:
            sent = getattr(data, "__fields_set__", None)
        if sent is None:
            sent = set(self._LDAP_FIELDS) | {"enabled"}

        def _clean(value):
            if isinstance(value, str):
                return value.strip() or None
            return value

        ldap = {}
        for f in ("enabled",) + tuple(self._LDAP_FIELDS):
            if f in sent:
                # Named by the caller — including an explicit null, which clears.
                ldap[f] = bool(getattr(data, f)) if f == "enabled" else _clean(getattr(data, f))
            elif f in existing:
                # Omitted, and we already hold a value: keep it untouched.
                ldap[f] = existing[f]
            else:
                # Omitted on a FIRST write — there is nothing to preserve, so the
                # schema default is the right answer and a fresh block still ends
                # up complete rather than half-populated.
                ldap[f] = bool(getattr(data, f)) if f == "enabled" else _clean(getattr(data, f))

        # Keep the existing encrypted password unless a new one is supplied.
        ldap["bind_password_enc"] = existing.get("bind_password_enc")
        if data.bind_password:
            ldap["bind_password_enc"] = encrypt_secret(data.bind_password)

        if ldap["enabled"]:
            if not ldap.get("url"):
                raise HTTPException(status_code=400, detail="LDAP server URL is required when enabled")
            if not ldap.get("base_dn"):
                raise HTTPException(status_code=400, detail="Base DN is required when enabled")

        current_config["ldap"] = ldap
        settings.config = current_config
        settings.updated_at = datetime.utcnow()
        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        try:
            await audit_service.log(
                db=db, organization_id=str(organization.id),
                action="settings.org_ldap_updated", user_id=str(current_user.id),
                resource_type="organization_settings", resource_id=str(settings.id),
                details={"enabled": ldap["enabled"], "url": ldap.get("url")},
            )
        except Exception:
            pass

        return await self.get_ldap(db, organization, current_user)

    async def resolve_ldap_config(self, db: AsyncSession, organization: Organization):
        """Build a runtime ``LDAPConfig`` for this org.

        Uses the org's saved DB block (decrypting the bind password) when present;
        otherwise falls back to the global bow-config.yaml ldap section. Returns a
        ``dash_config.LDAPConfig`` so the existing connection/sync services are
        unchanged.
        """
        from app.settings.dash_config import LDAPConfig
        from app.settings.config import settings as app_settings
        from app.services.email.secrets import decrypt_secret

        settings = await self.get_settings(db, organization, None)
        raw = (settings.config or {}).get("ldap")
        if not raw:
            return app_settings.dash_config.ldap  # file fallback
        data = {k: raw.get(k) for k in self._LDAP_FIELDS if raw.get(k) is not None}
        data["enabled"] = bool(raw.get("enabled", False))
        if raw.get("bind_password_enc"):
            data["bind_password"] = decrypt_secret(raw.get("bind_password_enc"))
        return LDAPConfig(**data)

    async def resolve_login_ldap_config(self, db: AsyncSession):
        """The LDAP config a LOGIN should authenticate against, and the org that owns it.

        Returns ``(LDAPConfig, organization_id | None)``.

        ★Why this exists at all. ``resolve_ldap_config`` needs an Organization,
        and a login has none — nobody has picked an org yet, and the whole point
        is that this person may not belong to one. So the login path read
        ``settings.dash_config.ldap`` (the FILE) instead, while the settings form
        writes to the DATABASE. Configure LDAP in the UI and ``enabled`` stayed
        false forever: the sync job worked, and not one directory user could
        sign in. Two stores, one of them never consulted by the code that
        matters.

        ★★★Which org's block may answer that question is a SECURITY decision,
        not a convenience. This used to scan every organization and take the
        first with ``enabled`` on, ordered by id. An org admin can write that
        block — it is an ordinary field on the Settings ▸ Identity Provider
        form — so on a multi-tenant instance one tenant could point the LOGIN
        page at a directory they control, pick an id that sorts first, and then
        sign in as anybody: a directory that binds successfully for any
        credentials makes ``get_by_email(<whatever address was typed>)`` return
        the real local account, instance owner included. Resolving the account
        from the directory's own mail attribute would not have helped — against
        a hostile directory that attribute is attacker-controlled too.

        The rule now:

          designated org      → that org's block, and only that one
          exactly one org     → that org's block (today's behaviour, exact —
                                which is every install of this product so far)
          several, none named → no org block at all; fall back to the file

        The designation lives in the instance-wide singleton
        (``instance_settings.config['login_ldap_org_id']``), which no tenant can
        write. Absent designation with more than one candidate FAILS CLOSED and
        says so in the log, because an instance that quietly stops honouring a
        saved directory looks exactly like a directory that is down.

        Falls back to the file config, so a bow-config.yaml setup is unchanged.
        The org id is returned because a directory that vouched for somebody is
        also the right place to put them.
        """
        from app.models.organization_settings import OrganizationSettings
        from app.models.organization import Organization
        from app.models.instance_settings import InstanceSettings
        from app.settings.dash_config import LDAPConfig
        from app.settings.config import settings as app_settings
        from app.services.email.secrets import decrypt_secret
        import logging

        _logger = logging.getLogger(__name__)

        def _to_config(raw):
            data = {k: raw.get(k) for k in self._LDAP_FIELDS if raw.get(k) is not None}
            data["enabled"] = True
            if raw.get("bind_password_enc"):
                data["bind_password"] = decrypt_secret(raw.get("bind_password_enc"))
            return LDAPConfig(**data)

        # Which org, if any, the INSTANCE has named as its directory owner.
        designated = None
        try:
            row = (await db.execute(select(InstanceSettings))).scalars().first()
            if row is not None:
                designated = (getattr(row, "config", None) or {}).get("login_ldap_org_id")
        except Exception as e:  # a missing/unreadable singleton must not be a login outage
            _logger.warning("Could not read the instance login-directory setting: %s", e)

        rows = (await db.execute(select(OrganizationSettings))).scalars().all()
        enabled = {
            str(r.organization_id): (r.config or {}).get("ldap")
            for r in rows
            if ((r.config or {}).get("ldap") or {}).get("enabled")
        }

        if designated:
            raw = enabled.get(str(designated))
            if raw:
                return _to_config(raw), str(designated)
            # ★Do NOT fall through to the others. Falling back on a typo is how
            # the hole would come straight back.
            _logger.warning(
                "The organization named as the login directory (%s) has no enabled "
                "LDAP block; no directory will authenticate logins.", designated,
            )
            return app_settings.dash_config.ldap, None

        if len(enabled) == 1:
            org_id, raw = next(iter(enabled.items()))
            # One candidate is only trustworthy when it is also the only tenant.
            # On a multi-tenant instance the sole configured block still belongs
            # to somebody, and nobody said it speaks for everybody.
            org_count = len((await db.execute(select(Organization.id))).scalars().all())
            if org_count <= 1:
                return _to_config(raw), org_id
            _logger.warning(
                "Organization %s has an LDAP block but this instance has %d "
                "organizations and none is designated as the login directory. "
                "Refusing to use it — set instance_settings.config['login_ldap_org_id'].",
                org_id, org_count,
            )
        elif len(enabled) > 1:
            _logger.warning(
                "%d organizations have an enabled LDAP block and none is designated "
                "as the login directory. Refusing to pick one.", len(enabled),
            )

        return app_settings.dash_config.ldap, None

    async def get_locale(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Return the org's locale override + system default + enabled list."""
        from app.settings.config import settings as app_settings
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("locale")
        i18n = app_settings.dash_config.i18n
        return {
            "org_locale": raw if raw in i18n.enabled_locales else None,
            "default_locale": i18n.default_locale,
            "enabled_locales": list(i18n.enabled_locales),
            "effective_locale": raw if raw in i18n.enabled_locales else i18n.default_locale,
        }

    async def update_locale(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        locale: str | None,
    ) -> dict:
        """Set or clear the org locale override. None/empty clears to system default."""
        from app.settings.config import settings as app_settings
        i18n = app_settings.dash_config.i18n

        new_locale: str | None
        if locale in (None, ""):
            new_locale = None
        elif locale in i18n.enabled_locales:
            new_locale = locale
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Locale '{locale}' is not enabled. Enabled: {i18n.enabled_locales}",
            )

        settings = await self.get_settings(db, organization, current_user)
        current_config = dict(settings.config or {})
        if current_config.get("locale") != new_locale:
            current_config["locale"] = new_locale
            settings.config = current_config
            settings.updated_at = datetime.utcnow()
            flag_modified(settings, "config")
            db.add(settings)
            await db.commit()
            await db.refresh(settings)

            try:
                await audit_service.log(
                    db=db,
                    organization_id=str(organization.id),
                    action="settings.locale_updated",
                    user_id=str(current_user.id),
                    resource_type="organization_settings",
                    resource_id=str(settings.id),
                    details={"locale": new_locale},
                )
            except Exception:
                pass

        return {
            "org_locale": new_locale,
            "default_locale": i18n.default_locale,
            "enabled_locales": list(i18n.enabled_locales),
            "effective_locale": new_locale or i18n.default_locale,
        }

    async def get_timezone(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Return the org's timezone override + the effective tz (UTC fallback)."""
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("timezone")
        from app.services.reindex_schedule import resolve_timezone
        effective = resolve_timezone(raw).key
        return {
            "org_timezone": raw,
            "default_timezone": "UTC",
            "effective_timezone": effective,
        }

    async def update_timezone(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        timezone: str | None,
    ) -> dict:
        """Set or clear the org timezone. None/empty clears to UTC default."""
        from zoneinfo import ZoneInfo, available_timezones

        new_tz: str | None
        if timezone in (None, ""):
            new_tz = None
        elif timezone in available_timezones():
            new_tz = timezone
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Timezone '{timezone}' is not a valid IANA timezone.",
            )

        settings = await self.get_settings(db, organization, current_user)
        current_config = dict(settings.config or {})
        if current_config.get("timezone") != new_tz:
            current_config["timezone"] = new_tz
            settings.config = current_config
            settings.updated_at = datetime.utcnow()
            flag_modified(settings, "config")
            db.add(settings)
            await db.commit()
            await db.refresh(settings)

            try:
                await audit_service.log(
                    db=db,
                    organization_id=str(organization.id),
                    action="settings.timezone_updated",
                    user_id=str(current_user.id),
                    resource_type="organization_settings",
                    resource_id=str(settings.id),
                    details={"timezone": new_tz},
                )
            except Exception:
                pass

            # ★ Re-register every live cron for this org in the new timezone.
            #
            # A job carries the timezone it was registered WITH; changing this
            # setting used to change nothing already scheduled. The comment in
            # scheduled_prompt_service._register_job said a later change is
            # "picked up on the next update/restart" — true, and it means the
            # setting silently disagreed with every existing schedule until
            # someone happened to edit it. Set the org to Asia/Yangon and your
            # 8 AM refresh still ran at 8 AM UTC, with nothing in the UI saying so.
            #
            # Best-effort: a scheduler failure must not fail the settings save.
            try:
                await self._reschedule_org_crons(db, organization)
            except Exception:
                # No module-level logger OR logging import in this file — every
                # other handler here imports it locally.
                import logging
                logging.getLogger(__name__).warning(
                    "Timezone changed but re-registering crons failed", exc_info=True
                )

        return {
            "org_timezone": new_tz,
            "default_timezone": "UTC",
            "effective_timezone": new_tz or "UTC",
        }

    async def _reschedule_org_crons(self, db: AsyncSession, organization: Organization) -> int:
        """Re-register this org's report refreshes and scheduled prompts.

        Re-registration is what applies a timezone: APScheduler resolves the
        zone at add_job time. Both mechanisms are covered — fixing only one
        would leave the two disagreeing about the same wall-clock time, which
        is the state this is here to end.
        """
        from sqlalchemy import select as _select
        from app.models.report import Report
        from app.models.scheduled_prompt import ScheduledPrompt
        from app.services.scheduled_prompt_service import ScheduledPromptService
        from app.services.report_service import ReportService

        n = 0
        sp_service = ScheduledPromptService()
        for sp in (await db.execute(
            _select(ScheduledPrompt)
            .join(Report, ScheduledPrompt.report_id == Report.id)
            .where(
                Report.organization_id == organization.id,
                ScheduledPrompt.deleted_at.is_(None),
                ScheduledPrompt.is_active.is_(True),
            )
        )).scalars().all():
            sp_service._register_job(sp)
            n += 1

        rs = ReportService()
        for r in (await db.execute(
            _select(Report).where(
                Report.organization_id == organization.id,
                Report.cron_schedule.isnot(None),
                Report.status != 'archived',
            )
        )).scalars().all():
            rs._reregister_report_cron(r)
            n += 1
        return n

    async def get_week_start(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Return the org's first-day-of-week override + the effective value.

        ``effective_week_start`` resolves None/"auto" against the org locale
        (Hebrew/Arabic -> Sunday, otherwise Monday) so the settings UI can show
        what the AI will actually use.
        """
        from app.ai.agents.planner.clock import resolve_first_weekday, _WEEKDAYS
        settings = await self.get_settings(db, organization, current_user)
        raw = (settings.config or {}).get("week_start")
        locale = (settings.config or {}).get("locale")
        effective = _WEEKDAYS[resolve_first_weekday(raw, locale)].lower()
        return {
            "org_week_start": raw,
            "options": list(self._WEEK_START_OPTIONS),
            "effective_week_start": effective,
        }

    _WEEK_START_OPTIONS = ("sunday", "monday", "saturday")

    async def update_week_start(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        week_start: str | None,
    ) -> dict:
        """Set or clear the org first-day-of-week. None/empty/"auto" -> derive from locale."""
        new_week_start: str | None
        if week_start in (None, "", "auto"):
            new_week_start = None
        elif week_start.lower() in self._WEEK_START_OPTIONS:
            new_week_start = week_start.lower()
        else:
            raise HTTPException(
                status_code=422,
                detail=f"week_start must be one of {self._WEEK_START_OPTIONS}, 'auto', or null.",
            )

        settings = await self.get_settings(db, organization, current_user)
        current_config = dict(settings.config or {})
        if current_config.get("week_start") != new_week_start:
            current_config["week_start"] = new_week_start
            settings.config = current_config
            settings.updated_at = datetime.utcnow()
            flag_modified(settings, "config")
            db.add(settings)
            await db.commit()
            await db.refresh(settings)

            try:
                await audit_service.log(
                    db=db,
                    organization_id=str(organization.id),
                    action="settings.week_start_updated",
                    user_id=str(current_user.id),
                    resource_type="organization_settings",
                    resource_id=str(settings.id),
                    details={"week_start": new_week_start},
                )
            except Exception:
                pass

        from app.ai.agents.planner.clock import resolve_first_weekday, _WEEKDAYS
        locale = current_config.get("locale")
        effective = _WEEKDAYS[resolve_first_weekday(new_week_start, locale)].lower()
        return {
            "org_week_start": new_week_start,
            "options": list(self._WEEK_START_OPTIONS),
            "effective_week_start": effective,
        }

    async def update_ai_feature(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        feature_name: str,
        # Changed parameter name from 'enabled' to 'value'
        new_value: bool # Assuming this endpoint is for boolean toggles
    ):
        """Update a specific AI feature setting's value"""
        settings = await self.get_settings(db, organization, current_user)
        if settings.config is None: settings.config = {} # Ensure config exists

        # Get the feature configuration using the model's method
        feature = settings.get_config(feature_name)

        if not isinstance(feature, FeatureConfig):
             # Might be a non-feature config, or doesn't exist
             schema_config = OrganizationSettingsConfig()
             if feature_name in schema_config.ai_features:
                 # Feature exists in schema but not DB, use default
                 feature = schema_config.ai_features[feature_name]
             else:
                 raise HTTPException(status_code=404, detail=f"Feature '{feature_name}' not found or is not a valid feature configuration.")


        if not feature.editable or feature.state == FeatureState.LOCKED:
            raise HTTPException(
                status_code=403,
                detail=f"Feature '{feature_name}' cannot be modified."
            )

        # Update the feature's value
        feature.value = new_value
        # State will be updated automatically by FeatureConfig validator/dict if it's not LOCKED

        # Update the config in the database
        if "ai_features" not in settings.config or not isinstance(settings.config.get("ai_features"), dict):
            settings.config["ai_features"] = {}

        # Store the updated feature as a dict
        settings.config["ai_features"][feature_name] = feature.dict()

        flag_modified(settings, "config")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

        # Audit log
        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.ai_feature_toggled",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(settings.id),
                details={"feature_name": feature_name, "value": new_value},
            )
        except Exception:
            pass

        try:
            await telemetry.capture(
                "ai_feature_updated",
                {"feature_name": feature_name, "new_value": new_value},
                user_id=current_user.id,
                org_id=organization.id,
            )
        except Exception:
            pass

        return settings

    # ── Built-in agents ────────────────────────────────────────────────────
    #
    # ★ These operate on `DataSource.publish_status` — the same field the
    # per-agent switch on the Agents page writes. No second flag exists, so the
    # two screens cannot disagree about whether Power BI is on.

    @staticmethod
    def _builtin_agent_names() -> list:
        """The names the seeder creates. Imported so there is one list, not two."""
        from app.services.default_agents_seeder import (
            FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME,
        )
        return [FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME]

    _BUILTIN_BLURBS = {
        "Microsoft Fabric": "Per-user Microsoft sign-in",
        "Power BI": "Per-user Microsoft sign-in",
        "City Mart Retail": "Sample retail data",
    }

    async def list_builtin_agents(self, db: AsyncSession, organization: Organization) -> list:
        """Seeded agents present in this org, with their current on/off state.

        Returns [] when none exist — a workspace seeded with SEED_DEFAULT_AGENTS
        off, or one whose agents were deleted. The card then does not render,
        rather than showing rows that control nothing.
        """
        from app.models.data_source import DataSource

        rows = (await db.execute(
            select(DataSource).where(
                DataSource.organization_id == organization.id,
                DataSource.deleted_at.is_(None),
                DataSource.name.in_(self._builtin_agent_names()),
            )
        )).scalars().all()

        order = {n: i for i, n in enumerate(self._builtin_agent_names())}
        rows = sorted(rows, key=lambda d: order.get(d.name, 99))
        return [
            {
                "id": str(d.id),
                "name": d.name,
                "description": self._BUILTIN_BLURBS.get(d.name, ""),
                # Anything other than an explicit "disabled" reads as on, so a
                # NULL or a future status never presents as switched off.
                "enabled": (d.publish_status or "published") != "disabled",
            }
            for d in rows
            # ★ Same defensive intersection as the setter: the card must never
            # list an agent it cannot act on. Redundant against today's query,
            # deliberate against tomorrow's.
            if d.name in order
        ]

    async def set_builtin_agents(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        *,
        enabled: bool,
        names: list = None,
    ) -> list:
        """Switch seeded agents on/off. ``names=None`` means all of them.

        ★ The candidate set is intersected with the seeder's own list, so a name
        that is not a built-in agent is silently ignored rather than honoured —
        this endpoint can never disable a customer's own agent, whatever is
        posted to it.
        """
        from app.models.data_source import DataSource

        builtin = set(self._builtin_agent_names())
        targets = builtin if not names else (builtin & set(names))
        if not targets:
            return await self.list_builtin_agents(db, organization)

        rows = (await db.execute(
            select(DataSource).where(
                DataSource.organization_id == organization.id,
                DataSource.deleted_at.is_(None),
                DataSource.name.in_(list(targets)),
            )
        )).scalars().all()

        new_status = "published" if enabled else "disabled"
        changed = []
        for d in rows:
            # ★ Re-check membership here, not just in the WHERE clause. The query
            # already restricts to `targets`, so this is redundant today — but it
            # makes "can never disable a customer's own agent" a property of this
            # loop rather than of a query someone may later widen. Without it the
            # guarantee lives somewhere else and nothing fails loudly when it moves.
            if d.name not in targets:
                continue
            if (d.publish_status or "published") != new_status:
                d.publish_status = new_status
                changed.append(d.name)
        if changed:
            await db.commit()

        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="settings.builtin_agents_set",
                user_id=str(current_user.id),
                resource_type="organization_settings",
                resource_id=str(organization.id),
                details={"enabled": enabled, "changed": changed},
            )
        except Exception:
            # Audit is best-effort: never fail the toggle because logging failed.
            pass

        return await self.list_builtin_agents(db, organization)
