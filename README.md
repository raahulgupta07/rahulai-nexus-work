# CityAgent Insights

**Current version: `0.0.490.18`** — see [CHANGELOG.md](CHANGELOG.md) for what shipped, and [UPGRADE.md](UPGRADE.md) to install or upgrade.

**Your self-hosted AI coworker for data** — agents that connect to your databases, files, and BI tools, then query, analyze, build dashboards and decks, and explain their reasoning. Enterprise-ready: SSO, RBAC, audit, LDAP/SCIM, per-org model controls.

Each agent gets its own data, tools, credentials, instructions, and permissions. Start in chat, then run the same agents in reports, dashboards, automations, scheduled tasks, team channels, and MCP clients.

Bring your own models and infrastructure. Nothing leaves your box — telemetry and external phone-home are off by default.

---

## Quick Start

Build from source (the prebuilt public image is **not** whitelabeled — always build your own):

```bash
git clone git@github.com:raahulgupta07/rahulai-nexus-work.git
cd rahulai-nexus-work

cp .env.example .env      # fill in the two values marked REQUIRED
chmod 600 .env

docker compose -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
docker compose -f docker-compose.dev.yaml up -d
```

App runs at **http://localhost:8095**. First signup bootstraps the org and becomes owner/admin.

`.env.example` documents every setting with its real default. Read the note at the top of it before you start — `DASH_ENCRYPTION_KEY` is generated once and must never change. Leaving it empty does not stop start-up: the application prints a warning, generates a throwaway key and carries on, so on an installation that already holds credentials the loss is permanent and easy to miss.

**Upgrading a machine installed before the rename:** its `.env` carries `BOW_ENCRYPTION_KEY` and `BOW_DATABASE_URL`. Both spellings are still read, but rename them to `DASH_` while you are there — see `UPGRADE.md`.

### Upgrading

```bash
./preflight.sh      # read-only: where am I now?
./upgrade.sh        # backup, tag, pull, build, verify, swap
```

Both are documented in [UPGRADE.md](UPGRADE.md), along with rollback.

### Upgrading by hand

Run these yourself if you would rather not run the script. It is the same
sequence `upgrade.sh` performs, one command at a time. Do them **in order** —
step 3 is what makes step 8 possible.

Names below (`dash-app`, `dash-postgres`, `docker-compose.dev.yaml`) are this
repo's defaults; step 0 prints the real ones for your machine.

```bash
cd /path/to/rahulai-nexus-work
```

**0 — See what is running.** Note the container names and the project.

```bash
docker ps --filter label=com.docker.compose.service=app \
  --format '{{.Names}}  project={{.Label "com.docker.compose.project"}}  image={{.Image}}'
docker exec dash-app cat /app/VERSION      # the version you are on now
```

**1 — Back up the database.** This is the only real way back to today's data.

```bash
mkdir -p ~/cityagent-backups
source .env    # so $POSTGRES_USER / $POSTGRES_DB below are the right ones
docker exec dash-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  -f /tmp/pre-upgrade.dump
docker cp dash-postgres:/tmp/pre-upgrade.dump \
  ~/cityagent-backups/pre-upgrade-$(date +%Y%m%d-%H%M).dump
ls -lh ~/cityagent-backups/
```

**Stop** if the file is only a few kilobytes — that is a failed dump, not a small
database. Dump *inside* the container and copy it out, as above: piping
`pg_dump` through a terminal can corrupt the binary format.

**2 — Back up `.env`.** It holds `DASH_ENCRYPTION_KEY`, and if that key ever
changes every stored credential becomes unreadable — silently, with no error.

```bash
cp .env ~/cityagent-backups/env-$(cat VERSION)
```

**3 — Tag the image you are running.** Step 5 rebuilds over this exact tag, so
without this tag the current image is deleted and there is nothing to roll back
to. Two working images have already been lost this way.

```bash
IMG=$(docker inspect -f '{{.Config.Image}}' dash-app)      # e.g. cityagentinsights:0.0.503
docker tag "$IMG" "cityagentinsights:pre-$(cat VERSION)"
docker image inspect "cityagentinsights:pre-$(cat VERSION)" >/dev/null && echo "rollback tag OK"
```

**4 — Get the new code.**

```bash
git status --short          # must print nothing
git pull --ff-only
cat VERSION                 # the version you are moving TO
```

