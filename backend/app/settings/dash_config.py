from typing import List, Optional
from pydantic import BaseModel, Field, validator, ConfigDict, AliasGenerator
from pydantic.alias_generators import to_camel
import os

# ★Same reason as config.py: this module's field defaults call os.getenv
# at import time, and it can be imported first.
from app.settings.env_compat import normalize_environment as _normalize_environment
_normalize_environment()
import secrets
import base64


class LLMModel(BaseModel):
    model_id: str
    model_name: str
    is_default: bool = False
    is_small_default: bool = False
    is_enabled: bool = True


class LLMProvider(BaseModel):
    provider_type: str
    provider_name: str
    api_key: str
    models: List[LLMModel]

class Intercom(BaseModel):
    enabled: bool = False

class Telemetry(BaseModel):
    enabled: bool = True

class Swagger(BaseModel):
    enabled: bool = False


class I18nConfig(BaseModel):
    """Internationalization configuration.

    System-wide default and the allowlist of locales that can be selected by
    organizations. Individual orgs override `default_locale` via
    `OrganizationSettingsConfig.locale`.
    """
    default_locale: str = "en"
    enabled_locales: List[str] = ["en", "es", "he", "fr", "sv", "ar", "ru", "de", "pt", "it"]
    fallback_locale: str = "en"

    @validator("default_locale")
    def _default_in_enabled(cls, v, values):
        enabled = values.get("enabled_locales") or ["en"]
        if v not in enabled:
            raise ValueError(f"default_locale '{v}' must be one of enabled_locales {enabled}")
        return v

    @validator("fallback_locale")
    def _fallback_in_enabled(cls, v, values):
        enabled = values.get("enabled_locales") or ["en"]
        if v not in enabled:
            raise ValueError(f"fallback_locale '{v}' must be one of enabled_locales {enabled}")
        return v


class DeploymentConfig(BaseModel):
    type: str = "self_hosted"

class FeatureFlags(BaseModel):
    allow_uninvited_signups: bool = False
    allow_multiple_organizations: bool = False
    verify_emails: bool = False

class OTELConfig(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            validation_alias=to_camel,
            serialization_alias=to_camel,
        ),
        populate_by_name=True,
    )
    enabled: bool = False
    service_name: str = "cityagent-insights-backend"
    traces_endpoint: str = "http://localhost:4317"
    protocol: str = "grpc"  # grpc or http/protobuf
    headers: Optional[str] = ""  # format: key1=value1,key2=value2

    def get_headers(self) -> dict:
        """Parse OTLP headers from environment variable format: key1=value1,key2=value2"""
        if not self.headers:
            return {}
        headers = {}
        for pair in self.headers.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
        return headers

class AuthConfig(BaseModel):
    # local_only | sso_only | hybrid
    mode: str = "hybrid"


class GoogleOAuth(BaseModel):
    enabled: bool = False
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    # Trust this provider to admit people, not just identify them. See
    # OIDCProvider.auto_provision below — same meaning, same default.
    auto_provision: bool = False


class OIDCProvider(BaseModel):
    name: str
    enabled: bool = False
    issuer: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: List[str] = ["openid", "profile", "email"]
    # UI niceties
    label: Optional[str] = None
    icon: Optional[str] = None
    # Advanced options
    pkce: bool = True
    client_auth_method: str = "basic"  # basic | post
    discovery: bool = True
    uid_claim: Optional[str] = "sub"
    redirect_path: Optional[str] = None
    extra_authorize_params: dict = {}
    extra_token_params: dict = {}
    # ★Trust this provider to ADMIT people, not merely identify them.
    #
    # Signing in proves who somebody is; it has never decided whether they get
    # an account here. Without this, a person the provider happily authenticates
    # is still refused ("Sign-up is disabled. Ask your admin for an invite.")
    # unless an admin invited them first — which is the whole point of wiring up
    # a directory, and was the single most common failure on this install.
    #
    # Off by default: turning SSO on must never, by itself, hand an account to
    # everyone the identity provider knows. That has to be a deliberate act.
    #
    # ★Scope lives in the PROVIDER, not here. "Trust Keycloak" means trusting
    # everyone Keycloak lets in, federated identities included. Restricting that
    # belongs in the provider's own client configuration, where it is one rule
    # instead of two lists that can disagree.
    auto_provision: bool = False
    # Group sync — sync OIDC group claims into BOW Groups on login
    sync_groups: bool = False
    group_claim: str = "groups"              # claim name in id_token
    resolve_group_names: bool = False        # call Graph API to get display names (Entra returns UUIDs)


