// connectorDocs.ts
// -----------------------------------------------------------------------------
// Curated, human-written setup help for every data connector in the app.
//
// Product-name mentions inside `notes` are written as the `{product}` token and
// resolved against the instance branding by `getConnectorDoc()` — see
// composables/useBranding.ts.
//
// Powers the "Download setup worksheet" feature (see connectorWorksheet.ts): for
// each connector we describe WHERE an admin gets each credential/config value,
// the per-user connect flow, and any gotchas — so a non-technical admin can hand
// the worksheet to whoever holds the credentials (IT / cloud admin / DBA).
//
// `whereToGet` is keyed by the connector's field name (the same names the
// backend `/data_sources/{type}/fields` endpoint returns, derived from the
// Pydantic config/credential schemas in
// backend/app/schemas/data_sources/configs.py). Any field not explicitly listed
// falls back to a name-derived generic hint in connectorWorksheet.ts, so every
// connector — curated or not — always produces a complete worksheet.
// -----------------------------------------------------------------------------

import { getBranding } from '~/composables/useBranding'

export interface ConnectorDoc {
  /** field name -> where/how to obtain that value */
  whereToGet: Record<string, string>
  /** ordered steps describing the per-user (sign-in) connect flow, if any */
  authFlow: string[]
  /** free-form notes / prerequisites / gotchas shown at the bottom */
  notes: string
}

// Generic reusable snippets ---------------------------------------------------
const GENERIC_HOST =
  'Hostname or IP of the server, from your DBA or infrastructure team (e.g. db.internal.company.com). Do not include the protocol or port.'
const GENERIC_PORT =
  'The TCP port the service listens on. Use the pre-filled default unless your DBA changed it.'
const GENERIC_DB =
  'The database (catalog) name to connect to. Ask your DBA which database holds the data you need.'
const GENERIC_SCHEMA =
  'Optional. Restrict discovery to one schema. Leave blank to index all schemas the account can see.'
const GENERIC_USER =
  'A dedicated read-only service account username. Ask your DBA to create one scoped to just the required schemas.'
const GENERIC_PASSWORD =
  'The password for the service account above. Provided by your DBA — keep it secret; it is stored encrypted.'
const AWS_ACCESS_KEY =
  'AWS Console -> IAM -> Users -> (your service user) -> Security credentials -> Create access key. This is the Access key ID.'
const AWS_SECRET_KEY =
  'Shown once when you create the access key above (AWS IAM). Copy it immediately — AWS will not show the secret again.'
const AWS_REGION =
  'The AWS region code the resource lives in, e.g. us-east-1. Visible in the top-right region selector of the AWS Console.'

