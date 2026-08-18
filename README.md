# CityAgent Insights

**Current version: `0.0.543.1`** — see [CHANGELOG.md](CHANGELOG.md) for what shipped, and [UPGRADE.md](UPGRADE.md) to install or upgrade.

**Your self-hosted AI coworker for data** — agents that connect to your databases, files, and BI tools, then query, analyze, build dashboards and decks, and explain their reasoning. Enterprise-ready: SSO, RBAC, audit, LDAP/SCIM, per-org model controls.

Each agent gets its own data, tools, credentials, instructions, and permissions. Start in chat, then run the same agents in reports, dashboards, automations, scheduled tasks, team channels, and MCP clients.

Bring your own models and infrastructure. Nothing leaves your box — telemetry and external phone-home are off by default.

---

## Quick Start

Build from source (the prebuilt public image is **not** whitelabeled — always build your own).

This repository ships **two** compose files, and which one you choose is the
single most important decision of the install:

| | `docker-compose.yaml` | `docker-compose.dev.yaml` |
|---|---|---|
| Use it for | **any real server** | a laptop |
| HTTPS | yes, Caddy obtains and renews the certificate | none |
| Reachable on | 80 / 443 only | `APP_PORT`, default 8095 |
| Postgres | not published — inside the network only | **published on `POSTGRES_PORT`** |
| Storage volumes | `postgres_data`, `uploads_data`, … | `postgres_data_dev`, `uploads_data_dev`, … |

> **Do not run the development file on a server.** It publishes Postgres on a
> host port. If the firewall or cloud security group allows that port, the
> database is reachable from the internet with the password in your `.env`, and
> application traffic bypasses TLS. Nothing warns you; the stack looks healthy.

### Production install

```bash
git clone git@github.com:raahulgupta07/rahulai-nexus-work.git
cd rahulai-nexus-work

cp .env.example .env      # fill in the two values marked REQUIRED, and DOMAIN
chmod 600 .env

docker compose build --build-arg FE_CACHEBUST=$(date +%s) app
docker compose up -d
```

Before that first `up -d`, three things must already be true, because Caddy
asks a certificate authority for a real certificate the moment it starts:

1. `DOMAIN` in `.env` is the DNS name people will type.
2. That name already resolves to this server's public address.
3. Inbound **80 and 443** are open. Port 80 is how the certificate is issued —
   closing it does not harden the site, it prevents HTTPS altogether.

Then open **https://your-domain**. First signup bootstraps the org and becomes
owner/admin.

`curl localhost:8095` will **not** answer on a production install, and neither
will `psql -h localhost -p 5440` — nothing is published on the host except
Caddy. That is the point. To reach either service, go through the container:

```bash
docker exec dash-app curl -fsS http://localhost:3000/health
docker exec dash-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Testing Caddy itself from the host, plain HTTP answers **308**, not 200 — that
is the redirect to HTTPS working, not a fault. HTTPS answers 200:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/health    # 308
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/health   # 200
```

Both measured on a clean production stack brought up from this file.

### Development install (a laptop, no SSL)

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
```

App runs at **http://localhost:8095**.

★ The two are **not interchangeable after the fact.** They use different volume
names, so pointing an existing installation at the other file gives it an empty
database — it starts cleanly, reports healthy, and shows you a fresh signup
screen with all of your data still sitting in the volume it no longer looks at.
Pick one at install time and keep using it. Step 0 of the upgrade below prints
which one a running stack was started with, so you never have to remember.

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

Names below (`dash-app`, `dash-postgres`) are this repo's defaults; step 0
prints the real ones for your machine, **including which compose file your
stack was started with**. Use what step 0 tells you, not what you remember —
building with the other file swaps the storage volumes and hands the
application an empty database.

The shape of it, before the detail. Each step says what you should see, and the
five marked ⚠ are the ones that fail *quietly* — they do not error, they look
exactly like success:

| | Step | If you skip it |
|---|---|---|
| 0 | ⚠ See what is running, and with which compose file | You act on the wrong stack, or rebuild it onto empty volumes |
| 1 | ⚠ Back up the database | No way back to today's data |
| 2 | ⚠ Back up `.env` | Every stored credential becomes unreadable |
| 3 | ⚠ Tag the running image | Step 5 deletes it; nothing to roll back to |
| 4 | Pull the new code | — |
| 5 | ⚠ Build with `FE_CACHEBUST` | Build succeeds and ships the **old** interface |
| 6 | Check the built image | You deploy a build that did not take |
| 7 | Swap and verify | — |
| 8 | Roll back, if needed | — |

Everything up to step 6 is reversible and leaves the running app untouched. The
first step that changes what users see is **7**.

```bash
cd /path/to/rahulai-nexus-work
```

**0 — See what is running.** Note the container names and the project.

```bash
docker ps --filter label=com.docker.compose.service=app \
  --format '{{.Names}}  project={{.Label "com.docker.compose.project"}}  image={{.Image}}'
