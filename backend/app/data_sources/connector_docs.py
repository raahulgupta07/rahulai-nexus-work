"""
connector_docs.py
-----------------------------------------------------------------------------
Single source of truth for connector setup help ("How to get each value").

Consumed by THREE surfaces (Phase 1 of the connector-docs work):
  1. GET /data_sources/{type}/fields  -> attaches a `docs` block so the
     frontend right-side help panel renders for EVERY connector.
  2. GET /data_sources/{type}/setup-doc.docx -> renders a real Word document.
  3. (legacy) the client-side HTML worksheet, until retired.

`whereToGet` is keyed by the connector's field name (same names the
/fields endpoint returns from the Pydantic config/credential schemas).

`build_connector_docs()` ALWAYS returns a complete doc for any connector:
curated entries win; every other field gets a name/title-derived generic
hint. So curated (~15) and non-curated (all the rest) both get a panel.
-----------------------------------------------------------------------------
"""

from __future__ import annotations

# --- Generic reusable snippets ------------------------------------------------
GENERIC_HOST = (
    "Hostname or IP of the server, from your DBA or infrastructure team "
    "(e.g. db.internal.company.com). Do not include the protocol or port."
)
GENERIC_PORT = (
    "The TCP port the service listens on. Use the pre-filled default unless "
    "your DBA changed it."
)
GENERIC_DB = (
    "The database (catalog) name to connect to. Ask your DBA which database "
    "holds the data you need."
)
GENERIC_SCHEMA = (
    "Optional. Restrict discovery to one schema. Leave blank to index all "
    "schemas the account can see."
)
GENERIC_USER = (
    "A dedicated read-only service account username. Ask your DBA to create "
    "one scoped to just the required schemas."
)
GENERIC_PASSWORD = (
    "The password for the service account above. Provided by your DBA — keep "
    "it secret; it is stored encrypted."
)
AWS_ACCESS_KEY = (
    "AWS Console -> IAM -> Users -> (your service user) -> Security "
    "credentials -> Create access key. This is the Access key ID."
)
AWS_SECRET_KEY = (
    "Shown once when you create the access key above (AWS IAM). Copy it "
    "immediately — AWS will not show the secret again."
)
AWS_REGION = (
    "The AWS region code the resource lives in, e.g. us-east-1. Visible in the "
    "top-right region selector of the AWS Console."
)