**Stop** if `git status` shows changes — someone edited this checkout. Find out
what those changes are first. Never use `git reset --hard` to get past it.
**Stop** if `git pull` refuses; do not force it.

**5 — Build.** `FE_CACHEBUST` is not optional. Without it Docker reuses a cached
layer, the build succeeds, and it ships the **old** interface — the most
confusing failure this project has.

```bash
docker compose -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
```

Takes a few minutes. Needs ~10 GB of free disk — check with
`df -h /System/Volumes/Data` on a Mac (plain `df -h /` reports the wrong
volume). A disk-full build has crashed Docker outright.

**6 — Check the image you just built, not the app still running.**

```bash
docker run --rm --entrypoint sh "$IMG" -c 'cat /app/VERSION'
```

**Stop unless this prints the version from step 4.** If it prints the old one
the build did not pick up your source: run `docker builder prune`, then repeat
step 5. Nothing has been swapped yet, so the running app is still fine.

**7 — Swap.** Database migrations run automatically as the new container starts.

```bash
docker compose -f docker-compose.dev.yaml up -d app
sleep 15
curl -s -o /dev/null -w 'health %{http_code}\n' http://localhost:8095/health
curl -s http://localhost:8095/api/changelog | head -c 120     # should name the new version
docker exec -w /app/backend dash-app alembic current | tail -1
```

Then hard-refresh the browser (**Cmd/Ctrl + Shift + R**) — an open tab keeps
running the old bundle until it reloads.

**8 — If it went wrong**, go back to the image you tagged in step 3:

```bash
docker tag "cityagentinsights:pre-<old version>" "$IMG"
docker compose -f docker-compose.dev.yaml up -d app
```

That returns the code. It does **not** undo database migrations — if those need
reverting too, restore the step 1 dump.

Docker Compose and Kubernetes deployments are provided for servers.

---

## Features

- **Analysis:** Create reports and dashboards, generate queries, and run deep or root-cause analysis.
- **Trustworthy dashboards:** A dashboard is never built on part of its data — if a result is too large to carry in full, the agent is told to summarise it first rather than charting a fraction of it. Every dashboard is compiled before it is saved, so a broken one never reaches the screen. And each one carries a short written summary above the tiles: the headline, which way the numbers are moving, and what stands out — with every figure checked against the dashboard's own data before it is shown.
- **Agent context:** Configure each agent with the right data, tools, credentials, instructions, permissions, and starters.
- **File agents:** Upload CSV/Excel/Word/PDF — an LLM librarian reads each file and routes it to a queryable table, an instruction, a skill, or the knowledge base.
- **Local Runtime:** Pair a small helper app on your laptop and agent-generated Python runs on *your* machine. Connect local folders from the paperclip menu (native folder picker or typed path) and query CSV/Excel files in place — data never leaves your device, and results carry a "Computed on your device" badge. Warehouse credentials stay server-side via a data proxy. macOS menu-bar app, Windows tray app, and a single-file CLI (`local-runtime/helper.py`).
- **Attachment-aware chat:** Every message shows what it was asked against — a green chip per connected folder and a blue chip per uploaded file. Folder attachments stay with the conversation across turns and reloads until you detach them.
- **Automations:** Schedule reports, run recurring tasks, and trigger investigations from events and webhooks.
- **Channels:** Run headlessly via Claude Code, Codex, and other MCP clients, or through Microsoft Teams, Slack, Google Chat, WhatsApp, email, Excel, and the web app. Slack connects over Socket Mode — an outbound connection, so nothing needs to be exposed to the internet.
- **MCP gateway:** Connect agents to MCP servers and custom APIs, then expose their context and tools through one governed gateway.
- **Evals and self-improvement:** Set evals for expected behavior; on failure, agents can draft instruction fixes and re-run the evals — passing changes wait for approval or promote automatically.
- **Governance:** RBAC, approvals, audit logs, service accounts, SSO, and model policies. Members can build private agents from files they upload; connecting a database, warehouse or BI tool stays with administrators. Settings carries a switch per built-in agent — turn one off and it disappears from everyone's list and from the chat picker, and stops being given to the AI, without deleting anything.
- **Operations:** `./preflight.sh` reports the state of an install without changing anything; `./upgrade.sh` takes the backups, tags a rollback image, pulls, builds, verifies the built image really is the new version, swaps and waits for health — stopping rather than continuing past any failed check. `./upgrade.sh --rollback` returns to the previous image without touching the database. An open tab is told when a new version is deployed, and a browser holding a stale service worker left by a previous occupant of the hostname repairs itself on the next visit.