class LDAPConfig(BaseModel):
    """LDAP / Active Directory configuration for group sync and optional bind auth."""
    enabled: bool = False
    url: str = ""                                      # e.g. ldaps://ad.corp.com:636
    bind_dn: Optional[str] = None                      # service account DN
    bind_password: Optional[str] = None                # service account password
    use_ssl: bool = True
    start_tls: bool = False
    base_dn: str = ""
    user_search_base: Optional[str] = None             # defaults to base_dn
    user_search_filter: str = "(objectClass=person)"
    user_email_attribute: str = "mail"
    # ★★★The attribute a person actually TYPES at the sign-in form. The LDAP
    # door searched on `user_email_attribute` ALONE while the form was labelled
    # "Username" with the placeholder `jsmith` — so the label promised a
    # username the code could never accept, and `jsmith` came back as one
    # indistinguishable LOGIN_BAD_CREDENTIALS. Measured on a stock OpenLDAP:
    # `ldapuser` refused (`ldap_not_in_directory` in the audit log),
    # `ldapuser@cityagent.io` accepted. `uid` on OpenLDAP/inetOrgPerson,
    # `sAMAccountName` on Active Directory.
    # ★Empty string restores the old email-only match, for a directory whose
    # login attribute genuinely is the address.
    user_login_attribute: str = "uid"
    # ★`cn`, not `displayName`, which is what this defaulted to. Stock OpenLDAP
    # `inetOrgPerson` does not define `displayName` at all, so on the most
    # ordinary directory in existence the default named an attribute that was
    # never there and every account came out unnamed. `cn` is mandatory on
    # `person` and therefore always present. Active Directory has both; set it
    # explicitly there if `displayName` is preferred. Open WebUI reads `cn` too.
    user_name_attribute: str = "cn"
    group_search_base: Optional[str] = None            # defaults to base_dn
    group_search_filter: str = "(objectClass=group)"
    group_name_attribute: str = "cn"
    group_member_attribute: str = "member"              # "member" (DN) or "memberUid"
    group_member_format: str = "dn"                    # "dn" or "uid"
    sync_interval_minutes: int = 60
    auto_provision_users: bool = False                  # create app user on LDAP login
    connection_timeout: int = 10
    page_size: int = 500

    @validator('bind_password', pre=True, always=True)
    def resolve_env_var(cls, v):
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            return os.environ.get(v[2:-1])
        return v


class SMTPSettings(BaseModel):
    host: str = "smtp.resend.com"
    port: int = 587
    username: Optional[str] = None
    password: Optional[str] = None
    from_name: str = "CityAgent Insights"
    from_email: str = "no-reply@cityagent.io"
    use_tls: bool = True
    use_ssl: bool = False
    use_credentials: bool = True
    validate_certs: bool = True

class Stripe(BaseModel):
    api_key: str = None
    webhook_secret: str = None


class LicenseConfig(BaseModel):
    """Enterprise license configuration"""
    key: Optional[str] = Field(default=None, description="Enterprise license key (DASH_LICENSE_KEY)")

    @validator('key', pre=True, always=True)
    def load_from_env(cls, v):
        """Auto-load license key from env var if not set or placeholder in config"""
        if not v:
            # No value set, fallback to default env var
            from app.settings.env_compat import env_get as _env_get
            return _env_get("DASH_LICENSE_KEY")
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            # Parse env var name from placeholder like ${DASH_LICENSE_KEY2}
            env_var_name = v[2:-1]
            return os.environ.get(env_var_name)
        return v


class DatabaseAuth(BaseModel):
    """Authentication method for the application database.

    provider: 'password' (default) uses the password in the URL.
              'aws_iam' generates short-lived IAM tokens via boto3.
              Future: 'azure_entra', 'gcp_iam'.
    """
    provider: str = "password"
    region: str = ""       # AWS only — e.g. "us-east-1"
    ssl_mode: str = ""     # e.g. "verify-full" (required for IAM auth)
    password: str = ""     # Only used by StaticPasswordProvider when URL has no password