# --- Curated connectors (rich, portal-exact guidance) -------------------------
# Keys are the normalized (lowercased) registry `type`.
CONNECTOR_DOCS: dict[str, dict] = {
    "postgresql": {
        "whereToGet": {
            "host": GENERIC_HOST
            + " For managed Postgres: AWS RDS -> Databases -> (instance) -> "
            "Endpoint; or Azure/GCP the \"Host\"/\"Public IP\" on the instance overview.",
            "port": "Default 5432. Your DBA confirms if changed.",
            "database": GENERIC_DB,
            "schema": GENERIC_SCHEMA + ' Default is "public".',
            "user": GENERIC_USER,
            "password": GENERIC_PASSWORD,
        },
        "authFlow": [
            "System auth: the admin supplies one read-only service account here; "
            "everyone queries through it.",
            "Ask the DBA: CREATE ROLE insights LOGIN PASSWORD '…'; GRANT CONNECT "
            "ON DATABASE … ; GRANT USAGE ON SCHEMA … ; GRANT SELECT ON ALL TABLES "
            "IN SCHEMA … .",
        ],
        "notes": "Ensure the server allows connections from the CityAgent Insights "
        "host IP (pg_hba.conf / cloud firewall / security group). For cloud RDS the "
        "security group must allow inbound 5432 from the app.",
    },
    "mysql": {
        "whereToGet": {
            "host": GENERIC_HOST,
            "port": "Default 3306.",
            "database": GENERIC_DB,
            "user": GENERIC_USER,
            "password": GENERIC_PASSWORD,
        },
        "authFlow": [
            "System auth: one read-only MySQL user shared by everyone.",
            "DBA grant: CREATE USER 'insights'@'%' IDENTIFIED BY '…'; GRANT SELECT "
            "ON db.* TO 'insights'@'%'; FLUSH PRIVILEGES;",
        ],
        "notes": "The MySQL user host mask (@'%' or a specific IP) must permit the "
        "CityAgent Insights server. Check the cloud firewall / security group allows "
        "inbound 3306.",
    },
    "mariadb": {
        "whereToGet": {
            "host": GENERIC_HOST,
            "port": "Default 3306.",
            "database": GENERIC_DB,
            "user": GENERIC_USER,
            "password": GENERIC_PASSWORD,
        },
        "authFlow": ["System auth: one read-only MariaDB user shared by everyone."],
        "notes": "Same as MySQL — grant SELECT to a dedicated user and open port 3306 "
        "to the app host.",
    },
    "mssql": {
        "whereToGet": {
            "host": GENERIC_HOST
            + " For a named instance use host\\INSTANCE; for Azure SQL it is "
            "<server>.database.windows.net.",
            "port": "Default 1433.",
            "database": GENERIC_DB,
            "schema": GENERIC_SCHEMA + ' Default is "dbo".',
            "odbc_driver": "Leave the default (18) unless your server requires an "
            "older ODBC driver.",
            "encrypt": "Leave enabled for Azure SQL and modern servers.",
            "user": "A SQL Server login (SQL authentication). Ask your DBA to create "
            "a read-only login.",
            "password": GENERIC_PASSWORD,
        },
        "authFlow": [
            "System auth (SQL login): admin supplies one read-only login used by "
            "everyone.",
            "Kerberos / Windows Integrated: uses the app's own domain identity (no "
            "password stored).",
            "Kerberos SSO (per-user): the app impersonates the signed-in user via "
            "constrained delegation — no secret stored.",
            "DBA: CREATE LOGIN insights WITH PASSWORD='…'; CREATE USER insights FOR "
            "LOGIN insights; GRANT SELECT/VIEW DEFINITION on the target schema.",
        ],
        "notes": "Azure SQL: allow the app host IP under the server firewall. On-prem: "
        "open TCP 1433 and confirm SQL authentication is enabled (Mixed Mode).",
    },
    "snowflake": {
        "whereToGet": {
            "account": "Snowflake account identifier, e.g. ABCDEF-GH12345. In "
            "Snowsight: bottom-left account menu -> Account -> \"Account/Server URL\" "
            "(the part before .snowflakecomputing.com), or Admin -> Accounts.",
            "warehouse": "A virtual warehouse (compute) name. Snowsight -> Admin -> "
            "Warehouses. Use a small XS warehouse dedicated to analytics.",
            "database": "The database to query. Snowsight -> Data -> Databases.",
            "schema": "Schema within the database (comma-separated list allowed). "
            "Snowsight -> Data -> Databases -> (db) -> Schemas.",
            "role": "Optional. The Snowflake role that grants access to the above "
            "objects (e.g. INSIGHTS_RO). Snowsight -> Admin -> Users & Roles -> Roles.",
            "user": "A dedicated Snowflake user. Snowsight -> Admin -> Users & Roles "
            "-> Users -> + User.",
            "password": "That user's password.",
            "private_key_pem": "Key-pair auth: the PEM private key you generated "
            "(openssl genrsa) whose public key is assigned to the user via ALTER USER "
            "… SET RSA_PUBLIC_KEY.",
            "private_key_passphrase": "Optional passphrase protecting the private key "
            "above.",
        },
        "authFlow": [
            "Username / Password OR Key Pair (recommended for service accounts).",
            "Key pair: generate an RSA key, run ALTER USER insights SET "
            "RSA_PUBLIC_KEY='<pub>', then paste the private key here.",
            "Grant: GRANT USAGE ON WAREHOUSE …; GRANT USAGE ON DATABASE/SCHEMA …; "
            "GRANT SELECT ON ALL TABLES … TO ROLE INSIGHTS_RO;",
        ],
        "notes": "Prefer key-pair auth for a service account (no password rotation). "
        "If your Snowflake enforces network policies, allowlist the CityAgent Insights "
        "egress IP.",
    },
    "bigquery": {
        "whereToGet": {
            "project_id": "Google Cloud project ID. GCP Console -> top project "
            "selector, or Console -> IAM & Admin -> Settings -> \"Project ID\".",
            "dataset": "The BigQuery dataset to query. Console -> BigQuery -> Explorer "
            "-> (project) -> the dataset name.",
            "credentials_json": "A Service Account key JSON. Console -> IAM & Admin -> "
            "Service Accounts -> Create (grant \"BigQuery Data Viewer\" + \"BigQuery "
            "Job User\") -> Keys tab -> Add key -> JSON. Paste the whole file contents.",
        },
        "authFlow": [
            "Service Account JSON (recommended): create a service account, grant "
            "BigQuery Data Viewer + Job User, download a JSON key, paste it here.",
            "Sign in with Google (per-user): each user authorizes with their own "
            "Google account instead of a shared key.",
        ],
        "notes": "The service account needs roles/bigquery.dataViewer on the dataset "
        "and roles/bigquery.jobUser on the project. Keep the JSON key secret — it is "
        "stored encrypted.",
    },
    "databricks_sql": {
        "whereToGet": {
            "server_hostname": "Databricks -> SQL Warehouses -> (your warehouse) -> "
            "Connection details -> \"Server hostname\" (e.g. dbc-1234.cloud.databricks.com).",
            "http_path": "Same \"Connection details\" panel -> \"HTTP path\" "
            "(e.g. /sql/1.0/warehouses/abc123).",
            "catalog": "The Unity Catalog to query. Databricks -> Catalog explorer.",
            "schema": "Optional schema within the catalog. Databricks -> Catalog "
            "explorer -> (catalog) -> schemas.",
            "access_token": "Databricks -> User Settings (top-right avatar) -> "
            "Developer -> Access tokens -> Generate new token. Copy it immediately.",
        },
        "authFlow": [
            "Personal Access Token (PAT): generate under User Settings -> Developer "
            "-> Access tokens.",
            "For a service identity, generate the PAT from a service principal rather "
            "than a personal user.",
        ],
        "notes": "The token owner must have USE CATALOG / USE SCHEMA / SELECT on the "
        "target Unity Catalog objects and CAN USE on the SQL Warehouse.",
    },
    "powerbi": {
        "whereToGet": {
            "tenant_id": "Azure Portal -> Microsoft Entra ID -> Overview -> "
            "\"Directory (tenant) ID\".",
            "client_id": "Azure Portal -> Entra ID -> App registrations -> (your app) "
            "-> Overview -> \"Application (client) ID\".",
            "client_secret": "Azure Portal -> Entra ID -> App registrations -> (your "
            "app) -> Certificates & secrets -> New client secret -> copy the Value "
            "(not the Secret ID) immediately.",
            "workspaces": "Optional. Comma-separated Power BI workspace IDs to scope "
            "to. In Power BI Service open the workspace; the ID is in the URL "
            "(…/groups/<workspace-id>/…). Leave blank to auto-discover.",
        },
        "authFlow": [
            "Service Principal (Azure AD): register an app in Entra ID, create a "
            "client secret, and in the Power BI Admin Portal enable \"Allow service "
            "principals to use Power BI APIs\" and add the app to each workspace as a "
            "Member/Viewer.",
            "Sign in with Microsoft (per-user): each user authorizes with their own "
            "Microsoft account; RLS applies per user.",
        ],
        "notes": "Power BI Admin Portal -> Tenant settings must allow service "
        "principals. The app must be added to the target workspace(s). Datasets are "
        "queried via DAX.",
    },
    "powerbi_report_server": {
        "whereToGet": {
            "username": "A Windows/AD account that can view reports on the Report "
            "Server. Format DOMAIN\\user or user.",
            "password": "That account's password.",
            "domain": "The Active Directory domain (NetBIOS name) for NTLM auth.",
        },
        "authFlow": [
            "System auth (NTLM): admin supplies one AD account used to browse the "
            "on-prem Report Server."
        ],
        "notes": "On-prem only. The app host must reach the Report Server URL and the "
        "account needs Browser role on the target folders.",
    },
    "tableau": {
        "whereToGet": {
            "server_url": "Your Tableau Server/Cloud base URL, e.g. "
            "https://10ax.online.tableau.com or https://tableau.company.com.",
            "site_name": "The Tableau site (content URL). For Tableau Cloud it is the "
            "segment after /site/ in the URL; blank = Default site.",
            "api_version": "Leave default (matches recent Tableau). Change only for "
            "older on-prem Server versions.",
            "pat_name": "Tableau -> (avatar) My Account Settings -> Personal Access "
            "Tokens -> the token name you created.",
            "pat_token": "The secret shown once when you create the Personal Access "
            "Token. Copy it immediately.",
        },
        "authFlow": [
            "Personal Access Token (PAT): each user (or a service account) creates a "
            "PAT under My Account Settings -> Personal Access Tokens.",
            "The PAT owner needs access to the published data sources you want to "
            "query (Metadata API + VizQL Data Service must be enabled by the Tableau "
            "admin).",
        ],
        "notes": "Tableau admin must enable the Metadata API and VizQL Data Service on "
        "the site. PATs expire after inactivity — a service PAT is recommended.",
    },
    "salesforce": {
        "whereToGet": {
            "domain": "Use \"login\" for production, or your My Domain (e.g. mycompany) "
            "— Setup -> Company Settings -> My Domain.",
            "sandbox": "Enable if connecting to a sandbox org (login host "
            "test.salesforce.com).",
            "username": "The Salesforce username (an email-like login) of a dedicated "
            "integration user.",
            "password": "That user's password.",
            "security_token": "Salesforce -> (avatar) Settings -> My Personal "
            "Information -> Reset My Security Token. It is emailed to the user; append "
            "it after the password.",
        },
        "authFlow": [
            "Username / Password + Security Token: create an integration user with a "
            "Profile/Permission Set granting read (View All Data or object-level Read).",
            "Reset the security token from the user's personal settings; it arrives by "
            "email.",
        ],
        "notes": "If the org enforces IP ranges, either allowlist the app IP or rely "
        "on the security token. API access must be enabled on the user's profile.",
    },
    "mongodb": {
        "whereToGet": {
            "host": "Hostname of the mongod / cluster. For Atlas: Atlas -> Database "
            "-> Connect -> the host in the connection string (e.g. "
            "cluster0.ab12.mongodb.net).",
            "port": "Default 27017. Omit / ignore when using SRV (Atlas).",
            "database": "The database to query. Atlas -> Browse Collections, or ask "
            "your team.",
            "auth_source": "Optional. The database that holds the user credentials "
            "(often \"admin\"). Atlas uses \"admin\".",
            "use_srv": "Enable for Atlas / mongodb+srv:// connection strings "
            "(DNS-based seedlist).",
            "tls": "Enable for Atlas and any TLS-secured cluster.",
            "user": "A read-only database user. Atlas -> Database Access -> Add New "
            "Database User (role readAnyDatabase or read on the target db).",
            "password": "That database user's password.",
        },
        "authFlow": [
            "Username / Password: create a read-only DB user (Atlas: Database Access; "
            "self-hosted: db.createUser with role \"read\").",
            "For Atlas, enable SRV + TLS and allowlist the app IP under Network Access.",
        ],
        "notes": "Atlas -> Network Access must allowlist the CityAgent Insights egress "
        "IP (or 0.0.0.0/0 for testing only).",
    },
    "s3": {
        "whereToGet": {
            "bucket": "The S3 bucket name. AWS Console -> S3 -> Buckets.",
            "prefix": "Optional. A key prefix (folder) to scope to, e.g. reports/2026/. "
            "Leave blank for the whole bucket.",
            "region": AWS_REGION,
            "endpoint_url": "Optional. Only for S3-compatible stores (MinIO, Wasabi, "
            "R2). Leave blank for AWS S3.",
            "access_key": AWS_ACCESS_KEY + " The IAM user needs s3:GetObject + "
            "s3:ListBucket on this bucket.",
            "secret_key": AWS_SECRET_KEY,
            "session_token": "Optional. Only for temporary (STS) credentials.",
            "role_arn": "Assume-Role auth: AWS Console -> IAM -> Roles -> (role) -> "
            "copy the Role ARN. The app assumes it via STS.",
        },
        "authFlow": [
            "AWS Access Key: an IAM user with s3:GetObject + s3:ListBucket on the "
            "bucket/prefix.",
            "AWS Assume Role (STS): the app assumes an IAM role you specify by ARN.",
            "AWS Default Chain: use the host's instance profile / IRSA — no keys "
            "entered.",
        ],
        "notes": "Grant the least-privilege policy: s3:ListBucket on the bucket + "
        "s3:GetObject on the prefix. Files (PDF/Word/Excel/CSV) become searchable "
        "objects.",
    },
    "aws_redshift": {
        "whereToGet": {
            "host": "Redshift -> Clusters -> (cluster) -> \"Endpoint\" (host part, "
            "before :port). Redshift Serverless: the workgroup endpoint.",
            "port": "Default 5439.",
            "database": "The Redshift database name (Clusters -> (cluster) -> "
            "Properties -> Database name).",
            "schema": GENERIC_SCHEMA + ' Default "public".',
            "region": AWS_REGION,
            "cluster_identifier": "Optional (needed for IAM auth). Redshift -> "
            "Clusters -> the cluster identifier.",
            "user": "A read-only Redshift user. Ask your DBA: CREATE USER insights "
            "PASSWORD '…'; GRANT SELECT … .",
            "password": GENERIC_PASSWORD,
            "access_key": AWS_ACCESS_KEY,
            "secret_key": AWS_SECRET_KEY,
            "role_arn": "For Assume-Role auth: the IAM role ARN from AWS IAM -> Roles.",
        },
        "authFlow": [
            "Username / Password: a database user with SELECT on the target schema.",
            "AWS Keys (IAM) / Assume Role (ARN): temporary credentials minted via "
            "redshift:GetClusterCredentials.",
        ],
        "notes": "The cluster security group must allow inbound 5439 from the app "
        "host. For IAM auth the AWS principal needs redshift:GetClusterCredentials on "
        "the cluster/dbuser.",
    },
    "clickhouse": {
        "whereToGet": {
            "host": GENERIC_HOST + " ClickHouse Cloud: Console -> your service -> "
            "Connect -> the host.",
            "port": "Default 8123 (HTTP) or 8443 (HTTPS). ClickHouse Cloud uses 8443.",
            "database": "The database to query. Default \"default\".",
            "secure": "Enable (HTTPS) for ClickHouse Cloud and any TLS endpoint.",
            "user": "A read-only ClickHouse user. Cloud: Console -> Settings; "
            "self-hosted: CREATE USER … with GRANT SELECT.",
            "password": "That user's password. ClickHouse Cloud shows the "
            "default-user password on service creation.",
        },
        "authFlow": [
            "Username / Password over the HTTP(S) interface.",
            "ClickHouse Cloud: use the connection details panel; keep Secure enabled "
            "(port 8443).",
        ],
        "notes": "ClickHouse Cloud: add the app egress IP to the service IP Access "
        "List. Grant the user SELECT on the target database only.",
    },
}