// -----------------------------------------------------------------------------
// Curated connectors — rich, portal-exact guidance.
// Keys are the normalized (lowercased) registry `type`.
// -----------------------------------------------------------------------------
export const connectorDocs: Record<string, ConnectorDoc> = {
  postgresql: {
    whereToGet: {
      host: GENERIC_HOST + ' For managed Postgres: AWS RDS -> Databases -> (instance) -> Endpoint; or Azure/GCP the "Host"/"Public IP" on the instance overview.',
      port: 'Default 5432. Your DBA confirms if changed.',
      database: GENERIC_DB,
      schema: GENERIC_SCHEMA + ' Default is "public".',
      user: GENERIC_USER,
      password: GENERIC_PASSWORD,
    },
    authFlow: [
      'System auth: the admin supplies one read-only service account here; everyone queries through it.',
      'Ask the DBA: CREATE ROLE insights LOGIN PASSWORD \'…\'; GRANT CONNECT ON DATABASE … ; GRANT USAGE ON SCHEMA … ; GRANT SELECT ON ALL TABLES IN SCHEMA … .',
    ],
    notes: 'Ensure the server allows connections from the {product} host IP (pg_hba.conf / cloud firewall / security group). For cloud RDS the security group must allow inbound 5432 from the app.',
  },
  mysql: {
    whereToGet: {
      host: GENERIC_HOST,
      port: 'Default 3306.',
      database: GENERIC_DB,
      user: GENERIC_USER,
      password: GENERIC_PASSWORD,
    },
    authFlow: [
      'System auth: one read-only MySQL user shared by everyone.',
      'DBA grant: CREATE USER \'insights\'@\'%\' IDENTIFIED BY \'…\'; GRANT SELECT ON db.* TO \'insights\'@\'%\'; FLUSH PRIVILEGES;',
    ],
    notes: 'The MySQL user host mask (@\'%\' or a specific IP) must permit the {product} server. Check the cloud firewall / security group allows inbound 3306.',
  },
  mariadb: {
    whereToGet: {
      host: GENERIC_HOST,
      port: 'Default 3306.',
      database: GENERIC_DB,
      user: GENERIC_USER,
      password: GENERIC_PASSWORD,
    },
    authFlow: ['System auth: one read-only MariaDB user shared by everyone.'],
    notes: 'Same as MySQL — grant SELECT to a dedicated user and open port 3306 to the app host.',
  },
  mssql: {
    whereToGet: {
      host: GENERIC_HOST + ' For a named instance use host\\\\INSTANCE; for Azure SQL it is <server>.database.windows.net.',
      port: 'Default 1433.',
      database: GENERIC_DB,
      schema: GENERIC_SCHEMA + ' Default is "dbo".',
      odbc_driver: 'Leave the default (18) unless your server requires an older ODBC driver.',
      encrypt: 'Leave enabled for Azure SQL and modern servers.',
      user: 'A SQL Server login (SQL authentication). Ask your DBA to create a read-only login.',
      password: GENERIC_PASSWORD,
    },
    authFlow: [
      'System auth (SQL login): admin supplies one read-only login used by everyone.',
      'Kerberos / Windows Integrated: uses the app\'s own domain identity (no password stored).',
      'Kerberos SSO (per-user): the app impersonates the signed-in user via constrained delegation — no secret stored.',
      'DBA: CREATE LOGIN insights WITH PASSWORD=\'…\'; CREATE USER insights FOR LOGIN insights; GRANT SELECT/VIEW DEFINITION on the target schema.',
    ],
    notes: 'Azure SQL: allow the app host IP under the server firewall. On-prem: open TCP 1433 and confirm SQL authentication is enabled (Mixed Mode).',
  },
  snowflake: {
    whereToGet: {
      account: 'Snowflake account identifier, e.g. ABCDEF-GH12345. In Snowsight: bottom-left account menu -> Account -> "Account/Server URL" (the part before .snowflakecomputing.com), or Admin -> Accounts.',
      warehouse: 'A virtual warehouse (compute) name. Snowsight -> Admin -> Warehouses. Use a small XS warehouse dedicated to analytics.',
      database: 'The database to query. Snowsight -> Data -> Databases.',
      schema: 'Schema within the database (comma-separated list allowed). Snowsight -> Data -> Databases -> (db) -> Schemas.',
      role: 'Optional. The Snowflake role that grants access to the above objects (e.g. INSIGHTS_RO). Snowsight -> Admin -> Users & Roles -> Roles.',
      user: 'A dedicated Snowflake user. Snowsight -> Admin -> Users & Roles -> Users -> + User.',
      password: 'That user\'s password.',
      private_key_pem: 'Key-pair auth: the PEM private key you generated (openssl genrsa) whose public key is assigned to the user via ALTER USER … SET RSA_PUBLIC_KEY.',
      private_key_passphrase: 'Optional passphrase protecting the private key above.',
    },
    authFlow: [
      'Username / Password OR Key Pair (recommended for service accounts).',
      'Key pair: generate an RSA key, run ALTER USER insights SET RSA_PUBLIC_KEY=\'<pub>\', then paste the private key here.',
      'Grant: GRANT USAGE ON WAREHOUSE …; GRANT USAGE ON DATABASE/SCHEMA …; GRANT SELECT ON ALL TABLES … TO ROLE INSIGHTS_RO;',
    ],
    notes: 'Prefer key-pair auth for a service account (no password rotation). If your Snowflake enforces network policies, allowlist the {product} egress IP.',
  },
  bigquery: {
    whereToGet: {
      project_id: 'Google Cloud project ID. GCP Console -> top project selector, or Console -> IAM & Admin -> Settings -> "Project ID".',
      dataset: 'The BigQuery dataset to query. Console -> BigQuery -> Explorer -> (project) -> the dataset name.',
      credentials_json: 'A Service Account key JSON. Console -> IAM & Admin -> Service Accounts -> Create (grant "BigQuery Data Viewer" + "BigQuery Job User") -> Keys tab -> Add key -> JSON. Paste the whole file contents.',
    },
    authFlow: [
      'Service Account JSON (recommended): create a service account, grant BigQuery Data Viewer + Job User, download a JSON key, paste it here.',
      'Sign in with Google (per-user): each user authorizes with their own Google account instead of a shared key.',
    ],
    notes: 'The service account needs roles/bigquery.dataViewer on the dataset and roles/bigquery.jobUser on the project. Keep the JSON key secret — it is stored encrypted.',
  },
  databricks_sql: {
    whereToGet: {
      server_hostname: 'Databricks -> SQL Warehouses -> (your warehouse) -> Connection details -> "Server hostname" (e.g. dbc-1234.cloud.databricks.com).',
      http_path: 'Same "Connection details" panel -> "HTTP path" (e.g. /sql/1.0/warehouses/abc123).',
      catalog: 'The Unity Catalog to query. Databricks -> Catalog explorer.',
      schema: 'Optional schema within the catalog. Databricks -> Catalog explorer -> (catalog) -> schemas.',
      access_token: 'Databricks -> User Settings (top-right avatar) -> Developer -> Access tokens -> Generate new token. Copy it immediately.',
    },
    authFlow: [
      'Personal Access Token (PAT): generate under User Settings -> Developer -> Access tokens.',
      'For a service identity, generate the PAT from a service principal rather than a personal user.',
    ],
    notes: 'The token owner must have USE CATALOG / USE SCHEMA / SELECT on the target Unity Catalog objects and CAN USE on the SQL Warehouse.',
  },
  powerbi: {
    whereToGet: {
      tenant_id: 'Azure Portal -> Microsoft Entra ID -> Overview -> "Directory (tenant) ID".',
      client_id: 'Azure Portal -> Entra ID -> App registrations -> (your app) -> Overview -> "Application (client) ID".',
      client_secret: 'Azure Portal -> Entra ID -> App registrations -> (your app) -> Certificates & secrets -> New client secret -> copy the Value (not the Secret ID) immediately.',
      workspaces: 'Optional. Comma-separated Power BI workspace IDs to scope to. In Power BI Service open the workspace; the ID is in the URL (…/groups/<workspace-id>/…). Leave blank to auto-discover.',
    },
    authFlow: [
      'Service Principal (Azure AD): register an app in Entra ID, create a client secret, and in the Power BI Admin Portal enable "Allow service principals to use Power BI APIs" and add the app to each workspace as a Member/Viewer.',
      'Sign in with Microsoft (per-user): each user authorizes with their own Microsoft account; RLS applies per user.',
    ],
    notes: 'Power BI Admin Portal -> Tenant settings must allow service principals. The app must be added to the target workspace(s). Datasets are queried via DAX.',
  },
  powerbi_report_server: {
    whereToGet: {
      username: 'A Windows/AD account that can view reports on the Report Server. Format DOMAIN\\\\user or user.',
      password: 'That account\'s password.',
      domain: 'The Active Directory domain (NetBIOS name) for NTLM auth.',
    },
    authFlow: ['System auth (NTLM): admin supplies one AD account used to browse the on-prem Report Server.'],
    notes: 'On-prem only. The app host must reach the Report Server URL and the account needs Browser role on the target folders.',
  },
  tableau: {
    whereToGet: {
      server_url: 'Your Tableau Server/Cloud base URL, e.g. https://10ax.online.tableau.com or https://tableau.company.com.',
      site_name: 'The Tableau site (content URL). For Tableau Cloud it is the segment after /site/ in the URL; blank = Default site.',
      api_version: 'Leave default (matches recent Tableau). Change only for older on-prem Server versions.',
      pat_name: 'Tableau -> (avatar) My Account Settings -> Personal Access Tokens -> the token name you created.',
      pat_token: 'The secret shown once when you create the Personal Access Token. Copy it immediately.',
    },
    authFlow: [
      'Personal Access Token (PAT): each user (or a service account) creates a PAT under My Account Settings -> Personal Access Tokens.',
      'The PAT owner needs access to the published data sources you want to query (Metadata API + VizQL Data Service must be enabled by the Tableau admin).',
    ],
    notes: 'Tableau admin must enable the Metadata API and VizQL Data Service on the site. PATs expire after inactivity — a service PAT is recommended.',
  },
  salesforce: {
    whereToGet: {
      domain: 'Use "login" for production, or your My Domain (e.g. mycompany) — Setup -> Company Settings -> My Domain.',
      sandbox: 'Enable if connecting to a sandbox org (login host test.salesforce.com).',
      username: 'The Salesforce username (an email-like login) of a dedicated integration user.',
      password: 'That user\'s password.',
      security_token: 'Salesforce -> (avatar) Settings -> My Personal Information -> Reset My Security Token. It is emailed to the user; append it after the password.',
    },
    authFlow: [
      'Username / Password + Security Token: create an integration user with a Profile/Permission Set granting read (View All Data or object-level Read).',
      'Reset the security token from the user\'s personal settings; it arrives by email.',
    ],
    notes: 'If the org enforces IP ranges, either allowlist the app IP or rely on the security token. API access must be enabled on the user\'s profile.',
  },
  mongodb: {
    whereToGet: {
      host: 'Hostname of the mongod / cluster. For Atlas: Atlas -> Database -> Connect -> the host in the connection string (e.g. cluster0.ab12.mongodb.net).',
      port: 'Default 27017. Omit / ignore when using SRV (Atlas).',
      database: 'The database to query. Atlas -> Browse Collections, or ask your team.',
      auth_source: 'Optional. The database that holds the user credentials (often "admin"). Atlas uses "admin".',
      use_srv: 'Enable for Atlas / mongodb+srv:// connection strings (DNS-based seedlist).',
      tls: 'Enable for Atlas and any TLS-secured cluster.',
      user: 'A read-only database user. Atlas -> Database Access -> Add New Database User (role readAnyDatabase or read on the target db).',
      password: 'That database user\'s password.',
    },
    authFlow: [
      'Username / Password: create a read-only DB user (Atlas: Database Access; self-hosted: db.createUser with role "read").',
      'For Atlas, enable SRV + TLS and allowlist the app IP under Network Access.',
    ],
    notes: 'Atlas -> Network Access must allowlist the {product} egress IP (or 0.0.0.0/0 for testing only).',
  },
  s3: {
    whereToGet: {
      bucket: 'The S3 bucket name. AWS Console -> S3 -> Buckets.',
      prefix: 'Optional. A key prefix (folder) to scope to, e.g. reports/2026/. Leave blank for the whole bucket.',
      region: AWS_REGION,
      endpoint_url: 'Optional. Only for S3-compatible stores (MinIO, Wasabi, R2). Leave blank for AWS S3.',
      access_key: AWS_ACCESS_KEY + ' The IAM user needs s3:GetObject + s3:ListBucket on this bucket.',
      secret_key: AWS_SECRET_KEY,
      session_token: 'Optional. Only for temporary (STS) credentials.',
      role_arn: 'Assume-Role auth: AWS Console -> IAM -> Roles -> (role) -> copy the Role ARN. The app assumes it via STS.',
    },
    authFlow: [
      'AWS Access Key: an IAM user with s3:GetObject + s3:ListBucket on the bucket/prefix.',
      'AWS Assume Role (STS): the app assumes an IAM role you specify by ARN.',
      'AWS Default Chain: use the host\'s instance profile / IRSA — no keys entered.',
    ],
    notes: 'Grant the least-privilege policy: s3:ListBucket on the bucket + s3:GetObject on the prefix. Files (PDF/Word/Excel/CSV) become searchable objects.',
  },
  aws_redshift: {
    whereToGet: {
      host: 'Redshift -> Clusters -> (cluster) -> "Endpoint" (host part, before :port). Redshift Serverless: the workgroup endpoint.',
      port: 'Default 5439.',
      database: 'The Redshift database name (Clusters -> (cluster) -> Properties -> Database name).',
      schema: GENERIC_SCHEMA + ' Default "public".',
      region: AWS_REGION,
      cluster_identifier: 'Optional (needed for IAM auth). Redshift -> Clusters -> the cluster identifier.',
      user: 'A read-only Redshift user. Ask your DBA: CREATE USER insights PASSWORD \'…\'; GRANT SELECT … .',
      password: GENERIC_PASSWORD,
      access_key: AWS_ACCESS_KEY,
      secret_key: AWS_SECRET_KEY,
      role_arn: 'For Assume-Role auth: the IAM role ARN from AWS IAM -> Roles.',
    },
    authFlow: [
      'Username / Password: a database user with SELECT on the target schema.',
      'AWS Keys (IAM) / Assume Role (ARN): temporary credentials minted via redshift:GetClusterCredentials.',
    ],
    notes: 'The cluster security group must allow inbound 5439 from the app host. For IAM auth the AWS principal needs redshift:GetClusterCredentials on the cluster/dbuser.',
  },
  clickhouse: {
    whereToGet: {
      host: GENERIC_HOST + ' ClickHouse Cloud: Console -> your service -> Connect -> the host.',
      port: 'Default 8123 (HTTP) or 8443 (HTTPS). ClickHouse Cloud uses 8443.',
      database: 'The database to query. Default "default".',
      secure: 'Enable (HTTPS) for ClickHouse Cloud and any TLS endpoint.',
      user: 'A read-only ClickHouse user. Cloud: Console -> Settings; self-hosted: CREATE USER … with GRANT SELECT.',
      password: 'That user\'s password. ClickHouse Cloud shows the default-user password on service creation.',
    },
    authFlow: [
      'Username / Password over the HTTP(S) interface.',
      'ClickHouse Cloud: use the connection details panel; keep Secure enabled (port 8443).',
    ],
    notes: 'ClickHouse Cloud: add the app egress IP to the service IP Access List. Grant the user SELECT on the target database only.',
  },
}