---

## Bring Any LLM

Use your own API keys, endpoints, and deployments. Multiple providers and models can be configured in the same environment. This build defaults to **OpenRouter** (OpenAI-compatible) for model access.

| Provider | Supported | Notes |
|---|---|---|
| **OpenRouter** | Any OpenRouter model | Default. One key, many models |
| **OpenAI** | GPT and reasoning models | OpenAI API |
| **Azure OpenAI** | GPT and reasoning models | Endpoints + deployment names |
| **Google Gemini** | Gemini and Flash models | Google API key |
| **Anthropic** | Claude models | Anthropic API key |
| **AWS Bedrock** | Bedrock foundation models | API key, AWS access key, or IAM |
| **Any OpenAI-compatible API** | Ollama, Groq, Together, vLLM, LM Studio, … | Base URL + optional API key |

---

## Connect Any Data

**Databases / warehouses:** PostgreSQL, Snowflake, Google BigQuery, Databricks SQL, Microsoft Fabric, MySQL, AWS Athena, MariaDB, DuckDB, Microsoft SQL Server, ClickHouse, Azure Data Explorer, Vertica, AWS Redshift, Trino, Apache Pinot, Apache Druid, Oracle Database, MongoDB, Sybase SQL Anywhere, Teradata Vantage, SQLite, Spark

**BI tools:** Tableau, Power BI, **Power BI (User Sign-in)**, **Power BI (Multi-Tenant Sign-in)**, Power BI Report Server, Qlik Sense, Qlik QVD, Sisense, Oracle BI, Infor OLAP, Microsoft Analysis Services, **SAP BusinessObjects, SAP BW/BW4HANA, SAP HANA, SAP Datasphere**

> **Power BI sign-in modes:** *User Sign-in* lets each member authenticate with their own Microsoft account (email/password or MFA device code) — no shared service principal. *Multi-Tenant Sign-in* auto-discovers every tenant a user can reach from one consent and merges their workspaces.

**Business apps:** NetSuite, **Salesforce**, **Priority ERP**, ServiceNow, AWS Cost Explorer, PostHog, Outlook Mail

> **Priority ERP** catalogs *forms* rather than tables, keeps each field's Priority title so the agent reads the names your staff use, and follows subforms as joins. Sign in with a Personal Access Token (recommended), a dedicated API user, or — on-premise only — per-member "Sign in with Priority", where each member reaches exactly the forms their own Priority account allows.

**Search / observability:** Elasticsearch, OpenSearch, Splunk, Zabbix, Jaeger

**Semantic layer:** Timbr AI

**Files:** Files and Directories, Amazon S3, CSV, OneDrive, SharePoint

### Tools through MCP

Any MCP server or custom API. Ready-to-connect: Monday, Notion, Jira / Atlassian, Linear, Sentry, GitHub, Google Drive, Gmail, X, plus custom MCP servers and internal/third-party HTTP APIs.

### Run anywhere

Web app · Claude Code / Codex / MCP clients · Excel · Microsoft Teams · Slack · Google Chat · WhatsApp · Email · Webhooks and APIs · Scheduled tasks · Local Runtime helper on your own laptop (Settings → Local Runtime, pair with a 6-digit code).

Slack and Google Chat both run without a public URL: Slack over Socket Mode, Google Chat over your own Google Cloud project.

---

## Enterprise

For teams that need stronger security, compliance, and governance:

- **Self-hosted:** Deploy on your own infrastructure and keep control of your data.
- **SSO and provisioning:** Google Workspace and OIDC-compatible identity providers, with SCIM and LDAP support.
- **RBAC:** Fine-grained permissions on agents, data, tools, and administration.
- **Approvals and audit:** Review changes and track agent and data operations.
- **Service access:** API keys and service accounts for headless workflows.
- **Model controls:** Decide which providers and models are available to each organization.

---

## Security and Privacy

- **No phone-home.** Telemetry is off by default; there is no hardcoded analytics key. Verify in `dash-config.yaml`:

  ```yaml
  telemetry:
    enabled: false
  intercom:
    enabled: false
  ```

- **Secrets** (SSO/LDAP/SMTP/connector credentials) are Fernet-encrypted at rest with your `DASH_ENCRYPTION_KEY` and never returned to the client.