# --- Aliases: registry `type` -> curated key ---------------------------------
ALIASES: dict[str, str] = {
    "mssql": "mssql",
    "microsoft_sql_server": "mssql",
    "databricks": "databricks_sql",
    "redshift": "aws_redshift",
    "postgres": "postgresql",
    "mongo": "mongodb",
    "amazon_s3": "s3",
    "power_bi": "powerbi",
}

# --- Official docs URLs (best-effort; used in the .docx footer) ---------------
OFFICIAL_DOCS: dict[str, str] = {
    "postgresql": "https://www.postgresql.org/docs/current/libpq-connect.html",
    "mysql": "https://dev.mysql.com/doc/refman/8.0/en/connecting.html",
    "mariadb": "https://mariadb.com/kb/en/connecting-to-mariadb/",
    "mssql": "https://learn.microsoft.com/sql/connect/",
    "snowflake": "https://docs.snowflake.com/en/user-guide/admin-user-management",
    "bigquery": "https://cloud.google.com/bigquery/docs/authentication",
    "databricks_sql": "https://docs.databricks.com/aws/en/dev-tools/auth/pat",
    "powerbi": "https://learn.microsoft.com/power-bi/developer/embedded/embed-service-principal",
    "tableau": "https://help.tableau.com/current/server/en-us/security_personal_access_tokens.htm",
    "salesforce": "https://help.salesforce.com/s/articleView?id=sf.user_security_token.htm",
    "mongodb": "https://www.mongodb.com/docs/atlas/security-add-mongodb-users/",
    "s3": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-overview.html",
    "aws_redshift": "https://docs.aws.amazon.com/redshift/latest/mgmt/connecting-to-cluster.html",
    "clickhouse": "https://clickhouse.com/docs/en/cloud/security/connectivity",
}