docker exec dash-app cat /app/VERSION      # the version you are on now
```

<details><summary>What this looks like</summary>

```
ro-ed-api                        project=ro-ed-lang       image=ro-ed-lang-app:latest
dash-app                         project=cityagentinsights  image=cityagentinsights:0.0.543.1
bow-app-dev                      project=bagofwords-upstream  image=bagofwords/bagofwords:latest
rise-app-1                       project=rise             image=rise-app
cityaicfcdemandforcasting-app-1  project=cityaicfcdemandforcasting  image=cityaicfcdemandforcasting-app

0.0.543.1
```

Five apps on this machine, only one of them ours. Read the `project=` column and
pick the row for **your** stack — that row gives you the container name for every
later step and the image tag for step 3. If a server only runs this product you
will see a single line.

</details>

Now ask the container which compose file it was started with. Docker recorded
it at start-up, so this is fact, not memory:

```bash
docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' dash-app
```

<details><summary>What this looks like</summary>

A production install:

```
/srv/rahulai-nexus-work/docker-compose.yaml
```

A development install — the name `docker-compose.dev.yaml` appears. It may be
alone, or after the base file, depending on how it was started. Both are
development:

```
/Users/you/rahulai-nexus-work/docker-compose.dev.yaml
```

```
/home/you/rahulai-nexus-work/docker-compose.yaml,/home/you/rahulai-nexus-work/docker-compose.dev.yaml
```

★ **Read the names, not how many there are.** A single file is not proof of a
production install — this repository's own development stack was started with
`-f docker-compose.dev.yaml` alone, and prints one line. The word `dev` in any
listed filename is the only thing that decides it.

</details>

Set `COMPOSE` from that answer, in the terminal you will use for the rest of
this: `-f ` in front of **each** filename the label printed, in the same order,
without the directory part. Every `docker compose` command below repeats it, so
all of them act on the stack you actually have:

```bash
COMPOSE="-f docker-compose.yaml"          # if that is the only name printed
# COMPOSE="-f docker-compose.dev.yaml"    # if that is the only name printed
# COMPOSE="-f docker-compose.yaml -f docker-compose.dev.yaml"   # if both printed
echo "docker compose $COMPOSE"
```

**Stop if the two do not match.** Building with a file your stack was not
started with is not a smaller mistake than the rest of this page — it is the
larger one. The two files use different volume names, so the new container
mounts empty storage: it starts, passes its health check, and offers a fresh
signup screen, while every report, connection and user sits untouched in a
volume nothing is reading. No error is printed at any point.

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

<details><summary>What this looks like</summary>

```
Successfully copied 19.6MB to /Users/you/cityagent-backups/pre-upgrade-20260801-1540.dump

-rw-r--r--  1 you  staff    19M Aug  1 15:40 pre-upgrade-20260801-1540.dump
```

**19 MB is a real dump.** A few kilobytes is a failed one.

</details>

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
IMG=$(docker inspect -f '{{.Config.Image}}' dash-app)      # e.g. cityagentinsights:0.0.542
docker tag "$IMG" "cityagentinsights:pre-$(cat VERSION)"
docker image inspect "cityagentinsights:pre-$(cat VERSION)" >/dev/null && echo "rollback tag OK"
```

<details><summary>What this looks like</summary>

```
rollback tag OK
```

`docker tag` prints nothing when it works, which is why the line above asks for
the tag back and prints something you can see. Keep `$IMG` in this same terminal
— steps 6 and 8 both use it.

</details>

**4 — Get the new code.**

```bash
git status --short          # must print nothing
git pull --ff-only
cat VERSION                 # the version you are moving TO
```

<details><summary>What this looks like</summary>

A clean checkout with nothing new to fetch — `git status` prints nothing at all:

```
Already up to date.
```

A normal upgrade instead ends with a file list and a version that moved:

```
Updating dedabbc9..6605eca7
Fast-forward
 162 files changed, 10446 insertions(+), 2981 deletions(-)
```

</details>

**Stop** if `git status` shows changes — someone edited this checkout. Find out
what those changes are first. Never use `git reset --hard` to get past it.
**Stop** if `git pull` refuses; do not force it.

If it refuses with *"refusing to merge unrelated histories"*, this checkout
predates the repository reset of 27 July. Fix it once, per checkout:

```bash
git fetch origin && git reset --hard origin/main
```

**5 — Build.** `FE_CACHEBUST` is not optional. Without it Docker reuses a cached
layer, the build succeeds, and it ships the **old** interface — the most
confusing failure this project has.

```bash
docker compose $COMPOSE build --build-arg FE_CACHEBUST=$(date +%s) app
```

<details><summary>What this looks like</summary>

Hundreds of lines scroll past. The only ones that matter are the last three:

```
#64 exporting to image
#64 naming to docker.io/library/cityagentinsights:0.0.543.1 done
#64 DONE 72.6s
 Image cityagentinsights:0.0.543.1 Built
```

The slowest stage is `yarn generate`, which compiles the interface — that is the
bulk of the wait, and it is why this cannot be a quick pull.