class Database(BaseModel):
    url: str = Field(
        default_factory=lambda: os.getenv(
            "DASH_DATABASE_URL",
            "sqlite:////app/backend/db/app.db"
        )
    )
    # Fields for managed DB with IAM auth (used when auth.provider != 'password')
    host: str = Field(default_factory=lambda: os.getenv("DASH_DATABASE_HOST", ""))
    port: int = Field(default_factory=lambda: int(os.getenv("DASH_DATABASE_PORT", "5432")))
    name: str = Field(default_factory=lambda: os.getenv("DASH_DATABASE_NAME", ""))
    username: str = Field(default_factory=lambda: os.getenv("DASH_DATABASE_USER", ""))
    auth: DatabaseAuth = Field(default_factory=lambda: DatabaseAuth(
        provider=os.getenv("DASH_DATABASE_AUTH_PROVIDER", "password"),
        region=os.getenv("DASH_DATABASE_AUTH_REGION", ""),
        ssl_mode=os.getenv("DASH_DATABASE_SSL_MODE", ""),
    ))

    def get_url(self) -> str:
        """Build the connection URL.

        For 'password' provider, returns the existing url field as-is.
        For IAM providers, constructs the URL from host/port/name/username
        (password is injected at connect time by the auth provider).
        """
        if self.auth.provider == "password":
            return self.url
        # IAM auth — build URL without password; it's injected per-connection
        return f"postgresql://{self.username}@{self.host}:{self.port}/{self.name}"

    @property
    def uses_iam_auth(self) -> bool:
        return self.auth.provider != "password"

def generate_fernet_key():
    # Generate a valid Fernet-compatible key (32 url-safe base64-encoded bytes)
    key = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(key).decode()

class DashConfig(BaseModel):
    deployment: DeploymentConfig = DeploymentConfig()
    base_url: Optional[str] = Field(default="http://0.0.0.0:3000")
    mcp_public_url: Optional[str] = None
    features: FeatureFlags = FeatureFlags()
    auth: AuthConfig = AuthConfig()
    google_oauth: GoogleOAuth = GoogleOAuth()
    ldap: LDAPConfig = LDAPConfig()
    oidc_providers: List[OIDCProvider] = []
    default_llm: List[LLMProvider] = []
    smtp_settings: SMTPSettings = None
    encryption_key: str = Field(
        default_factory=generate_fernet_key,
        description="Encryption key for sensitive data",
        env="DASH_ENCRYPTION_KEY"
    )
    stripe: Stripe = Stripe()
    database: Database = Database()
    intercom: Intercom = Intercom()
    telemetry: Telemetry = Telemetry()
    swagger: Swagger = Swagger()
    license: LicenseConfig = LicenseConfig()
    otel: OTELConfig = OTELConfig()
    i18n: I18nConfig = I18nConfig()

    @validator('encryption_key')
    def validate_encryption_key(cls, v):
        # If the value is empty or still the placeholder, generate a valid key.
        # An ephemeral, process-local key is fine for a throwaway run but is a
        # footgun in any persistent deployment: it changes on every restart, so
        # everything encrypted with it (LLM provider keys, data-source
        # credentials) becomes permanently undecryptable after a restart. Warn
        # loudly so operators pin DASH_ENCRYPTION_KEY instead of silently
        # bricking their stored secrets.
        # ★Both spellings. The configuration file may still carry the old
        # placeholder while the code has been renamed; if this sentinel missed
        # it, the literal string "${DASH_ENCRYPTION_KEY}" would be handed on as
        # if it were a real key.
        if not v or v.strip() in {"", "${DASH_ENCRYPTION_KEY}", "${BOW_ENCRYPTION_KEY}"}:
            # ★★★Same warning as the loader: generating is right on a NEW
            # install and destroys every stored credential on any other, and
            # the two cannot be told apart from here.
            import logging
            logging.getLogger(__name__).warning(
                "DASH_ENCRYPTION_KEY is not set — generating a random, in-memory "
                "encryption key. It will change on the next restart, making all "
                "currently-encrypted credentials undecryptable. Set a stable "
                "DASH_ENCRYPTION_KEY for any non-throwaway deployment."
            )
            return generate_fernet_key()
        return v