def resolve_key(dtype: str | None) -> str | None:
    """Resolve a registry type to a curated key (case-insensitive, alias-aware)."""
    if not dtype:
        return None
    key = str(dtype).lower()
    if key in CONNECTOR_DOCS:
        return key
    aliased = ALIASES.get(key)
    if aliased and aliased in CONNECTOR_DOCS:
        return aliased
    return None


# --- Name/title-derived generic hints (fallback for uncurated fields) ---------
def _generic_hint(name: str, title: str = "", description: str = "") -> str:
    """Best-effort hint for a field with no curated text."""
    if description:
        return description
    n = (name or "").lower()

    def has(*subs: str) -> bool:
        return any(s in n for s in subs)

    if has("password", "passwd"):
        return GENERIC_PASSWORD
    if has("secret", "client_secret"):
        return "A secret/key generated in the provider's admin console. Copy it " \
               "immediately (often shown only once). Stored encrypted."
    if has("token", "api_key", "apikey", "access_key", "accesskey"):
        return "An access token / API key generated in the provider's admin " \
               "console or account settings. Copy it immediately; it is stored " \
               "encrypted."
    if has("client_id", "app_id", "application_id"):
        return "The application/client ID from the provider's app registration " \
               "(admin console -> app/API registration -> Overview)."
    if has("tenant", "directory"):
        return "The tenant/directory ID from your identity provider (e.g. Entra " \
               "ID -> Overview -> Directory (tenant) ID)."
    if n in ("host", "hostname", "server", "server_hostname"):
        return GENERIC_HOST
    if n == "port" or n.endswith("_port"):
        return GENERIC_PORT
    if has("database", "catalog", "dataset", "db_name"):
        return GENERIC_DB
    if n == "schema" or has("schema"):
        return GENERIC_SCHEMA
    if n in ("user", "username", "uid", "login"):
        return GENERIC_USER
    if has("region"):
        return AWS_REGION
    if has("url", "endpoint", "uri"):
        return "The base URL/endpoint of the service, from your admin console or " \
               "the provider's connection details panel. Include https:// but no " \
               "trailing path unless specified."
    if has("bucket"):
        return "The storage bucket / container name from your cloud storage console."
    if has("workspace", "project"):
        return "The workspace/project identifier, visible in the provider's console " \
               "or in the resource URL."
    if title:
        return f"{title}. Ask your admin / provider console for this value."
    return "Ask your admin or the provider's console for this value. Optional " \
           "fields can be left blank."