</details>

Takes a few minutes. Needs ~10 GB of free disk — check with
`df -h /System/Volumes/Data` on a Mac (plain `df -h /` reports the wrong
volume). A disk-full build has crashed Docker outright.

```
/dev/disk3s5   926Gi   822Gi    80Gi    92%   /System/Volumes/Data
                                └── this is the number that matters
```

**6 — Check the image you just built, not the app still running.**

```bash
docker run --rm --entrypoint sh "$IMG" -c 'cat /app/VERSION'
```

<details><summary>What this looks like</summary>

Good — matches the `cat VERSION` from step 4, so this build really is the new one:

```
0.0.543.1
```

Bad — the build silently reused a cached layer and produced the *previous*
release. It exits 0. Nothing warns you. This is the check that catches it:

```
0.0.543
```

</details>

**Stop unless this prints the version from step 4.** If it prints the old one
the build did not pick up your source: run `docker builder prune`, then repeat
step 5. Nothing has been swapped yet, so the running app is still fine.

**7 — Swap.** Database migrations run automatically as the new container starts.

```bash
docker compose $COMPOSE up -d app
sleep 15
docker exec dash-app curl -s -o /dev/null -w 'health %{http_code}\n' http://localhost:3000/health
docker exec dash-app curl -s http://localhost:3000/api/changelog | head -c 120
docker exec -w /app/backend dash-app alembic current | tail -1
```

★ These checks run **inside** the container deliberately, so the same three
lines work on a production install as on a development one. On production
nothing is published on the host and `curl localhost:8095` cannot answer; use
your real `https://` address in the browser instead.

<details><summary>What this looks like</summary>

```
health 200
{"current_version":"0.0.543.1","available":true,"versions":[{"version":"0.0.543.1","date
297905a87c8a (head) (mergepoint)
```

Three things to read, in order: **200** means it is serving; the version in
`current_version` must be the new one; and `alembic current` must end in
`(head)` — that is the database confirming its migrations finished.

Give it about 15 seconds first. Health can be 000 or 502 for a few seconds while
the container boots, and on an empty database the workers may restart once
before settling.

</details>

Then hard-refresh the browser (**Cmd/Ctrl + Shift + R**) — an open tab keeps
running the old bundle until it reloads.

**8 — If it went wrong**, go back to the image you tagged in step 3. Point the
tag the container runs at the saved image, and start it again:

```bash
docker images cityagentinsights | grep pre-        # find your rollback tag
docker tag cityagentinsights:pre-0.0.543.1 "$IMG"  # use YOUR version here
docker compose $COMPOSE up -d app
docker exec dash-app cat /app/VERSION              # confirm you are back
```

★ **If the release you are undoing added a database migration, that is not enough
— the old container will not start.** It runs `alembic upgrade head` on boot, the
database records a revision the old image has no file for, and it exits rather
than guess:

```
ERROR [alembic.util.messaging] Can't locate revision identified by 'instrdir01'
Migration failed after 3 attempts. Exiting.
```

Nothing in that message says "you rolled back"; it reads like a broken database.
Fix it by pointing the version marker back at the last revision the old image
knows. New tables are additive, so the old code just ignores them and your data
stays:

```bash
# ask the OLD image what it knows — do not guess the revision
docker run --rm --entrypoint sh cityagentinsights:pre-0.0.543.1 \
  -c 'cd /app/backend && alembic heads'

source .env
docker exec dash-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "update alembic_version set version_num='<what that printed>'"

docker compose $COMPOSE up -d --force-recreate app
```

Rehearsed on a copy of a real database: healthy in ~15 seconds, zero tracebacks,
every row still there.

Restoring the dump is the *other* option, and only if the data itself is what
broke — it throws away everything created since the backup:

```bash
docker cp ~/cityagent-backups/pre-upgrade-20260801-1540.dump dash-postgres:/tmp/r.dump
source .env
docker exec dash-postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --clean --if-exists /tmp/r.dump
```

★ That restore **overwrites the current database**. Everything created since the
dump was taken is gone. Only do it if the upgrade actually broke the data —
rolling the image back on its own is enough for most failures.

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

**BI tools:** Tableau, Power BI, **Power BI (User Sign-in)**, **Power BI (Multi-Tenant Sign-in)**, Power BI Report Server, Power BI (.pbix file), Qlik Sense, Qlik QVD, Sisense, Oracle BI, Infor OLAP, Microsoft Analysis Services, **SAP BusinessObjects, SAP BW/BW4HANA, SAP HANA, SAP Datasphere**

> **Power BI sign-in modes:** *User Sign-in* lets each member authenticate with their own Microsoft account (email/password or MFA device code) — no shared service principal. *Multi-Tenant Sign-in* auto-discovers every tenant a user can reach from one consent and merges their workspaces.

**Business apps:** NetSuite, **Salesforce**, **Priority ERP**, ServiceNow, monday.com, SharePoint Lists, AWS Cost Explorer, PostHog, Outlook Mail

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
