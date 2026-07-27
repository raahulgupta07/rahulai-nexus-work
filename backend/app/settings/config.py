import os
import yaml
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from fastapi_mail import FastMail, ConnectionConfig
from .bow_config import BowConfig


def _analytics_env_int(name: str, default: int) -> int:
    """Parse an int env var safely; fall back to default on missing/garbage."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _analytics_env_float(name: str, default: float) -> float:
    """Parse a float env var safely; fall back to default on missing/garbage."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


class Settings(BaseSettings):
    PROJECT_NAME: str = "CityAgent Insights"
    PROJECT_VERSION: str = open("../VERSION").read().strip()
    API_PREFIX: str = "/api"
    DEBUG: bool = True
    TESTING: bool = False
    TEST_DATABASE_URL: str = "sqlite:///db/test_{}.db".format(os.getpid())
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
    # Smart (structural) Excel ingestion. OFF by default → byte-identical to the
    # legacy xlsx_to_csvs path. Overridable via env SMART_EXCEL_INGEST=true.
    smart_excel_ingest: bool = os.environ.get(
        "SMART_EXCEL_INGEST", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # When a report has genuine user-uploaded files (NOT files auto-snapshotted
    # from a bound agent's data source), focus the agent's readable files on those
    # uploads instead of every bound agent's inherited files. Fixes "I uploaded one
    # file, why is it reading all the others" in Auto mode. Falls back to all files
    # when there are no user uploads. ON by default; disable via env =false.
    scope_chat_uploads_to_report: bool = os.environ.get(
        "SCOPE_CHAT_UPLOADS", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    # When a turn has genuine user uploads, also suppress the bound data-source
    # SCHEMAS/tables for that turn so the clarify tool doesn't offer every bound
    # agent (CRM/Financial/Music) — the upload becomes the sole focus. Only fires
    # alongside scope_chat_uploads_to_report AND when an upload is present. ON by
    # default; disable via env =false to keep tables available on upload turns.
    scope_uploads_suppress_schema: bool = os.environ.get(
        "SCOPE_UPLOADS_SUPPRESS_SCHEMA", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Smart file intake: classify each upload (table/instruction/skill/knowledge)
    # and REWRITE raw content into clean structured instructions/skills instead of
    # dumping it verbatim. OFF by default → legacy raw def_ingest path. Any failure
    # falls back to the legacy path. Enable via env SMART_FILE_INTAKE=true.
    smart_file_intake: bool = os.environ.get(
        "SMART_FILE_INTAKE", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # "Improve overview": on a MANUAL FILE AGENT, split its overweight primary
    # instruction into a clean dictionary + on-demand kind='skill' instructions,
    # and attach table references. Purely additive and gated: the endpoint 403s
    # and the UI button is hidden when OFF, so live behavior is byte-identical.
    # Never touches database connectors. Enable via env INSTRUCTION_IMPROVE=true.
    instruction_improve: bool = os.environ.get(
        "INSTRUCTION_IMPROVE", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Clean file-agent table names: strip the leading {uuid}_ prefix that managed
    # uploads carry, so file agents see clean names like `mm_conso_feb` instead of
    # `t_22b02300_..._mm_conso`. OFF by default → byte-identical to the legacy
    # (uuid-prefixed) table names. Enable via env CLEAN_FILE_TABLE_NAMES=true.
    clean_file_table_names: bool = os.environ.get(
        "CLEAN_FILE_TABLE_NAMES", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Microsoft Fabric (User Sign-in) connector: gates catalog visibility of the
    # additive per-user ``fabric_user`` connector in the Add-Connection catalog.
    # Default TRUE — this whitelabel seeds a Fabric agent on install and wants the
    # connector always visible so admins can add more. Set HYBRID_FABRIC_USER=false
    # to hide it from the catalog (existing connections keep working regardless).
    hybrid_fabric_user: bool = os.environ.get(
        "HYBRID_FABRIC_USER", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Default-agents seeder: on a FRESH install, right after the first org is
    # bootstrapped (first signup), auto-create three ready-to-use agents —
    # Microsoft Fabric (per-user), Power BI (per-user), and the City Mart Retail
    # sample. Idempotent (marker in OrganizationSettings) and only ever fires for
    # the very first org, so existing installs are never retroactively seeded.
    # ON by default; disable via env SEED_DEFAULT_AGENTS=false.
    seed_default_agents: bool = os.environ.get(
        "SEED_DEFAULT_AGENTS", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Per-user private instructions: each member can add instructions scoped to
    # ONLY themselves on a shared agent (Instruction.user_id = their id). Shared
    # org rules stay user_id=NULL. Retrieval loads (NULL OR current_user) so a
    # user never sees another user's private rules. OFF by default → byte-identical
    # (user_id ignored, all instructions loaded as today). Env PER_USER_INSTRUCTIONS.
    per_user_instructions: bool = os.environ.get(
        "PER_USER_INSTRUCTIONS", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Per-user table selection + training on per-user-token connectors
    # (fabric_user, powerbi_user ONLY). When ON, a member who has signed in with
    # their own Microsoft token can manage THEIR OWN overlay: pick which of the
    # tables their token can reach are active for their agent, and run a Learn
    # scoped to those tables → a private overview (Instruction.user_id = them).
    # Strictly gated to per-user connectors via is_per_user_connector(); shared
    # connectors (duckdb, service-principal, etc.) are byte-identical. OFF by
    # default → current behavior (shared catalog, creator-manages). Env
    # HYBRID_PER_USER_TABLE_SELECT.
    hybrid_per_user_table_select: bool = os.environ.get(
        "HYBRID_PER_USER_TABLE_SELECT", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Live "Learn agent" progress. When ON, llm_sync(force_llm=True) stamps its
    # stage (reading_tables -> analyzing -> generating_overview ->
    # grounding_publishing) into an in-memory per-(data_source, user) tracker,
    # exposed via GET /data_sources/{id}/learn-status so the UI can show the
    # real stage instead of a bare spinner. Best-effort + connector-agnostic;
    # a stamping failure never affects the learn. OFF by default -> no stamping,
    # byte-identical behavior. Env HYBRID_LEARN_PROGRESS.
    hybrid_learn_progress: bool = os.environ.get(
        "HYBRID_LEARN_PROGRESS", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Full-app analytics page (nav item below Monitoring). Adds an org-wide
    # usage/adoption/ROI dashboard: DAU/WAU/MAU, per-user + per-company (email
    # domain) + per-agent + per-connector activity, question-intent clusters,
    # cohort retention, activation funnel, cost/tokens and derived ROI. OFF by
    # default -> nav item hidden, no route surfaced. Env HYBRID_APP_ANALYTICS.
    hybrid_app_analytics: bool = os.environ.get(
        "HYBRID_APP_ANALYTICS", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Local compute (Option A): a "Local folder (this device)" data source whose
    # files are read via the browser File System Access API and whose SQL runs
    # in-browser (DuckDB-WASM) on the user's CPU — raw data never uploads, only
    # the result rows return. OFF by default -> the connector is hidden and no
    # client-exec branch is taken (byte-identical). Env HYBRID_LOCAL_COMPUTE.
    hybrid_local_compute: bool = os.environ.get(
        "HYBRID_LOCAL_COMPUTE", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Local runtime (Cowork-style): when a user has paired the local helper and
    # toggled "run on my computer", the agent-generated Python executes on their
    # laptop (helper daemon) instead of the server sandbox. The agent brain,
    # codegen, prompts and tools are untouched — only the execution seam in
    # StreamingCodeExecutor dispatches. Data fetch is proxied back to the server
    # (credentials never leave). OFF by default -> dispatch never fires, server
    # path is byte-identical. Env HYBRID_LOCAL_RUNTIME.
    hybrid_local_runtime: bool = os.environ.get(
        "HYBRID_LOCAL_RUNTIME", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # Attach a local folder in chat (builds on HYBRID_LOCAL_RUNTIME). The paired
    # helper scans its whitelisted folders with DuckDB and posts back SCHEMA ONLY
    # (table name, columns+types, row count) — never rows. Those tables are then
    # offered in the composer's attach menu; attaching one injects its schema
    # into the planner context so the agent writes SQL against it, and any job
    # touching a local:<folder> client is FORCED onto the helper (never the
    # server, which has no such client). OFF by default -> no folder endpoints
    # surfaced to the UI, no context injection, no forced routing: the local
    # runtime behaves exactly as before. Env HYBRID_LOCAL_FOLDER_ATTACH.
    hybrid_local_folder_attach: bool = os.environ.get(
        "HYBRID_LOCAL_FOLDER_ATTACH", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    # DEF-006 Power BI truncation guard. The executeQueries REST endpoint silently
    # returns a PARTIAL table — a hard 100,000-row cap, plus an earlier response-SIZE
    # cap that bites at an arbitrary row count on wide rows — with nothing in the
    # payload saying so. A partial DataFrame then reaches pandas and every aggregate
    # built on it (top-N, sums, distinct counts) is confidently wrong. When ON, a
    # detected truncation RAISES with a message telling the model to push the
    # aggregation into DAX instead of pulling the whole table. ON by default; set
    # HYBRID_PBI_TRUNCATION_GUARD=false to fall back to the old silent behavior if it
    # ever trips a working flow. Env HYBRID_PBI_TRUNCATION_GUARD.
    hybrid_pbi_truncation_guard: bool = os.environ.get(
        "HYBRID_PBI_TRUNCATION_GUARD", "true"
    ).strip().lower() in ("1", "true", "yes", "on")

    # Dashboard trust release. All three default ON; each can be turned off
    # independently if it ever trips a working flow.
    #   completeness gate — refuse to build an artifact from truncated data,
    #     so the agent aggregates FIRST instead of build/render/discard/rebuild.
    #   render preflight  — load the generated dashboard in headless Chromium
    #     (already in the image for PDF export) and refuse to store one that
    #     does not render. Catches invalid JSX, not just the prose case.
    #   insights          — generate a grounded summary for each dashboard, with
    #     every figure verified against the artifact's own data before storing.
    hybrid_artifact_completeness_gate: bool = os.environ.get(
        "HYBRID_ARTIFACT_COMPLETENESS_GATE", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    hybrid_artifact_render_preflight: bool = os.environ.get(
        "HYBRID_ARTIFACT_RENDER_PREFLIGHT", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    hybrid_artifact_insights: bool = os.environ.get(
        "HYBRID_ARTIFACT_INSIGHTS", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    # ROI baseline knobs for the App Analytics page. Only the ROI section of the
    # dashboard depends on these; every other number comes straight from the DB.
    # `configured` in the ROI payload is true only when at least one of these env
    # vars is explicitly set (so a fresh install shows "not configured" instead of
    # pretending the defaults were chosen).
    #   minutes saved per answered query (manual baseline)
    analytics_baseline_minutes_per_query: int = _analytics_env_int(
        "ANALYTICS_BASELINE_MINUTES_PER_QUERY", 30
    )
    #   fully-loaded hourly labour rate used to value the time saved
    analytics_hourly_rate_usd: float = _analytics_env_float(
        "ANALYTICS_HOURLY_RATE_USD", 18
    )
    #   fixed monthly run-rate (subscription/infra) folded into automation cost
    analytics_monthly_run_rate_usd: float = _analytics_env_float(
        "ANALYTICS_MONTHLY_RUN_RATE_USD", 0
    )
    #   one-off implementation cost, used only for payback-period
    analytics_impl_cost_usd: float = _analytics_env_float(
        "ANALYTICS_IMPL_COST_USD", 0
    )
    bow_config: BowConfig | None = None
    email_client: FastMail | None = None

    @property
    def version(self) -> str:
        return self.PROJECT_VERSION

    @classmethod
    def load(cls):
        # Load YAML configuration
        environment = os.environ.get("ENVIRONMENT", "development")
        print("Loading settings for environment:", environment)

        # Load environment variables first
        if environment == "development":
            
            dotenv_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                ".env"
            )
            print(f"Loading .env from: {dotenv_path}")
            load_dotenv(dotenv_path)

        # Load and validate bow-config using Pydantic.
        # BOW_CONFIG_PATH overrides the default; restrict it to an absolute
        # path with a yaml extension so this env var can't be used to read
        # an arbitrary file off disk via path traversal.
        yaml_path = os.environ.get('BOW_CONFIG_PATH')
        if yaml_path:
            yaml_path = os.path.abspath(yaml_path)
            if not yaml_path.endswith((".yaml", ".yml")):
                raise RuntimeError(
                    f"BOW_CONFIG_PATH must point to a .yaml or .yml file: {yaml_path!r}"
                )
            if not os.path.isfile(yaml_path):
                raise RuntimeError(f"BOW_CONFIG_PATH does not exist: {yaml_path!r}")
        else:
            yaml_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "configs/bow-config.dev.yaml" if environment == "development" else "bow-config.yaml"
            )

        print(f"Loading config from: {yaml_path}")
        
        # Process environment variables in the YAML before validation
        def resolve_env_vars(config):
            if isinstance(config, dict):
                return {k: resolve_env_vars(v) for k, v in config.items()}
            elif isinstance(config, list):
                return [resolve_env_vars(i) for i in config]
            elif isinstance(config, str) and config.startswith("${") and config.endswith("}"):
                # Extract env var name from ${VAR_NAME}
                env_var_name = config[2:-1]
                env_value = os.environ.get(env_var_name)
                if env_value is not None:
                    return env_value
                # If env var is not set and this is encryption key, generate one
                if env_var_name == "BOW_ENCRYPTION_KEY":
                    from .bow_config import generate_fernet_key
                    new_key = generate_fernet_key()
                    os.environ["BOW_ENCRYPTION_KEY"] = new_key  # Save for future use
                    return new_key
                return None  # Env var not set — return None instead of raw placeholder
            return config

        # Inline path-traversal guard at the sink (Snyk python/PT). Reject
        # traversal sequences and ensure the resolved path exists.
        if ".." in yaml_path or not yaml_path.endswith((".yaml", ".yml")):
            raise RuntimeError(f"Config path is not a yaml file: {yaml_path!r}")
        yaml_path_safe = os.path.realpath(yaml_path)
        if not os.path.isfile(yaml_path_safe):
            raise RuntimeError(f"Config path is not a regular file: {yaml_path!r}")

        with open(yaml_path_safe, "r") as yaml_file:
            yaml_config = yaml.safe_load(yaml_file)
            # Resolve environment variables before validation
            yaml_config = resolve_env_vars(yaml_config)
            # Validate config using Pydantic model
            bow_config = BowConfig(**yaml_config)
            
        # Create the environment-specific settings instance
        if environment == "development":
            from .development import Development
            settings = Development(bow_config=bow_config)
        elif environment == "staging":
            from .staging import Staging
            settings = Staging(bow_config=bow_config)
        elif environment == "production":
            from .production import Production
            settings = Production(bow_config=bow_config)
        else:
            raise ValueError(f"Unknown environment: {environment}")
            
        # Setup email client.
        # - With use_credentials=True (default), both username and password must be
        #   present. In dev/test, BOW_SMTP_PASSWORD is often unset and resolves to
        #   None; in that case, leave email_client unset rather than failing to start.
        # - With use_credentials=False, the relay accepts mail without auth and
        #   username/password are not required.
        smtp = bow_config.smtp_settings
        if smtp and ((not smtp.use_credentials) or (smtp.username and smtp.password)):
            email_config = ConnectionConfig(
                MAIL_USERNAME=smtp.username or "",
                MAIL_PASSWORD=smtp.password or "",
                MAIL_FROM_NAME=smtp.from_name,
                MAIL_FROM=smtp.from_email,
                MAIL_PORT=smtp.port,
                MAIL_SERVER=smtp.host,
                MAIL_STARTTLS=smtp.use_tls,
                MAIL_SSL_TLS=smtp.use_ssl,
                USE_CREDENTIALS=smtp.use_credentials,
                VALIDATE_CERTS=smtp.validate_certs,
                TEMPLATE_FOLDER=None
            )
            settings.email_client = FastMail(email_config)

        return settings
    

settings = Settings.load()