def _iter_schema_fields(schema: dict | None):
    """Yield (name, title, description, required) from a JSON schema dict."""
    if not isinstance(schema, dict):
        return
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    for name, spec in props.items():
        spec = spec or {}
        yield (
            name,
            spec.get("title") or name,
            spec.get("description") or "",
            name in required,
        )


def build_connector_docs(
    dtype: str,
    config_fields: dict | None = None,
    credentials_fields: dict | None = None,
    credentials_by_auth: dict | None = None,
    meta: dict | None = None,
) -> dict:
    """
    Return a COMPLETE docs block for any connector — curated + generic fallback.

    Shape (mirrors the frontend ConnectorDoc + extras for the .docx):
      {
        type, title, overview, curated: bool,
        whereToGet: {field_name: hint},   # every visible field covered
        fields: [{name, title, required, whereToGet}],  # ordered, for docx table
        authFlow: [str], notes: str, officialDocsUrl: str | None,
      }
    """
    meta = meta or {}
    key = resolve_key(dtype)
    curated = CONNECTOR_DOCS.get(key) if key else None
    curated_where = (curated or {}).get("whereToGet", {})

    # Collect fields in a stable order: config first, then credentials, then
    # any extra fields that only appear under a specific auth mode.
    ordered: list[tuple[str, str, str, bool]] = []
    seen: set[str] = set()

    def add_from(schema: dict | None):
        for name, title, desc, req in _iter_schema_fields(schema):
            if name in seen:
                continue
            seen.add(name)
            ordered.append((name, title, desc, req))

    add_from(config_fields)
    add_from(credentials_fields)
    for _mode, sch in (credentials_by_auth or {}).items():
        add_from(sch)

    where: dict[str, str] = {}
    fields: list[dict] = []
    for name, title, desc, req in ordered:
        hint = curated_where.get(name) or _generic_hint(name, title, desc)
        where[name] = hint
        fields.append({"name": name, "title": title, "required": req, "whereToGet": hint})

    # If schema introspection yielded nothing (rare), fall back to curated keys.
    if not fields and curated_where:
        for name, hint in curated_where.items():
            where[name] = hint
            fields.append({"name": name, "title": name, "required": False, "whereToGet": hint})

    title = meta.get("title") or (dtype or "").replace("_", " ").title()
    if curated:
        overview = (
            f"Set up the {title} connector. An admin enters the connection details "
            "below once; the panel explains where each value comes from."
        )
        auth_flow = list(curated.get("authFlow") or [])
        notes = curated.get("notes") or ""
    else:
        overview = (
            f"Set up the {title} connector. Enter the connection details below. "
            "Each field's help explains where to find the value — check your admin "
            "console or ask the team that owns this system."
        )
        auth_flow = [
            "Gather the credentials from the system's admin console or the team "
            "that owns it (a read-only / least-privilege account is recommended).",
            "If \"Require user authentication\" is available and enabled, each user "
            "connects with their own identity so per-user permissions apply.",
        ]
        notes = (
            "Make sure the CityAgent Insights host can reach this service over the "
            "network (firewall / security group / allowlist), and use a read-only "
            "account scoped to just the data you need. Secrets are stored encrypted."
        )

    return {
        "type": dtype,
        "title": title,
        "overview": overview,
        "curated": bool(curated),
        "whereToGet": where,
        "fields": fields,
        "authFlow": auth_flow,
        "notes": notes,
        "officialDocsUrl": OFFICIAL_DOCS.get(key) if key else None,
    }