// -----------------------------------------------------------------------------
// Aliases: registry `type` -> curated key. Lets the exact registry type
// (e.g. "MSSQL", "databricks_sql", "aws_redshift") resolve to a curated entry.
// -----------------------------------------------------------------------------
const ALIASES: Record<string, string> = {
  mssql: 'mssql',
  microsoft_sql_server: 'mssql',
  databricks: 'databricks_sql',
  redshift: 'aws_redshift',
  postgres: 'postgresql',
  mongo: 'mongodb',
  amazon_s3: 's3',
  power_bi: 'powerbi',
}

/**
 * Resolve curated docs for a connector `type` (case-insensitive, alias-aware).
 * Returns undefined when there is no curated entry — callers then use the
 * generic field-name-derived fallback in connectorWorksheet.ts.
 */
export function getConnectorDoc(type: string | undefined | null): ConnectorDoc | undefined {
  if (!type) return undefined
  const key = String(type).toLowerCase()
  if (connectorDocs[key]) return withBrand(connectorDocs[key])
  const aliased = ALIASES[key]
  if (aliased && connectorDocs[aliased]) return withBrand(connectorDocs[aliased])
  return undefined
}

/**
 * Replace the `{product}` token with the configured product name. Returns a
 * shallow copy so the curated map itself is never mutated. Defaults to today's
 * literal name, so an unconfigured instance reads exactly as before.
 */
export function resolveBrandTokens(text: string): string {
  return text.replace(/\{product\}/g, getBranding().productName)
}

function withBrand(doc: ConnectorDoc): ConnectorDoc {
  if (!doc.notes.includes('{product}')) return doc
  return { ...doc, notes: resolveBrandTokens(doc.notes) }
}