# --- Minimal, dependency-free .docx writer -----------------------------------
# A .docx is a ZIP of WordprocessingML. We build it with stdlib `zipfile` +
# templated XML so no extra Python dependency is needed (python-docx is NOT in
# the image, and `uv sync --frozen` would ignore an unlocked add).

_ACCENT = "2F6DF0"
_MUTED = "586074"


def _xml_escape(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _run(text: str, *, bold=False, sz=21, color=None) -> str:
    rpr = "<w:rPr>"
    if bold:
        rpr += "<w:b/>"
    rpr += f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    rpr += "</w:rPr>"
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def _para(text="", *, bold=False, sz=21, color=None, align=None, space_before=0, space_after=80) -> str:
    ppr = "<w:pPr>"
    ppr += f'<w:spacing w:before="{space_before}" w:after="{space_after}"/>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    ppr += "</w:pPr>"
    body = _run(text, bold=bold, sz=sz, color=color) if text else ""
    return f"<w:p>{ppr}{body}</w:p>"


def _cell(runs_xml: str, width: int) -> str:
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>'
        f'<w:tcMar><w:top w:w="40" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/>'
        f'<w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tcMar>'
        f"</w:tcPr>{runs_xml}</w:tc>"
    )


def render_setup_docx(docs: dict) -> bytes:
    """
    Render a `docs` block (from build_connector_docs) into a real .docx file
    (stdlib zipfile — no external dependency) and return its bytes. A fillable
    setup worksheet an admin can hand to whoever holds the credentials.
    """
    import io
    import zipfile

    docs = docs or {}
    title = docs.get("title") or "Connector"
    body: list[str] = []

    # Title + overview
    body.append(_para(f"{title} — connection setup", bold=True, sz=40, color=_ACCENT, space_after=60))
    if docs.get("overview"):
        body.append(_para(docs["overview"], sz=20, color=_MUTED, space_after=160))

    # Where to get each value (fillable 3-col table)
    body.append(_para("Where to get each value", bold=True, sz=28, color=_ACCENT, space_before=120))
    body.append(_para(
        "Fill the right-hand column with the value from your admin console, then "
        "hand this back to whoever is setting up the connector.",
        sz=18, color=_MUTED,
    ))

    widths = (2600, 5400, 2000)
    borders = (
        "<w:tblBorders>"
        + "".join(
            f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="D3D9E4"/>'
            for s in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders>"
    )
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    rows = []
    # header row
    hdr_cells = "".join(
        _cell(_para(lbl, bold=True, sz=18, space_after=0), w)
        for lbl, w in zip(("Field", "Where to get it", "Your value"), widths)
    )
    rows.append(f"<w:tr>{hdr_cells}</w:tr>")
    for f in docs.get("fields") or []:
        name = f.get("title") or f.get("name") or ""
        if f.get("required"):
            name += "  *"
        cells = (
            _cell(_para(name, bold=True, sz=18, space_after=0), widths[0])
            + _cell(_para(f.get("whereToGet") or "", sz=18, space_after=0), widths[1])
            + _cell(_para("", sz=18, space_after=0), widths[2])
        )
        rows.append(f"<w:tr>{cells}</w:tr>")
    table = (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        + borders
        + "</w:tblPr>"
        + grid
        + "".join(rows)
        + "</w:tbl>"
    )
    body.append(table)
    body.append(_para("* required field", sz=16, color=_MUTED, space_before=40))

    # Authentication options
    if docs.get("authFlow"):
        body.append(_para("Authentication options", bold=True, sz=28, color=_ACCENT, space_before=160))
        for step in docs["authFlow"]:
            body.append(_para("•  " + step, sz=19, space_after=60))

    # Prerequisites / notes
    if docs.get("notes"):
        body.append(_para("Prerequisites & notes", bold=True, sz=28, color=_ACCENT, space_before=160))
        body.append(_para(docs["notes"], sz=19))

    # Official docs link
    if docs.get("officialDocsUrl"):
        body.append(
            "<w:p><w:pPr><w:spacing w:before=\"120\" w:after=\"80\"/></w:pPr>"
            + _run("Official documentation: ", bold=True, sz=18)
            + _run(docs["officialDocsUrl"], sz=18, color=_ACCENT)
            + "</w:p>"
        )

    body.append(_para(
        "Generated by CityAgent Insights · setup worksheet",
        sz=16, color=_MUTED, align="center", space_before=200,
    ))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) +
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1200" w:right="1200" w:bottom="1200" w:left="1200" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()
