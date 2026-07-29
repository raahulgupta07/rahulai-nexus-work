# CityAgent Insights — whitelabel fork

Whitelabel fork of a third-party analytics codebase (upstream baseline `v0.0.476`) rebranded to **CityAgent Insights**. Same codebase family as CityAgent Analytics "Dash", but this tree is built from the upstream baseline directly rather than from Dash. Upstream version numbers below (`v0.0.4xx`) refer to that baseline.

## Tests — which command, when

```bash
# inner loop: fork-owned pure-logic tests, no DB, no app boot
docker exec -w /app/backend dash-app python -m pytest -q tests/unit/fork         # 783 tests, ~7s

# before a push / after an upstream port: the whole thing
docker exec -w /app/backend dash-app python -m pytest -q tests/unit              # ~2550 tests, ~1h

# testing a tree that is NOT the running container (a port, a merge, a backup):
docker run --rm -v "$PWD/bagofwords:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc cityagentinsights:local \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest <paths> -q -p no:cacheprovider'
```
★★**The `uid=999,gid=999` on those tmpfs mounts is load-bearing.** A tmpfs mounts `root:root 0755`; the container runs as `uid=999(app)`, so without it pytest cannot create its sqlite template and EVERY test errors at setup — 139 of them, looking exactly like the code is broken. `PYTHONPYCACHEPREFIX` is the same class of problem for `compileall` against a read-only mount.
★When a suite fails on a ported tree, **run the same files on the pre-port tree first**. The baseline is what tells you whether the port did it.

★`backend/tests/unit/fork/conftest.py` overrides the parent's function-scoped autouse `run_migrations` with a no-op. The parent rebuilds the sqlite schema **per test** (engine dispose + `gc.collect()` + `sleep(0.1)` + file copy, twice) ≈ 0.9s of fixed cost on every test regardless of whether it opens a connection. Measured on the same 236 tests: **210.06s → 2.24s (94×)**.
★**Never put a schema-needing test in `tests/unit/fork/`** — it fails "no such table", which reads as a product bug. Split by COST, not by feature.
★A fresh image has **no pytest**: `docker exec dash-app pip install -q pytest pytest-asyncio` after every bake.
★The containers were renamed to `dash-app` / `dash-postgres` in 0.0.490.9; the commands above said `bow-app-cai` for three releases after that and simply failed with "No such container".

## Run (own local image — NOT the Hub image)

The prebuilt upstream Docker Hub image has ZERO whitelabel. Always build from source.

```bash
cd /Users/rahulgupta/Desktop/CityAI-Final-Project/CityAgentWork/bagofwords
docker tag cityagentinsights:local cityagentinsights:pre-$(cat VERSION)      # ★DO THIS FIRST — see below
docker compose -p cityagentinsights -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app                                   # bakes source → cityagentinsights:local
docker compose -p cityagentinsights -f docker-compose.dev.yaml up -d app      # recreate container
```

★★★**Tag the image before every rebuild.** Building over the `cityagentinsights:local` tag orphans the previous manifest and containerd deletes it — two working rollback images (`132c6f610ae0`, `d11d22bfb799`) were lost this way on 2026-07-25. A tag is the only thing that keeps an old image alive.

★Always verify content **inside the new image** before swapping the container, not in the running one:
```bash
docker run --rm --entrypoint sh cityagentinsights:local -c 'cat /app/VERSION; sed -n 3p /app/CHANGELOG.md; grep -c <marker> /app/backend/<file>'
```

- **URL**: http://localhost:8095  (APP_PORT=8095 → container 3000)
- **Project name**: `cityagentinsights` (must pass `-p`; avoids name clash with a stale prior `bow-app-dev` on :3011)
- **Containers**: `dash-app` (8095→3000), `dash-postgres` (5440→5432) — renamed from `bow-*-cai` in 0.0.490.9
- **Image**: `cityagentinsights:local` (compose `app.image` + `build:` replaced the upstream prebuilt Hub image reference)
- **.env**: `APP_PORT=8095`, `POSTGRES_PORT=5440`, persisted `BOW_ENCRYPTION_KEY` (survives restarts, no forced logout)
- Postgres creds: user `bow` / password in `.env` (`POSTGRES_PASSWORD`), db `bagofwords`
- App cwd = `/app/backend` (start.sh `cd /app/backend` then `uvicorn main:app`); frontend served static from `/app/frontend/dist`

## Admin / auth

- **No seeded admin.** First signup bootstraps the org + becomes owner/admin (`backend/app/core/auth.py:612,757`).
- ★**Live admin is `raahulgupta07@gmail.com`, org `5b59c42c-57a7-4a27-90cf-94ef3e3f39fc`** (the DB was reset and re-signed-up). The older `admin@cityagent.io` / org `9bf37931-9f62-4288-9e4b-1ef238571861` are **STALE** — they appear in earlier session notes below; don't trust them.
- ★Password unknown to tooling → for API/E2E work, mint a JWT inside the container instead: copy a script into **`/app/backend/`** (not `/tmp`, or `import main` fails), then `import main` to register the ORM registry and `await get_jwt_strategy().write_token(user)`. Browser/Playwright: set cookie **`auth.token`** (not `auth_token`) and target `http://localhost:3000` from inside the container.
- ★Postgres for this stack is **`-U bow -d bagofwords`** on :5440 (`dash/dash` is the *Analytics* stack — different project).
- fastapi-users, prefix `/api`: register `POST /api/auth/register`, login `POST /api/auth/jwt/login` (form: username/password).
- **Most API calls need header `X-Organization-Id: <org>`** — omitting it 400s `organization.required`.

## Whitelabel changes made (user-facing only)

- Upstream product name (spaced and lowercase-spaced forms) → `CityAgent Insights` across 56 files (frontend .vue, `locales/*.json`, backend `email_strings.py` / `email_send_service.py` / `email/message_builder.py`). Kept `ee/LICENSE` (legal).
- Browser title: `nuxt.config.ts` `app.head.title` + `titleTemplate`. Static tool-window titles in `frontend/public/*.html` (`BOW Visualization`/`BOW Artifact`).
- **Logos** (dark-bg circular "C" mark): `media/logo-128.png`, `frontend/public/assets/{logo.png,logo-128.png}`, `frontend/public/favicon.ico`. Baked into source AND hot-swappable via `docker cp … dash-app:/app/frontend/dist/assets/…` (static, no rebuild).
- **Chat bubble (Intercom) removed**: guard fixed `if (environment==='production' && intercom?.enabled)` in `layouts/default.vue` + `layouts/users.vue` (was `&& intercom` — the object is always truthy, a latent bug) + `bow-config.yaml intercom.enabled: false`.
- **Documentation + GitHub nav links removed** from `layouts/default.vue` resources menu + `pages/index.vue` bottom nav. Credit badges ("Made with"/"Powered by") text rebranded + href → `#`. (3 enterprise-settings Documentation links in identity-provider/audit/license.vue left as-is.)

**Kept intentionally** (deploy-critical internals): `BOW_*` env vars, `bow-config.yaml`, container base names `bow-*`, POSTGRES_USER `bow` + DB name, `x-bow-*` headers, the pyproject package name, and the MCP server key — all inherited identifiers that other systems match on by string. Renaming any of them is a coordinated change, not a find-and-replace.

## PPTX / slides fixes

- AI-generated deck code (`generate_slides`) hallucinates python-pptx APIs. Root killers seen:
  - `chart.plot_area` / `chart.chart_area` — **do not exist** on python-pptx `Chart` → `AttributeError`.
  - Unguarded division `x / period_total` → `ZeroDivisionError` when bound data aggregates to 0 (e.g. a CRM deck bound to a mismatched DuckDB source with no CRM columns).
- **Guard added**: `sanitize_pptx_code()` in `backend/app/ai/code_execution/pptx_executor.py` neutralizes any `.plot_area`/`.chart_area` statement line to `pass` before exec. Applied at generation time → fixes FUTURE decks only (can't retro-fix already-`failed` ones).
- Failed decks: `status=failed`, `pptx_path` NULL, no previews → export `GET /api/artifacts/{id}/export/pptx` returns 400 ("Slides generation failed"). To manually repair a deck whose code is self-contained: re-exec sanitized code → write pptx to `/app/backend/uploads/pptx/<id>.pptx` → `UPDATE artifacts SET status='completed', pptx_path=…`.
- **Failed-state UI**: `frontend/components/dashboard/ArtifactFrame.vue` — `isFailedArtifact` computed shows a clean "Slides generation failed" panel instead of dumping raw `generate_slides` source; hides the code iframe + Polish button when failed.

## Enterprise unlocked + phone-home removed (self-owned product)

- **All EE features ON, no license needed.** `backend/app/ee/license.py` `get_license_info()` was rewritten to unconditionally return a permanent enterprise grant (`licensed=true`, `tier=enterprise`, all 13 `TIER_FEATURES["enterprise"]`, `max_users=-1`, `max_agents=-1`). No external license server, no signed key, no expiry. Every gate reads from it → `has_feature()`, `is_datasource_allowed()` (unlocks powerbi/qvd/sybase/tableau/zabbix/splunk), `get_max_*()`, `require_enterprise()` all pass. Frontend `useEnterprise.ts` reads `/api/license` → EE UI (audit/scim/ldap/custom-roles/cost-dashboard/pii) auto-unlocks, no FE edit.
- **Telemetry (PostHog phone-home) killed.** `backend/app/core/telemetry.py` default `BOW_POSTHOG_KEY` (was an inherited hardcoded upstream-cloud key `phc_aWBV…`) now `""` → client never initializes, all `capture()` no-op. `bow-config.yaml telemetry.enabled: false`. Verified live: `_posthog is None`, `_enabled()==False`.
- **External upstream links neutralized** → `#` / local placeholder: settings docs (identity-provider/audit/license.vue), UpgradeBanner pricing, sign-up terms/privacy, excel Office-manifest SupportUrl/LearnMore, scim `documentationUri`. The remaining upstream-domain strings are Swagger docstring curl examples only (no real call). `license.py` still compares an issuer against an upstream domain, but that path is unreachable (validator no longer runs).
- Verified live after rebuild: `GET /api/license` → enterprise + 13 features; powerbi/tableau/splunk allowed; scim/ldap/custom_roles/pii = True; max_agents/users = -1.

## LDAP directory sync — UI-configurable (per-org, DB-stored)

Was file-only (`bow-config.yaml` → `settings.bow_config.ldap`, restart to change). Now editable from **Settings → Identity Provider → LDAP Directory Sync**, per-organization, hot (no restart). Mirrors the SMTP secret pattern.

- **Storage**: `OrganizationSettings.config.ldap` (JSON, no migration — reused the existing per-org config blob like `pii_protection`/`entra_profile_sync`). Bind password Fernet-encrypted as `bind_password_enc` via `app.services.email.secrets.encrypt_secret`/`decrypt_secret` (same `BOW_ENCRYPTION_KEY`). **Never** returned to the client — read shape exposes only `bind_password_set: bool`. Config column is `json` (not jsonb) — cast for `jsonb_set`.
- **Schema**: `OrgLdapSchema` (read, redacted) + `OrgLdapUpdate` (write, optional `bind_password`) in `organization_settings_schema.py`.
- **Service** (`organization_settings_service.py`): `get_ldap` / `update_ldap` / `resolve_ldap_config`. `resolve_ldap_config(db, org)` returns a runtime `bow_config.LDAPConfig` from the org's DB block (decrypting the password), **falling back to `bow-config.yaml`** when the org has no saved block (`source_db` flag tells which). Existing connection/sync services unchanged.
- **Routes** (`ee/ldap/routes.py`): added `GET/PUT /api/enterprise/ldap/config`; rewired `sync` / `sync/preview` / `sync/status` / `test-connection` from the file config to `resolve_ldap_config(db, org)` (per-org). All gated `@require_enterprise("ldap")` + `manage_identity_providers`.
- **Background job** (`ee/ldap/jobs.py`): resolves each org's own config, skips disabled orgs. **Scheduler gate** in `main.py` changed from `bow_config.ldap.enabled` to just `has_feature("ldap")` so DB-only orgs also get periodic sync (file interval = shared tick).
- **Frontend**: `ee/composables/useLdapSync.ts` gained `config`/`fetchConfig`/`saveConfig`; `pages/settings/identity-provider.vue` replaced the "configure in bow-config" notice with the editable form (Connection / Directory tree / Sync sections + Save + Save & Test). Test runs against saved config → the form auto-saves before testing. i18n keys added to `locales/en.json` `settings.identityProvider.ldap*`.
- **UX**: password field write-only (blank = keep saved, placeholder shows "saved"); `source_db=false` shows a "values from bow-config.yaml — save to override" hint. FE is a static SPA → **requires image rebuild** to ship (backend/rust stages cached, only the frontend stage re-runs).
- Verified E2E: PUT encrypts + persists, GET redacts, DB stores ciphertext only (`gAAAAA…`), `resolve_ldap_config` decrypts round-trip, survives rebuild.

## SSO providers — UI-managed (Keycloak / OIDC / Google / Entra), instance-global

Built via 4 parallel sub-agents mirroring the LDAP build. Manage login SSO from **Settings → Identity Provider → Single Sign-On (SSO)** (top card). No new SSO *type* was needed — Keycloak is generic OIDC, and the full authorize/callback/PKCE/discovery/group-sync flow already existed (`routes/auth.py` + `services/auth_providers.py`); it was just file-only + empty.

- **★Instance-GLOBAL, not per-org** — SSO login happens before any org is selected, so providers can't live in `OrganizationSettings` (per-org) like LDAP. New singleton table **`instance_settings`** (`models/instance_settings.py`, JSON `config`, `async get_or_create(db)`) + Alembic migration. Config shape: `{auth_mode, google:{enabled,client_id,client_secret_enc}, oidc_providers:[{name,enabled,issuer,client_id,client_secret_enc,scopes,label,icon,pkce,discovery,uid_claim,sync_groups,group_claim,resolve_group_names}]}`.
- **Secrets** Fernet-encrypted as `client_secret_enc` (google + each provider) via `app.services.email.secrets`; **never returned** — read schema exposes `client_secret_set: bool`.
- **Schemas** (`organization_settings_schema.py`): `SsoConfigSchema`/`SsoConfigUpdate` + `SsoProviderRead/Update` + `SsoGoogleRead/Update`.
- **Service** `services/sso_config_service.py` `SsoConfigService`: `get_config` (DB or file fallback, `source_db` flag), `update_config` (encrypt/keep-secret, validate name slug `^[a-z0-9_-]+$` + unique + issuer-when-enabled), `resolve_oidc_providers`/`resolve_google`/`resolve_auth_mode` (DB→runtime `bow_config.OIDCProvider`/`GoogleOAuth`, decrypt, file fallback).
- **Routes** `routes/sso_config.py`: `GET/PUT /api/enterprise/sso/config` (perm `manage_identity_providers`, NOT enterprise-gated — SSO is core auth). Mounted in `main.py`.
- **Login flow rewired** (`auth_providers.py`): added async `_resolve_oidc_config` / `_resolve_google_config` (open `async_session_maker`, resolve from DB, file fallback); swapped the 4 login call sites in `build_authorize_url` + `_handle_callback`. **Public `/api/settings`** (`bow_settings.py`, unauthenticated) now resolves google/auth_mode/oidc_providers from DB → the login page (`pages/users/sign-in.vue`) renders a button per provider automatically (added `label` to the feed). **No restart** to add/change providers.
- **Frontend**: `ee/composables/useSsoProviders.ts` (fetch/save) + SSO card on `identity-provider.vue` (auth-mode radios, provider list + Google row, add-provider dropdown, inline edit form with write-only secret, PKCE/discovery/group-claim). 34 `sso*` i18n keys in `locales/en.json`.
- **★Known edges** (both minor, provider *management* is fully DB-driven): (1) `main.py` mounts the `/api/auth/{provider}` routes based on the **file** `auth_mode` at startup (default `hybrid` → always mounted), so a DB-only `auth_mode: sso_only` is honored by the login-page UI + settings but doesn't itself unmount local routes. (2) `_is_entra_provider` (post-login Entra *profile* sync detection) still reads file config — a DB-only Entra provider gets login but not profile-field sync until also in bow-config.
- Verified E2E: PUT encrypts + persists to `instance_settings`, GET redacts, DB stores ciphertext only (`gAAAAA…`), `resolve_oidc_providers` decrypts round-trip, public `/api/settings` serves the provider to the login page, SSO card baked into dist. Test provider reset to empty.

## People & Identities — merged-identity view (read-only)

New **Settings → People & Identities** page (`/settings/people`). Surfaces the app's existing email-based account merging: one `users` row per email; local password, every linked SSO/OAuth account (`oauth_accounts`, added in `UserManager.oauth_callback` when email matches — `core/auth.py:503`), and LDAP/directory group memberships all unify on `lower(email)`. This screen just *shows/searches* it — the merging already worked.

- **Backend** (read-only, no migration): `backend/app/schemas/people_schema.py` (`PersonView`/`IdentityView`/`PersonGroupView`) + `backend/app/routes/people.py` → `GET /api/organizations/{org}/people` (`@requires_permission('view_members')`), mounted in main.py. Joins `Membership` (role/is_owner) + `User` (has_password) + `OAuthAccount` (per-provider identity, `is_primary`) + `GroupMembership`→`Group` (name + `external_provider` as source). Batched queries, no N+1.
- **Frontend**: `pages/settings/people.vue` (search + expandable per-person cards showing each linked identity + group memberships), `composables/usePeople.ts` (uses `useOrganization()` for org id like `members.vue`), nav tab in `layouts/settings.vue` (`{name:'people', requiredPermission:'view_members'}`), i18n `settings.peopleTab`.
- Built by 2 parallel sub-agents. Verified live: endpoint returns admin with `local` identity (has_password), page baked into dist, tab in nav. (Only the admin user exists so far → one card; populates as real users sign in via SSO/LDAP.)
- Reference mockup: scratchpad `identity-view.html`.

## Member file-agents + folder-agent (upload files → one agent auto-sorts)

Normal members can build **private** agents from their own uploaded files — no database, no server paths. Push a mix of files → each auto-sorts into **Tables** (CSV/Excel), **Instructions** (definitions), or **Knowledge** (Word/PDF).

- **Permission** `create_file_data_source` — added to `backend/app/core/permissions_registry.py` (member baseline + `PERMISSION_CATEGORIES["Data & Connections"]`). **Existing orgs need a DB reseed or members get 403**: `docker exec -i bow-postgres-cai psql -U bow -d bagofwords -c "UPDATE roles SET permissions=(permissions::jsonb||'[\"create_file_data_source\"]'::jsonb)::json WHERE lower(name)='member' AND NOT (permissions::jsonb ? 'create_file_data_source');"` (config/perms columns are `json`, not jsonb → cast; heredoc needs `docker exec -i`).
- **Create gate** — `POST /data_sources` (`routes/data_source.py`): decorator removed; in-handler `resolve_permissions()` → admin (`full_admin_access`/`create_data_source`) may create any type; a `file_only` member (only `create_file_data_source`) is **restricted in `data_source_service.create_data_source(..., file_only=True)`** to `type=csv`, with `config.file_paths` forced empty (blocks arbitrary server-path reads — an escalation), `is_public=False`, and no linking existing connections. Private-by-default was already built (`DataSource.is_public` default False + `owner_user_id` + creator gets a `manage` `DataSourceMembership`).
- **Upload → queryable tables** (`services/file_service.py`, `upload_file`): CSV agents read `config.file_paths` (server paths) via DuckDB `read_csv_auto` — uploaded files were NOT auto-tabled. The hook now reflects the **server-generated** managed path (`os.path.abspath(uploads/files/{uuid}_name)`) into the connection's `file_paths`, then calls `llm_sync` to build the table. Member never supplies a path.
- **Excel** — `services/excel_ingest.py` `xlsx_to_csvs(path, out_dir)`: each non-empty sheet → a managed CSV → reflected into `file_paths` (1 sheet = 1 table). Empty/bad sheets skipped.
- **Definitions** — `services/def_ingest.py` `is_definitions_file(name)` (definition/glossary/dictionary/meaning/logic/rules/q&a) + `xlsx_to_definitions_block()` → ONE `data_modeling` Instruction scoped to the agent (via `InstructionService().create_instruction`), and the file is **not** also made a table.
- **Docs → knowledge** — already works: attached docx/pdf/pptx become `agent_v2` `analysis_files` → `read_file` → `_document_text.extract_document_text` (docx extraction leaks some `<w:t>` tags; `sanitize_extracted_text` can clean).
- Routing is **deterministic** (extension + filename), not LLM.
- **3 upload UI entry points** (all FE static → rebuild): (1) `pages/agents/new/index.vue` — two-card toggle (Connect / Upload), honors `?mode=upload`; (2) `components/AddConnectionModal.vue` — "Upload files" banner → `navigateTo('/agents/new?mode=upload')`; (3) `components/NewAgentWizardModal.vue` — **inline** upload mode (the "+ New → Agent" modal), `createFromUpload()` → sets `dsId` + `step='schema'` → flows into Select Tables → Set Context. These are 3 distinct components.
- Verified live as member `analyst@cityagent.io` (password in `.env`, not committed): csv + multi-sheet xlsx → tables, `Definitions.xlsx` → data-dictionary instruction (no junk table), `/etc/passwd` path stripped, `is_public:true` forced false, Postgres create → 403.
- **Known / TODO**: table names carry a `{uuid}_sheet` prefix (collision-safe, ugly); schema sync is async (`/full_schema` lags after upload — `GET /data_sources/{id}/refresh_schema` forces it); an LLM classifier for genuinely ambiguous files is a future enhancement.

## Gotchas (this environment)

- **rtk hook mangles plain `cp`/`ls`** in Bash → use absolute `/bin/cp`, `/bin/ls`.
- **Pasted screenshots die** in `/var/folders/.../TemporaryItems` before Bash can copy them. Reliable source = Claude Code cache: `~/.claude/image-cache/<session-id>/<N>.png` (N = Image #N).
- `docker compose build` via plain `run_in_background` captured NO output — run it with `> build.log 2>&1 &` + a Monitor on the log for `Image cityagentinsights:local Built`.
- Volumes persist across container recreate: `postgres_data_dev` (DB), `uploads_data_dev` (`/app/backend/uploads`, incl. generated pptx). So DB + generated files survive rebuilds.
- Uncommitted: all whitelabel edits are working-tree only (no git commit/tag yet).

## Session 2026-07-21 — file/agent ingestion work

- **Primary-instruction auto-fill** — agent `primary_instruction_id` is a nullable pointer (NOT a category); only 3 writers in `data_source_service.py` (create-copy, update PATCH = the manual button, fork). Wizard sets it on Finish from the onboarding overview draft. Fixes in `llm_sync`: `await refresh_data_source_schema` BEFORE the LLM generators (file-agent schema syncs async → empty overview → no primary); `_maybe_promote_fallback_primary` promotes an existing published `always`/`data_modeling` instruction when overview is empty AND primary is NULL — **runs even when `use_llm_sync=False`** (moved before the guard; model default is False, wizard toggle default ON); `generate_datasource_instruction(extra_context)` folds in existing instructions (the uploaded Data dictionary). FE retries `llm_sync` once.
- **★★csv_client digit-leading table-name crash (FIXED)** — uploads are `{uuid}_name.csv`; a digit-leading uuid made `CREATE VIEW 22b02300_...` an invalid unquoted identifier → `csv.connect.error Parser Error: syntax error at or near "22"` → the whole CSV connection died → agent had 0 tables → fell back to clarify. `csv_client._safe_table_name` now prefixes `t_` for non-letter-leading names + double-quotes the `CREATE VIEW`. Re-sync via `GET /data_sources/{id}/refresh_schema`. Note: `connection_tables` hold csv names; `datasource_tables.is_active` gates what the agent sees (raw-API create leaves is_active=false → 0 tables; the wizard "Select Tables" step activates them).
- **table_backing dedup** — a CSV/xlsx reflected into a table is marked `File.source_kind="table_backing"` (col already existed, no migration); `File.is_agent_readable` excludes them from `analysis_files` (`agent_v2`) + `FilesContextBuilder` so the agent queries the table, not the raw file. `source_kind` added to `FileSchema`; "In table" badge in `AgentFilesPanel.vue`.
- **Smart Excel parser behind `SMART_EXCEL_INGEST` (default OFF, auto-fallback)** — `services/excel_structizer.py` (L0 openpyxl grid+merges; L1 unmerge / split tables / detect header / drop title+totals+`_SD_`+`Unnamed`) + `services/llm_structizer.py` (L2 spec+apply, offline mock; `call_llm` optional OpenRouter). `services/excel_convert.py` `convert_xlsx()` = flag OFF → legacy `xlsx_to_csvs` byte-identical, ON → parse to friendly `{name}_{6hex}.csv` per table, **any failure/empty → falls back to legacy, never raises**. `file_service` `_looks_xlsx` = one-line swap to `convert_xlsx`. Flag in `config.py` (env `SMART_EXCEL_INGEST`) + `docker-compose.dev.yaml` passes `${SMART_EXCEL_INGEST:-false}`. **L2 real-LLM NOT wired yet** (mock only) → very messy sheets still rough. Standalone parser + harness live in a scratchpad, proven end-to-end (worst-case sheet → 3 clean tables, agent answered correctly).
- **Chat direct upload** — `FileUploadComponent.vue`: hidden `<input type=file>` moved out of the modal; paperclip now `@click="$refs.fileInput.click()"` opens the OS picker directly. Modal kept as fallback (`open()`); drag-drop already existed in `PromptBoxV2`.
- **★Pre-existing (NOT regressions): chat-uploaded file is NOT focused** — `report_service.py` binds the report's data sources and **snapshots every `ds.files` into `report.files`**, so the agent reads ALL bound sources + inherited files; a chat upload is just one more item, never the scoped focus. Scoping a turn to the just-uploaded files is UNBUILT. Also: global instruction leak (a `*_OVERVIEW`/Chinook instruction bleeds into unrelated agents).

## Connector setup-docs — Phase 1 (backend, BAKED)

Single source of truth for connector "How to get each value" help + real .docx download.
- **`backend/app/data_sources/connector_docs.py`** (NEW): `CONNECTOR_DOCS` (15 curated ports of the FE `connectorDocs.ts`) + `ALIASES` + `OFFICIAL_DOCS`. `build_connector_docs(dtype, config_fields, credentials_fields, credentials_by_auth, meta)` ALWAYS returns a complete block for ANY connector — curated wins per-field, else `_generic_hint(name,title,desc)` name/title-derived fallback. Shape: `{type,title,overview,curated,whereToGet{field:hint},fields[{name,title,required,whereToGet}],authFlow[],notes,officialDocsUrl}`.
- **`render_setup_docx(docs)`** builds a real .docx via **stdlib `zipfile`** (Content_Types + _rels/.rels + word/document.xml, WordML paragraphs + 3-col fillable table). **NO python-docx** — image uses `uv sync --frozen` so an unlocked pyproject add is silently ignored (verified: docx never installed). Dependency-free = no lock/build friction.
- **Service** `data_source_service.get_data_source_fields` now attaches `docs` (try/except → None). New `build_setup_docx(...)` → (filename, bytes).
- **Routes** `GET /data_sources/{type}/setup-doc.docx` (attachment, wordprocessing mime). `/fields` now carries `docs`.
- Verified live BAKED: powerbi `/fields` docs curated=True 6 fields; onedrive curated=False 5 fields (every connector covered); docx http200 real "Microsoft Word 2007+", zip-valid, has table + tenant hint.
- Backups: `.bak-connectordocs-20260722-094654/` (data_source.py route, connectorDocs.ts, connectorWorksheet.ts, AddConnectionModal.vue).
- **NEXT Phase 2 (FE)**: right panel reads `docs` from `/fields` (inline render — proven safe, NO separate component / NO computed `:ui`) → panel on ALL connectors; "Download setup worksheet" button → hit `/setup-doc.docx` instead of client-side HTML (`connectorWorksheet.ts`).

## Connector setup-docs — Phase 2 (frontend, BAKED)
`frontend/components/AddConnectionModal.vue` (backup `frontend/.bak-phase2-20260722-101322/`):
- Help panel ("How to get each value") now reads backend `docs` from
  `GET /data_sources/{type}/fields?auth_policy=all` → `helpDocApi` ref, fetched on
  every `selectedDataSource.type` change → panel shows for ALL connectors
  (curated + generic). Local `getConnectorDoc()` kept as offline fallback. Same
  shape (notes/whereToGet/authFlow) → panel TEMPLATE UNCHANGED (inline, no
  separate component, no computed :ui — blank-screen risk stays out).
- "Download setup worksheet" button → real .docx via
  `GET /data_sources/{type}/setup-doc.docx` (blob through useMyFetch, keeps
  auth+org headers). Falls back to client-side HTML worksheet on failure.
- FE served static: `yarn generate` → `/app/frontend/dist`, served by FastAPI.
  Persist = full image rebuild `docker compose -p cityagentinsights -f
  docker-compose.dev.yaml build app` then `up -d app`. node_modules NOT present
  locally → cannot `yarn generate` on host; rebuild builds FE inside image.
- Verified baked: /fields docs powerbi(curated 6)/onedrive(generic 5)/mongodb/
  clickhouse all Y; setup-doc.docx http200 real "Microsoft Word 2007+" zip+table
  for curated AND non-curated; bundle contains setup-doc.docx+auth_policy=all;
  index http200, no runtime errors. NOTE: app health path = /health (NOT
  /api/health, which 404s).

## Fast bake — Dockerfile FE stage (BAKED)
Backup: `Dockerfile.bak-fastbake-*`. Problem: FE stage copied ALL source
BEFORE `yarn install`, so any code edit busted the install layer → full
~1-2min reinstall every bake. Fix:
- `# syntax=docker/dockerfile:1` at top (enables BuildKit cache mounts).
- Split: `COPY frontend/package.json frontend/yarn.lock` → `yarn install
  --frozen-lockfile --ignore-scripts` (deps layer, cached unless deps change;
  --ignore-scripts skips `nuxt prepare` postinstall — generate prepares later)
  → THEN `COPY ./frontend` (node_modules is .dockerignore'd, won't clobber).
- `yarn generate` gets cache mounts: node_modules/.cache + .nuxt/cache →
  incremental compile.
RESULT: source-only bake 264s→117s. install now CACHED on code edits; remaining
~2min is irreducible `yarn generate` SPA compile. If build looks stale:
`docker builder prune`. Build/bake unchanged in workflow:
`docker compose -p cityagentinsights -f docker-compose.dev.yaml build app`.
NOTE: Fast dev-server path (yarn dev HMR :3010, NUXT_DEV_PROXY_TARGET) also set
up but user chose Docker-only; nuxt.config proxy targets now env-driven
(default unchanged). Backup `frontend/.bak-fastdev-*`.

## Improve Overview — split fat primary instruction into dict + skills (flag `INSTRUCTION_IMPROVE`, default OFF)

Manual **file agents only** (`type=csv`). Takes an overweight primary-instruction
"overview" (e.g. from an uploaded Definitions.xlsx) and splits it into: a lean
always-loaded **dictionary** + on-demand `kind='skill'` instructions + attached
table references. Additive, dormant behind flag — DB connectors / setup / worksheet
untouched.

- **Flag**: `backend/app/settings/config.py` `instruction_improve` (env `INSTRUCTION_IMPROVE`),
  surfaced via `/api/settings` features → `useAppSettings.ts` `improveOn`. Flip = set
  env + restart (NOT a DB flag).
- **Service** `backend/app/services/instruction_improve_service.py`: `preview` (LLM split,
  retry-on-empty up to 3× when formulas expected, ZERO writes), `apply` (creates skills via
  `InstructionService().create_instruction`, attaches refs, snapshots original into
  `structured_data['improve_backup']` w/ batch_id, guards double-apply), `undo` (restores
  text+refs, soft-deletes created skills). Model = `organization.get_default_llm_model`.
  ★NEVER inject schema + "rewrite as native overview" into the split prompt — regressed the
  LLM into DROPPING the whole formula section (skills 15→0). Keep the focused split prompt.
- **Route** `POST /instructions/{id}/improve?mode=preview|apply|undo`, flag-gated (403 off),
  same per-DS `check_resource_permissions` gate as update_instruction.
- **FE** `KnowledgeExplorer.vue`: ✨ Improve button (shown `improveOn && isFileAgent && !improveApplied`)
  → preview `UModal`; **↩ Undo improve** button (shown when applied). `improveApplied` reads
  `primary_instruction.improved` — a cheap flag added to the primary dict in
  `data_source_service.py` (`bool(structured_data.improve_backup)`), since the dict does NOT
  carry full `structured_data`. FE Undo needs image rebuild to ship.
- **Backups**: `data_source_service.py.bak-undoflag-*`, `KnowledgeExplorer.vue.bak-undobtn-*`
  (+ earlier P0/P1/P2 bak files). **All UNCOMMITTED.**
- **Shadow test stack** `docker-compose.shadow.yaml`: bow-app-shadow :8096 / bow-postgres-shadow
  :5442, flag ON, cloned DB, shared BOW_ENCRYPTION_KEY. Live :8095 never swapped. ★Shadow clone
  had data-source metadata but the CSV connection came over `conn_active=f` (clone artifact) →
  `create_data` fails on shadow → real-number tests must run on LIVE :8095.

### Validation (2026-07-22) — 10/11 numbers exact vs raw CSV ground-truth
Ran on LIVE :8095, CRM Agent (ds `311789a0-0425-4100-a2b3-847d26c3f6b9`), file
`1a54fe21…_MM Conso Data Report (Feb'25).csv` (2,654 rows, 35 cols). Login
`/api/auth/jwt/login` form-encoded; header `X-Organization-Id: 9bf37931-…`; flow
`POST /api/reports` → `POST /api/reports/{id}/completions?background=false`
`{prompt:{content,mode:'chat'}}`. Parallel = one report per Q, concurrent curl.
- Total rows 2,654 ✓ · Successful 1,240 ✓ · Uncontactable 855 ✓ · Unsuccessful 443 ✓ ·
  Completed 1,683 ✓ · top city Yangon 1,136 ✓ · Loyalty+Successful 766 ✓ (agent caught the
  trailing zero-width space `​` in "Loyalty") · Completed→Successful 73.68% ✓ ·
  Female+Successful 771 ✓.
- ★**qcomb FAILED (agent SQL, not the feature)**: "% Retained Users formula + Feb number".
  Skill RETRIEVAL worked — pulled the right formula from dict skill `be5262a3` and stated the
  exact multi-condition criteria. But the SQL didn't honor its own stated filters
  (internally inconsistent): numerator 1,064 = Retention+Completed+Successful (dropped
  Type=User AND Status∈{Existing,Retained}; should be 1,061); denominator 1,434 =
  Retention+Completed (dropped Successful — impossible, only 1,240 successful total).
  Answered 74.20%; per its own criteria correct = 1,061÷1,064 = **99.72%**. → agent
  mis-scopes multi-condition filters; dictionary/skill layer is fine.

## Smart three-way split + file-agent connector-parity (2026-07-22, BAKED, flags default OFF)

Built by 2 parallel sub-agents, baked into `cityagentinsights:local`, deployed. Live :8095 byte-identical (flags OFF). Validated E2E on shadow :8096 (flags ON).

### A. Smart split — Improve Overview now routes by RETRIEVABILITY (flag `INSTRUCTION_IMPROVE`)
`backend/app/services/instruction_improve_service.py` (backup `.bak-smartsplit-20260722-160101`). Rewritten from 2-bucket (dict + skills) to **3-bucket**:
- **dictionary** → always instruction (unchanged).
- **metric_instructions** → FORMULAS grouped by family (2–5 groups, NOT one-per-metric), created `kind='instruction'`, `load_mode='intelligent'`. ★KEY: these are retrievable in DEEP analysis + training; skills are NOT.
- **skills** → ONLY genuine multi-step chat PROCEDURES/SOPs. Most overviews yield 0.
- ★★★WHY: `read_instruction` (the only way to pull a `kind='skill'`) is `allowed_modes=["chat"]`, enforced in `ai/registry.py:184` (dropped from catalog in non-chat) + `ai/runner/tool_runner.py:37` (refused at runtime). Deep analysis = `mode="deep"` → skills VISIBLE but UNPULLABLE. So the old "every formula → skill" made formulas dead in deep. Formulas MUST be intelligent instructions.
- Quality linter: rate/% body must state numerator AND denominator; if source lacks one → per-item `warnings[]` (never invents).
- New JSON shape: `{dictionary_text, metric_instructions:[{title,description,body,warnings}], skills:[...]}`. preview/apply/undo all handle it; backward-compatible with old skills-only payloads. `improve_backup` snapshot now stores `created_metric_ids` + `created_skill_ids`; undo sweeps by `ai_source='improve:{batch}'`.
- **Validated on shadow (real CRM overview be5262a3, 6087 chars):** preview → 3 grouped metric instructions (Channel volume / Segment counts / Rate & percentage) + 0 skills + 2 warnings (caught Recruitment Rate + by-Channel missing denominators). apply → 3 rows `kind=instruction`/`intelligent`. undo → removed all 3, restored overview, 0 leftover. Full round-trip PASS.
- **FE TODO**: `KnowledgeExplorer.vue` preview modal still renders only `skills` — needs a pass to show `metric_instructions` + `warnings`. Backend safe if FE sends old shape.

### B. File-agent connector-parity (new flag `CLEAN_FILE_TABLE_NAMES`, default OFF)
Backups tagged `.bak-connparity-20260722-160211`. Makes file (csv/duckdb) agents first-class like DB connectors:
1. **Clean table names** — `csv_client.py` `_safe_table_name`: when `clean_file_table_names` ON, strips leading `{uuid}_` (dashed 8-4-4-4-12 or 32-hex) from the managed-upload basename → `mm_conso_feb` not `t_22b02300_..._mm_conso`. View-name==returned-name preserved; `t_` fallback only if still non-letter-leading; existing `used`-set dedupe kept. Flag OFF = byte-identical. ★Existing file agents need `GET /data_sources/{id}/refresh_schema` to pick up clean names (names cached in connection_tables/datasource_tables).
2. **Grounded @mentions** — `data_source_service.py` llm_sync generated-overview path: parses `@Token`s from the generated overview, resolves each to a `DataSourceTable` (exact / uuid-stripped / bare `schema.table` match), passes `references=[InstructionReferenceCreate(datasource_table,...)]` into the `InstructionCreate` → real clickable `datasource_table` refs. NOT flag-gated (safe no-op when no mentions); benefits connectors too. try/except → falls back to no refs, never breaks agent creation.
3. **Better empty-DataFrame retry msg** — `code_execution.py` "returned None or an empty DataFrame" nudge now appends the actual queryable table names (from `ds_clients` `_table_map`) so the model self-corrects a mistyped table name (the real file-agent failure, vs the client_key it used to blame). No flag, try/except.
- Flag passthrough added to `docker-compose.dev.yaml` + `docker-compose.shadow.yaml` (`CLEAN_FILE_TABLE_NAMES=${...:-false}`).
- Root cause of Image #50 empty-DataFrame: agent mis-copied a 40-char uuid table name → DuckDB found nothing. Clean names (#1) remove the trigger.
- **✅ VALIDATED E2E ON LIVE + SHIPPED (2026-07-22)**: `CLEAN_FILE_TABLE_NAMES=true` now ON on live (persisted in `.env`, backup `.env.bak-cleanflag-20260722`). Re-synced CRM Agent (ds `311789a0`): 6 ugly uuid tables → clean `mm_conso_data_report_feb_25` etc (old rows replaced; ★re-sync recreates them `is_active=false` — the raw-sync landmine — had to `UPDATE datasource_tables SET is_active=true`). **DEEP-mode query UNION'd all 6 clean tables → 21,240 rows (Jan 5291·Feb 2654·Mar 3092·Apr 2447·May 3829·Jun 3927); Feb=2654 matches raw-CSV ground truth; NO empty-DataFrame error, NO ugly name in trace.** Image #50 failure fixed. ★#2 @mention-grounding NOT exercised (only fires on overview REGEN via llm_sync, not refresh_schema) — still unproven, low-risk.

### Shadow relaunch gotcha
`docker-compose.shadow.yaml` postgres defaults `SHADOW_POSTGRES_PORT:-5441` which CLASHES with the stale `bow-postgres-dev` on 5441. Bring shadow up with `SHADOW_POSTGRES_PORT=5442 SHADOW_APP_PORT=8096 CLEAN_FILE_TABLE_NAMES=true docker compose -p cityagentinsights-shadow -f docker-compose.shadow.yaml up -d`.

## FUTURE ROADMAP — query-pattern learning (NOT built)
Today the agent RE-ENGINEERS every cold conversation (plans + writes UNION/join code from scratch → the ~340s LLM latency repeats). Reuse only happens WITHIN one chat thread (`load_step`) or if a run's pattern is manually saved as an instruction via `InstructionTriggerEvaluator` (review-gated, `suggest_instructions/trigger.py:102`). Roadmap idea: auto-capture successful query patterns (e.g. "the 6 monthly `mm_conso_*` tables share a schema → UNION ALL for all-months metrics") as `intelligent` instructions so future runs skip re-planning. Deferred — do AFTER the FE increment below.

## LLM-driven file ingestion + per-file fate badges (2026-07-22, BAKED, flag smart_file_intake)
Turns uploaded files the agent IGNORED into usable context. Flag `smart_file_intake` (env SMART_FILE_INTAKE) — OFF = byte-identical.
- **LLM Librarian** (`file_classifier.classify_file_llm`): the LLM READS the file's extracted text/sheet-preview and DECIDES destination (table|instruction|skill|knowledge) by CONTENT, not filename ("Definitions.xlsx" is no longer special). Deterministic `classify_file` kept only as a fast-path for obvious tables + fallback. Wired in `file_service._smart_file_intake`; all 4 destinations route off the LLM verdict.
- **Knowledge producer** (NEW `app/services/knowledge_ingest.py`): dest=knowledge → extract doc text → chunk (~1000 chars, 150 overlap) → `metadata_resources` rows (resource_type='knowledge', name="{stem} (part N/M)", path=filename, description=chunk, is_active) → retrievable by the `read_resources` tool. (Before: knowledge dest "fell through" = did nothing.)
- **Instruction path**: definitions/dictionary → ONE always-loaded instruction; rule/logic docs → ONE CONSOLIDATED instruction, load_mode='intelligent', ≤12 bullets (was 20 rows all 'always' = bloat). skill-dest bug fixed (kind='skill' not 'instruction').
- **Re-ingest** existing uploads: `POST /api/data_sources/{ds}/files/{file}/reingest` (FileService.reingest_file) — re-runs classify+route on a parked upload.
- **fate field** on FileSchema (table_backing|instruction|knowledge|upload), derived in get_files_by_data_source via back-links: instructions stamped `ai_source="file:{id}"`, knowledge chunks stamped `raw_data.source_file_id`. ★BOTH back-link checks MUST filter `deleted_at IS NULL` (soft-deleted leftovers otherwise win the fate).
- **FE** `AgentFilesPanel.vue`: per-file badge In table / Instruction / Knowledge / Not ingested + hover Re-ingest button (calls the endpoint). Original "In table" chip byte-identical.
- ★docx/pptx XML leak ROOT CAUSE (fixed in `_document_text.py`): regex `<(?:w|a):t[^>]*>` also matched `<w:tbl>`/`<w:tr>`/`<w:tc>`/`<w:tab/>` (table tags) → dragged raw OOXML into text; Q&A docs are Word TABLES → always leaked. Fix: anchor the name `<(?:w|a):t(?:\s[^>]*)?>` + `_strip_residual_ooxml()` on docx/pptx output (NOT pdf).
- **VALIDATED E2E on live** (CRM Agent, 3 real docs): Definitions.xlsx→instruction (clean, always); CRM Q&A.docx + Abbott.pptx→knowledge (21 clean chunks, no XML); fate correct; re-ingest 401=auth-live; FE badge strings baked. Image `cityagentinsights:local` rebuilt + up on :8095 (project cityagentinsights).

## Session 2026-07-23 — login revamp + first-run admin + empty-chat + Power BI connectors

Repo now = **CityAgent Coworker AI** (GitHub `git@github.com:raahulgupta07/cityagent-coworker-ai.git`, earlier push `18d878aa`). Sister app "Dash" (`ca-app`, :3007) = source of ported Power BI code. All edits additive + flag-gated OR isolated new connector types (original `powerbi` byte-identical). Backups tagged `.bak-*-2026072{2,3}`.

### Login page revamp + first-run super-admin
- **`frontend/pages/users/sign-in.vue`** (backups `.bak-loginrevamp-20260722`, `.bak-firstrun-20260722`): full rewrite — two-column layout, greeting, blue `cw-*` theme (matched to :8095, NOT Dash terracotta), embeds `<AuthShowcase/>`. PLUS first-run super-admin: `needsSetup` ref reads public `/api/settings`, `createAdmin()` posts register when org empty, FIRST-RUN badge. No signup link otherwise.
- **`frontend/components/auth/AuthShowcase.vue`** (NEW): animated agent pipeline (connect→understand→query→answer→dashboard→deck→report), scoped styles `:deep()`.
- **`backend/app/routes/bow_settings.py`**: added `needs_setup` to `/api/settings` = `user_count==0` (fail-closed). First user = super admin (OpenWebUI-style). Tested throwaway :8098 — 6/6 pass (needs_setup true on empty → superuser created → flips false → second signup 400).

### Empty-chat fix (no report until first question)
- **`frontend/layouts/default.vue`** + **`frontend/pages/reports/index.vue`** (backups `.bak-emptychat-20260722`): `createNewReport` routes to `/` with NO eager `POST /reports` — report created only when first prompt sent. Deferred-creation, not hidden/soft-delete.
- **`backend/app/services/report_service.py`**: `get_reports` also hides 0-completion placeholder reports (`title NOT IN ('untitled report','New report')` OR has completions) — belt-and-suspenders for legacy empties.

### Files → instructions (agent uses only instructions, raw files hidden)
- **`backend/app/models/file.py`** `is_agent_readable` excludes `instruction_backing`/`knowledge_backing` (gated `smart_file_intake`); **`file_service.py`** stamps `source_kind` on ingest. Builds on the 2026-07-22 LLM-librarian work above.

### ★Power BI (User Sign-in) — new connector `powerbi_user` (per-user email/password + MFA device code)
Copy of Power BI framework, per-user credential. Original `powerbi` UNTOUCHED.
- **NEW** `backend/app/services/powerbi_user_signin.py` (`try_password_signin` ROPC, `discover_user_tenants`, async `mint_access_token`) + `backend/app/routes/powerbi_user_signin.py` (`user-signin/connect`, `device-code/poll`, `select-tenant`). Helpers `powerbi_device_code.py` + `powerbi_tenant_discovery.py` (copied from Dash).
- FOCI public client `1950a258-227b-4e31-a9cf-717495945fc2` → NO app registration needed for ROPC/device-code. AADSTS classifier splits bad-creds vs MFA-required (50076/50079/50158/7000218/65001…) → falls back to device-code flow.
- Registry `powerbi_user` (title "Power BI (User Sign-in)", `user_login` variant). Schemas `PowerBIUserConfig`/`PowerBIUserLoginCredentials`. Per-user creds in `user_data_source_credentials`; `auth_policy=user_required`; overlay `UserDataSourceTable`.
- **FE** `UserDataSourceCredentialsModal.vue` (backup `.bak-pbiuser-20260722`): user_login state machine (form→device→tenants→done).
- **PROVEN**: extracted Dash refresh token → `mint_access_token` → PBI `/groups` 200, 3 workspaces.

### ★Power BI (Multi-Tenant Sign-in) — new connector `powerbi_mt` (auto tenant discovery + workspace merge)
Copy of the OAuth `powerbi` connector + cross-tenant discovery. Original `powerbi` UNTOUCHED.
- **NEW** `backend/app/services/powerbi_multitenant_scan.py`: `discover_tenants_from_refresh` (SYNC, ARM `/tenants`), `redeem_for_tenant` (SYNC, per-tenant PBI token via cross-tenant refresh-token redemption), `scan_all_tenants` (ASYNC, per-tenant scan+merge, tenant-tagged overlay, stores `tenant_tokens` map).
- **`connection_oauth_service.py`**: `if conn_type=="powerbi_mt": tenant_authority = tenant_id or "organizations"` (multi-tenant authority) else requires tenant_id — original path preserved. Scope map adds `powerbi_mt` (PBI scope + `offline_access`).
- **`routes/connection_oauth.py`** callback: guarded `if connection.type=="powerbi_mt":` → best-effort `scan_all_tenants` after existing overlay sync.
- **`data_source_service.py`** (~L2132): query-time routing mints access token per table's tenant when `auth_mode=="user_login"`.
- Registry `powerbi_mt` (title "Power BI (Multi-Tenant Sign-in)", oauth variant schema `PowerBIMultiTenantCredentials`, `client_path` same as powerbi). Config `PowerBIMultiTenantConfig` (workspaces).
- **★Form parity fix**: `PowerBIMultiTenantCredentials` = `tenant_id` (OPTIONAL — blank ⇒ multi-tenant discovery) + `client_id` + `client_secret` (required) + `oauth_client_id` + `oauth_client_secret` (OPTIONAL — the "OAuth Credentials (optional)" section, shown when Require-user-auth ON). Now byte-matches original `powerbi` form EXCEPT tenant_id optional. Verified `/data_sources/powerbi_mt/fields?auth_policy=all` oauth fields = `[tenant_id,client_id,client_secret,oauth_client_id,oauth_client_secret]`, required `[client_id,client_secret]`.
- **PROVEN LIVE cross-tenant**: `discover_tenants_from_refresh` found 2 real tenants (City Holdings + City Mart Holding) → one refresh token redeemed against BOTH → valid PBI tokens → `/groups` 200, 3 workspaces.

### ★User Sign-in form fix — admin sets only config, email/password per-user (BAKED)
**Bug (Image #1):** `powerbi_user` admin form rendered Email/Password under **System Credentials** + Require-user-auth OFF → saved `system_only` → validated a system connection with no creds → "Connection failed". Wrong: a User Sign-in connector has NO shared/system creds; admin sets only Tenant ID; each member enters own email/password at sign-in.
**Root cause:** backend `get_data_source_fields` filtered `credentials_by_auth` by scope correctly (user-scoped variant dropped under `system_only` → `{}`), BUT still returned the default variant's fields in top-level `credentials`. FE `ConnectForm.vue` `credentialFields` falls back to that leaked `credentials` when `credentials_by_auth` is empty → Email/Password shown as System Credentials.
**Fix (backups `.bak-usersignin-20260723-080910`):**
- `data_source_service.py get_data_source_fields`: `any_allowed = any(allowed(m) for variants)`; if **no** variant allowed under the policy → `credentials_fields = []` (pure user-sign-in under system_only shows config only). Safe: powerbi (system default) + powerbi_mt (now system-scoped) keep their fields.
- `data_source_registry.py`: `powerbi_mt` oauth variant `scopes=["user"]` → `["system","user"]` — the multi-tenant Azure **app registration (Client ID/Secret) is a SHARED system credential**, must render in the admin box (mirrors original `powerbi` service_principal). `powerbi_user` stays `["user"]` (pure per-user).
- `ConnectForm.vue`: `isUserSignInOnly` computed (no system auth variant + no system cred fields) → forces `require_user_auth=true`, hides the System Credentials box behind a blue "Per-user sign-in" note, hides Test button + auto-passes the (meaningless) system connection test.
**Verified live** `/fields`: powerbi_user system_only = `config:[default_tenant_id], credentials:[]`; user_required = `email,password`; powerbi_mt system_only = Client ID/Secret + OAuth section; powerbi unchanged.

### Power BI logos on new cards
- **`frontend/components/DataSourceIcon.vue`** (backup `.bak-pbilogo-20260723`): `normalizeType` aliases `powerbi_mt`/`powerbi_user` → `powerbi` (reuse brand logo). Also copied `powerbi.png` → `powerbi_mt.png`/`powerbi_user.png`.

### Gotchas this session
- `mint_access_token`/`scan_all_tenants` = ASYNC; `redeem_for_tenant`/`discover_tenants_from_refresh` = SYNC (smoke tests wire accordingly).
- PBI settings JSON has control chars → `json.loads(..., strict=False)`.
- Sub-agent idle notifications ≠ reports → verified work via `git status` + DB, not by trusting notifications.
- **Still uncommitted** (this session's PBI + login + first-run + empty-chat work) — awaiting explicit push.

## Session 2026-07-23 (part 2) — Fabric federated live, Data Agent redesign, owner sharing, City Mart sample

All ADDITIVE, flag-gated OR isolated; original `powerbi` untouched. Backups `.bak-*-20260723`. All baked into `cityagentinsights:local`, deployed (`docker restart`/`up --force-recreate`, then `docker commit`). Health path `/health`. **Uncommitted.**

### Fabric (User Sign-in) `fabric_user` — federated, VERIFIED LIVE + icon
- Phases 1–5 re-verified end-to-end via API (no UI, password-free using stored/device-code tokens). Config-less connector auto-discovers EVERY Fabric SQL endpoint the signed-in user can reach (cross-tenant: home City Holdings `0f69909c` → data in City Mart Holding `0a8a4f2c`), merges into ONE per-user overlay, routes each query to the right lakehouse via `MsFabricFederatedClient`.
- **E2E proven:** admin creates config-less `fabric_user` connector (typed NOTHING) → analyst signs in via device-code (`https://login.microsoft.com/device`) → **54 tables** across 4 lakehouses (DL_POC 29 / LK_CFC_Sales 15 / DL_POC_Toey 8 / CFC 2) → live query `DL_POC.CMO_SalesDetails` = 5,053,703 rows. Per-user creds+overlay are CONNECTION-scoped (`user_connection_credentials`, `user_connection_tables`); DS↔connection link = `domain_connection`.
- Activation endpoints for uploaded/synced tables: `GET /data_sources/{id}/refresh_schema` (SYNCHRONOUS — populates `full_schema`), `PUT /data_sources/{id}/update_tables_status {activate:[names],deactivate:[]}`, `POST .../bulk_update_tables`. TablesSelector uses `full_schema` + connection_filter.
- **★Fabric icon** (`DataSourceIcon.vue:58`, `.bak-fabricicon-20260723`): `normalizeType` aliases `fabric_user`/`fabric_mt`→`ms_fabric`; copied `ms_fabric.png`→`fabric_user.png`/`fabric_mt.png` (direct-name path works w/o rebuild).
- **★★★Fabric query CRASH FIX** (`data_source_service.py construct_clients`): a config-less `fabric_user` whose user hasn't signed in → federated build returns None → code fell through to generic `MsFabricClient(**{})` which REQUIRES `server_hostname`+`database` → "missing 2 required positional arguments" crash. Fix: for `fabric_user`, if no federated client → `continue` (skip, no client) instead of generic build. **Second bug behind it:** single-connection legacy alias did `next(iter(clients.keys()))` on an EMPTY dict → StopIteration/RuntimeError → guarded `if len(active_connections)==1 and clients:`. (`.bak-fabricskip-20260723`.) Verified: un-signed-in Fabric agent → empty clients no crash (planner clarifies); signed-in → federated client still built.

### Data Agent (upload) — dedicated entry + upload-only redesign (Option C)
Infra already existed (file agents `type=csv`, upload auto-sorts into Tables/Instructions/Knowledge). Surfaced it as first-class. All in `NewAgentWizardModal.vue` + `KnowledgeExplorer.vue` (`.bak-uploadentry-20260723`):
- **"+ New → Data Agent"** menu row → opens the SAME modal with `initialMode='upload'` (new prop; reset sets `sourceMode=props.initialMode||'connect'`). `openNewAgent(mode)` helper in KnowledgeExplorer.
- **Upload-only redesign** (`isUploadOnly = initialMode==='upload'`): hide the Connect/Upload source-picker cards; header "New Data Agent"; **steps relabeled Upload → Review → Set context** (`steps` now a computed); schema-step subtitle upload-native. Real **drag-and-drop dropzone** (`dragOver`/`onDropFiles`/`removeUploadFile`/`uploadInput` ref) + routing hints + per-file remove. Review step REUSES the existing TablesSelector activation (no backend change).
- **★Empty-Review-tables RACE FIX:** `createFromUpload` jumped to the Review step immediately, but CSV ingest/`llm_sync` builds the schema ASYNC and does NOT auto-populate `full_schema` (proven: total=0 even at +3s). Fix: `await GET /data_sources/{id}/refresh_schema` (synchronous) BEFORE `step='schema'`. Now Review opens with tables populated.
- **★"Use LLM to learn agent" toggle** added to upload mode (was connector-only). `createFromUpload` now passes `use_llm_sync: useLlmSync.value`; `loadDraftInstruction` gated on `useLlmSync` (default ON = connect unchanged). **★ROOT:** `POST /data_sources/{id}/llm_sync` SKIPS (`"LLM sync disabled for this data source"`) when the DS was created `use_llm_sync=false`; and the learned overview only sees **ACTIVE** tables → wizard order Upload→Review(activates)→Context(learns) is correct. Verified: overview references `@table` + columns after activation.
- **Connect flow cleaned:** removed the whole source-picker two-card grid from the CONNECTOR modal (connect-only now); removed the "Upload files" banner from **`AddConnectionModal.vue`** (`.bak-noupload-20260723`) — connect picker no longer offers upload (uploads live only in the Data Agent entry).

### ★File-classifier bug (smart_file_intake) — NOT FIXED, pending
E2E-tested a Data Agent with 5 files: sales.csv→**table** ✓, company_overview.pptx→**knowledge** (2 chunks) ✓, glossary.xlsx→**instruction** ✓, policy.docx→instruction (~ok), **inventory.xlsx (Stock+Suppliers dataset)→WRONGLY instruction** ✗ (LLM librarian read the columns as a data-dictionary). Fix TODO: bias CSV/XLSX that parse as wide tabular data (≥3 cols, many rows) → Tables; reserve instruction routing for narrow term/definition or glossary-named files.

### Agent owner badge + per-user filter (admin governance)
- **BE** (`data_source_service.py get_active_data_sources` + `DataSourceListItemSchema`, `.bak-owner-20260723`): batch-resolve owner map, expose `owner_user_id`/`owner_email`/`owner_name`. **Visibility gate UNCHANGED** — `show_all` still admin-gated; owner fields only on rows the caller can already see (verified analyst can't see admin's private agents even forcing `show_all=true`).
- **FE** (`KnowledgeExplorer.vue`): owner badge on each agent row (shown only in "Show all" view via `ownerLabel`); **owner filter dropdown** (`fOwner`/`ownerOptions`/`visibleAgents`) under the AGENTS header. `TreeGroup` (inline `defineComponent` ~L2744) gained an `owner` prop. Hardcoded "All owners"/"Owner: X" strings (no new i18n keys → avoid raw-key render).
- Per-person sharing ALREADY works: Settings→Members→Add member = **use/read access** (membership itself); "Manage *" chips are extra admin rights only. Proven: analyst added with `perms:None` → gains query access. (`DELETE /data_sources/{id}/members/{user_id}` returned 404 in a test though the membership WAS removed — possible bug to verify.)

### ★City Mart Retail sample database — replaces Music/Finance
Samples live in `backend/app/schemas/demo_data_source_schema.py` `DEMO_DATA_SOURCES` (path → `backend/demo-datasources/*.duckdb`); FE `AddConnectionModal.vue` reads `GET /data_sources/demos` LIVE (no rebuild needed) and hides installed ones (`uninstalledDemos`).
- Generated `demo-datasources/citymart_retail.duckdb` (12 MB, deterministic seed 42, `scratchpad/gen_citymart.py`) — **11-table star schema**, Myanmar-real: banners (City Mart/Marketplace/**City Express**=7-11 analog/Ocean/Seasons Bakery), 80 outlets Yangon-heavy, 1000 products, 5000 **City Rewards** members, dim_date 3 yrs with **festivals** (Thingyan=**2.4× daily sales**, Thadingyut, Christmas), MMK currency, channels (In-store 84%/City Mall Online/App/Delivery), promotions, suppliers. Facts: `fact_sales` 374k lines (gross−discount=net, member/non-member `customer_id>0`, points), `fact_inventory` 432k monthly snapshots, `fact_loyalty_txn` 61k. **★★★MUST generate with the CONTAINER's duckdb (1.5.4) — host is 0.9.2, storage format differs.** Ground truth checkable (agent queried banner totals to exact MMK).
- Registered `DEMO_DATA_SOURCES["citymart"]` (starters + schema-teaching instructions); **removed `chinook`+`stocks`** (`.bak-citymart-20260723`). Old chinook.sqlite/stocks.duckdb files still sit unreferenced.
- **★Demo install idempotency fix** (`demo_data_source_service.py`, `.bak-idempotent-20260723`): deleting a demo-installed DS leaves its connection LIVE (delete doesn't cascade the connection); connection names are unique per org (`uq_connections_org_name`) → re-install collided → **silent "duplicate key" fail** (looked like "can't select the sample"). Fix: `_create_demo_data_source` now REUSES an existing connection of the same name (refreshing its config) instead of creating a duplicate. Verified uninstall→reinstall cycles repeatedly.
- **★Connection naming:** demo `connection_name` "Retail DuckDB"→**"City Mart Retail"** so it's findable in the Create-Data-Agent connection picker (searching "city" now matches). The sample installs as a ready-to-use **agent** "City Mart Retail" (DuckDB connection); it does NOT need re-attaching.

## Session 2026-07-23 (part 3) — agent enable/disable toggle, disabled-visibility, samples database-only

- **Agent enable/disable toggle** — ONE concept everywhere = the global `publish_status` (`published`↔`disabled`). No "personal hide" (tried, then removed per user "just disabled"). Same `UToggle size="2xs"` as "Show all", **always visible** (not hover-only), manager-gated.
  - Agents list (`KnowledgeExplorer.vue`): inline `TreeGroup` got `toggleable`/`toggleOn`/`toggleBusy` props + `toggle-switch` emit → `toggleAgentEnabled` PUTs `publish_status` (gated by `canManageAgent`).
  - Picker (`prompt/DataSourceSelector.vue`): same toggle (`canManageDs` = `useCan('manage')`), `isDisabled`/`selectableSources`/`orderedDataSources`; `isAutoMode` now compares **`selectableSources` (enabled only)** so the Auto bolt shows even with disabled rows present; disabled agents are non-selectable/excluded from auto.
  - i18n keys in `locales/en.json` (`disableAgent`/`enableAgent`/`agentDisabled`/`agentEnabled`/`toastSaveFailed`).
- **★★ Disabled-visible-to-manager rule** (`data_source_service.py` `get_active_data_sources`, ~line 1608): `if publish_status == "disabled" and not (show_all_effective or is_gov or str(d.id) in manage_ids): continue` — mirrors the `draft` rule. A manager sees their disabled agents **greyed (Disabled badge + toggle) in both the list and the picker without "Show all"** (fixes "disable everything → list looks empty"). Never shown to non-managers, never selectable, never fed to the AI. Reverted the picker's `show_all=true` (it was over-pulling other users' private agents).
- **`user_hidden_data_sources` table = DORMANT** — the personal-hide FE was removed, but the table + `/data_sources/{id}/hide` (GET/POST/DELETE) endpoints are kept. Model `user_hidden_data_source.py`, migration `ca01hide01ds` (head). **★★ FK CASCADE FIX:** original FKs lacked `ON DELETE CASCADE`, so any ever-hidden agent hit a 500 `ForeignKeyViolation` on delete. Fixed live + in the migration + model (all 3 FKs `ON DELETE CASCADE`).
- **★ Samples are DATABASE-ONLY now** (`demo_data_source_service.py`): installing a sample creates **only the Connection** (the database) + loads its schema via `ConnectionService().refresh_schema` — **no agent, no auto-instructions, no membership**. The user builds the agent themselves in the Create Data Agent wizard by picking the connection. `_create_demo_data_source` returns a `Connection`; `_get_installed_demos` is keyed on the **connection's** `demo_id` marker; install message "Added sample database …". **★★ Resurrect fix:** `uq_connections_org_name` is not filtered by `deleted_at`, so a soft-deleted connection's name still owns the unique slot → reinstall INSERT collided; fix reuses/**resurrects** a soft-deleted connection by name (clears `deleted_at`). Verified: install = 0 agents / 1 connection / 11 tables; reinstall = no duplicate. (The old auto-agent carried 6 teaching instructions — those no longer auto-apply; "Option B" would re-apply them on wizard-create.)
- **★ Duplicate-agent warning** (`NewAgentWizardModal.vue` `createAgentFromExistingConnection`): if a chosen connection already has `agent_count > 0`, warn ("X already has an agent — click Create again to add another"); second click proceeds (`dupConfirmed` ref, reset on selection change). Soft warn, not a block (multiple agents per connection is legal).
- **★ HYBRID_FABRIC_USER flag**: the single-user Fabric connector ("Microsoft Fabric (User Sign-in)", type `fabric_user`) is hidden from the Add-Connection catalog unless `HYBRID_FABRIC_USER=true` (`data_source_registry.py:370` `_entry_visible` — keeps the catalog byte-identical when off). Enabled via `.env` `HYBRID_FABRIC_USER=true` + `up -d --force-recreate app`. It was tested via direct API (which bypasses the catalog gate), so it worked but was invisible in the UI.
- **Deploy reminders**: FE changes need a full image rebuild (`docker compose build app`); backend-only = `docker cp` + `docker restart`. Migrations auto-run (`alembic upgrade head` in `start.sh`). pg creds are `bow` / `bagofwords`. Login `POST /api/auth/jwt/login` (form-encoded); org header `X-Organization-Id`.
- Everything baked into `cityagentinsights:local`, **uncommitted**.

## Session 2026-07-24 — skills (bow-native), Anthropic skill import, real PDF export, multi-agent-per-connection

All additive/isolated. Baked into `cityagentinsights:local`, **uncommitted**. Backups `*.bak-*-2026072{3,4}`.

### How a "skill" runs in bow (verified end-to-end)
- A bow **skill** = an `instruction` row `kind='skill'` (chat-only TEXT, no code). NOT force-injected. Advertised in the `<available_skills>` catalog (short_id + title + description) by `_build_skills_catalog` (`instruction_context_builder.py:558`). Agent pulls full text on demand via the `read_instruction` tool (chat-mode only — enforced in `registry.py`/`tool_runner.py`). Scoped by `data_source_ids`; **empty `data_source_ids` = GLOBAL** (advertised to every agent).
- `description` is what the planner matches on to decide to pull → make it keyword-rich.
- The OTHER meaning: Anthropic library skills = folders (`SKILL.md` + scripts) that RUN CODE to make files. In bow those map to the secure-executor lane (`pptx_executor.py` pattern, invoked by `create_artifact.py`) — python-pptx already wired; docx/xlsx/pdf would be copies of that lane, flag-gated.
- **Proven flow** (harness `scratchpad/skill_test.py` + `skill_pack.json`): `POST /instructions` kind='skill' → chat completion via `POST /reports/{id}/completions?background=false` (report create field is **`data_sources`** [list of DS ids], NOT `data_source_ids`) → read `completion_blocks` for `tool_execution.tool_name=='read_instruction'` (proves the pull) + `loaded_instructions` (force-loaded set). Exec-summary scoped skill pulled + reformatted output; baseline (no skill) did not.

### Global skills imported (4) — DB rows, NOT code
Imported 4 Anthropic skills as **global** bow skills (`data_source_ids=[]`, `load_mode='intelligent'`, published): **Brand Styling** (brand-guidelines, de-Anthropic'd → generic house palette; do NOT ship "Anthropic official" brand into a whitelabel), **Internal Comms Writing** (internal-comms), **Theme Factory** (theme-factory), **Doc Co-Authoring Workflow** (doc-coauthoring). Verified: 3P-update chat pulled `9397fcab` (Internal Comms) → proper Progress/Plans/Problems doc grounded in real City Mart data.
- ★These are **DB rows in this org's Postgres** (survive restarts via volume), NOT in the git image and NOT version-controlled. To make them portable/shipped → need a seed script.
- Skill fit for this analytics project: executable lane → xlsx/pptx/docx/pdf (HIGH); prompt-skills → brand/comms/theme/doc-coauthoring (global). Skip claude-api (OpenRouter-only), mcp-builder/skill-creator/webapp-testing (dev tools), algorithmic-art/canvas/frontend/web-artifacts (not analytics).

### ★ Real PDF export for `doc` artifacts (was broken)
- **Root cause of the broken PDF (vertical 1-char-per-line text):** the PDF button = browser `window.print()`; the doc was `position:absolute` inside the **narrow artifact panel** (an ancestor with position/transform = its containing block), so `left/right:0` spanned only that sliver.
- **B — real server PDF (NEW):** `GET /artifacts/{id}/export/pdf` (`routes/artifact.py`, after the pptx route) + NEW `services/pdf_export_service.py`: `markdown-it-py` → HTML (typography mirrors DocViewer) → **Playwright headless Chromium `page.pdf()`**. **Zero new deps — `playwright` (+ chromium binary) and `markdown-it-py` + `pygments` are ALREADY installed in the image** (checked: no weasyprint/reportlab/fpdf; playwright works). Route only for `mode=='doc'`, reads `artifact.content['markdown']`, audit-logged, 500 on render failure. Verified: http 200, valid `%PDF-1.4`, 2 pages, full-width text extract.
  - FE: `ArtifactFrame.vue` viewer PDF button → new `exportDocPdf()` (mirrors `exportPptx` authed-blob download, falls back to `printDoc()` on error). Icon → `document-arrow-down`.
  - ★Limitation: server PDF renders text/tables/headings/lists faithfully but shows **charts as a placeholder** (`_strip_viz`) — charts are a frontend-only render. Chart-perfect capture = the print path. Chart-in-server-PDF = clean v2 (render the live page).
- **A — repaired print CSS (fix A):** `DocViewer.vue` (`printing-doc`/`.doc-viewer`) + `DocEditor.vue` (`printing-doc-editor`/`.bow-doc-editor`) `@media print` — added an **ancestor neutralizer** (`position:static; transform:none; overflow:visible; max-width:none; float:none` on `body *`) so the absolute doc resolves to the PAGE (full width) not the panel; the more-specific `.doc-viewer`/`.bow-doc-editor` rule re-applies `absolute` (wins on specificity among !important). Owner editor keeps browser print (captures live/unsaved edits + charts); viewer uses the server download.
- Deploy: backend `docker cp`+restart for the route; **FE needs full image rebuild** for both fixes. Backups `*.bak-pdfexport-20260723`.

### ★ One connection → multiple agents (removed the blocking warning)
- `NewAgentWizardModal.vue`: **removed** the pt3 `dupConfirmed` two-click red block. First click on Save & Continue now creates. Replaced with a neutral **blue info note** (`dupHint` computed): "X already has an agent. One connection can back multiple agents — this adds another on the same data." Never gates creation. Backend already allows multiple agents per connection (no change). Backup `*.bak-multiagent-20260724`. (Supersedes the pt3 "Duplicate-agent warning" bullet above.)

## Session 2026-07-24 (pt2) — MULTI-USER FABRIC: one connector, per-user login/tables/instructions (Phases 1-4 + connect-button). ALL LOCAL TESTS 20/20

Goal: ONE Fabric connector, super-admin enables it, EVERY member self-signs-in with their OWN Microsoft account → their OWN tables/schemas/workspaces (RLS) + their OWN private instructions — fully isolated. All additive/flag-gated, cp+restart backend (no rebuild except the FE toggle). Backups `*.bak-*-20260724`. **Uncommitted.** Shared Fabric agent `b9312b92` on connection `115a902d` (config-less), org `9bf37931…`. Test users: admin `admin@cityagent.io`/`CityAgent#2026`, member `analyst@cityagent.io`/`Analyst#2026`.

### What already existed (proven, NOT rebuilt)
`fabric_user` connector complete (Phases 1-5 from [[project_cityagent_insights_fabric_user]]): device-code sign-in, cross-tenant discovery, federated multi-lakehouse merge, per-lakehouse query routing. Per-user primitives already keyed by `user_id`: `user_connection_credentials` (token), `user_connection_tables` (overlay). `Instruction.user_id` column existed (but was PROVENANCE = creator, populated on EVERY row — NOT a privacy flag).

### Phase 3 — Isolation hardening (verified: no leak)
Audited every layer — all keyed to the querying user: list (`UserDataSourceTable.user_id==current_user`), token + overlay (`_build_fabric_federated_client` filters `user.id`), query build (`construct_clients` uses the COMPLETION's user = `head.user_id`, gated by `user_can_access_data_source`), no cross-user client cache. Added defense-in-depth to `_build_fabric_federated_client` (`data_source_service.py`): refuse to build without an explicit user + audit-log the token owner. Backup `.bak-p3isolation-20260724`.

### Phase 1 — Open to all members (ZERO code — just data + existing model)
Created ONE shared Fabric agent `b9312b92` **`is_public=true`**. `user_can_access_data_source` (`permission_resolver.py:798`) grants any org member access to a public DS; `manage` stays admin/owner-only. So public agent = "open to all, admin-only config" out of the box. Verified: member sees it, member PUT config → **403**. Also blanked the connection's stale `server_hostname` via ORM → config-less auto-discover-ALL. (★config col is a double-encoded JSON string — edit via ORM `json.dumps`, not `jsonb_set`.)

### ★★ Connect-button-missing fix (`connection_identity.py`)
Root cause: `supports_user_token()` returned True only if `"oauth" in allowed_user_auth_modes`. `fabric_user`/`powerbi_user` auth via **device-code/`user_login`** (modes=null) → skipped the per-user status builder → fell to the owner→**system** fallback (`build_user_status` `effective_auth="system", uses_fallback=True`) → the chat picker (`DataSourceSelector.vue` `isUsable`) treated it as usable-via-system → **hid the Connect button** (admin stuck: no system creds AND no connect). Fix: `USER_LOGIN_TOKEN_TYPES={"fabric_user","powerbi_user"}`; `supports_user_token` also True for those types → `effective_auth="none"` when no token → Connect shows for everyone incl. admin. Original `powerbi` (system/oauth) untouched. Backend-only, no rebuild (picker already renders Connect for connectable agents). Backup `.bak-connectbtn-20260724`.

### Phase 4 — PER-USER PRIVATE INSTRUCTIONS (the one genuinely new feature). Flag `PER_USER_INSTRUCTIONS` (default OFF = byte-identical; ON in `.env`+compose)
- **NEW column `is_private`** (migration `ca02priv01ins`, head; `server_default false`, backfills existing rows shared). ★`user_id` is the CREATOR on every row so it can't be the privacy flag — needed a separate `is_private`. `false`/NULL=shared; `true`=private to `user_id`.
- **Retrieval scope** (`instruction_context_builder.py` `_user_scope()` applied to ALL load paths — always/intelligent/build/skills): `is_private IN (false,NULL) OR user_id=current_user`. Flag off / no user → None → no filter.
- **Create gate** (`instruction_service._can_manage_shared_instruction`): full-admin or per-agent `manage` → may write shared (or own private); else FORCED private. Fails CLOSED.
- **★★Route fix** (`routes/instruction.py` POST `/instructions`): REMOVED `@requires_permission('manage_instructions')` decorator (it 403'd the exact members the feature is for). New in-body gate: manager → create (shared/private); flag-on member with ACCESS to the agent → create PRIVATE only (forced); else 403. ★load the DataSource and pass it to `user_can_access_data_source` (passing `ds=None` skips the is_public bypass → false 403). Flag-off preserved (non-manager → 403 as before).
- **★★Approval-bypass** (`_auto_finalize_build` new `force_publish` param, passed when `is_private`): a member's build normally stays `pending_approval` (needs admin) → the private rule NEVER reached main build → NEVER loaded into AI context. Private rules need no approval (owner-only) → force approve+promote to main. Isolation at retrieval keeps them invisible to others. (`_can_auto_publish_build` unchanged for shared.)
- **List isolation** (`_execute_instructions_query` — the LIST path, NOT `_visible_main_build_conditions`): added the same `is_private/user_id` scope so a promoted private rule never LISTS to other users (incl. admins). Serialization: `is_private` added to `InstructionListSchema` + `InstructionBase` (detail via from_orm already had it).
- **FE toggle**: `useAppSettings.ts` `perUserInstructionsOn`; `KnowledgeExplorer.vue` — `draft.is_private` + a **Private↔Shared** pill toggle (flag-gated, lock/globe) in the editor meta row + a **Private** badge on private rows; `is_private` wired into create/update bodies + edit-populate. FE needs image rebuild (done).
- Backups `.bak-p4peruser-20260724` (config/settings/context-builder/model/schema/service), `.bak-p4route-20260724`, `.bak-p4list-20260724`, `.bak-p4toggle-20260724` (FE). Flag added to `docker-compose.dev.yaml` + `.env` (`PER_USER_INSTRUCTIONS=true`).

### ★★★ LOCAL TEST 20/20 (`scratchpad/local_test.py` + `listcheck.py`)
Definitive proof via the REAL AI-context builder + list: **ADMIN sees [shared] only; ANALYST sees [shared + own private]**; member's "shared" request FORCED private; member create on shared agent works (200, private); PDF export real `%PDF-1.4`; multi-agent-per-connection 200; connect shows (effective_auth=none). ★Bugs FOUND BY testing (all fixed): member-403 (route), approval-trap (force_publish), list-leak (`_execute_instructions_query` scope). ★Test-harness landmines (not product): `/instructions` list `limit` cap is **le=200** (500→422); order pushes new rows past `limit=100` (use 200); instruction text has control chars → `json.loads(strict=False)`; hard-purge test instructions FK order = association→build_contents→instructions; `data_sources` name unique (soft-deleted name still collides → 409, use unique names); ★DB `build_content`→ actual table is `build_contents`/`instruction_builds`.

### Remaining (optional)
Phase 5 two-user LIVE sign-in leak test (needs a 2nd real Microsoft account) · Phase 6 polish partially done in pt3 below (Reconnect on expiry still open, admin who's-connected overview, stale-endpoint soft-delete). Browser-only checks pending: Fabric device-code sign-in via the UI modal + clicking the Private/Shared toggle. **Power BI** (`powerbi_user`) can be the SAME zero-config per-user pattern (`PowerBIUserConfig.default_tenant_id` already Optional/auto-discover); connect-button fix already covers it; data reach is capped by Power BI API (datasets need Pro/Build, ~6 tables in this org) not the connector.

## Session 2026-07-24 (pt3) — Fabric connection UX 4-pack (2 sub-agents), BAKED + VERIFIED

Built by 2 parallel sub-agents (backend + frontend), backups `*.bak-uxbuild-20260724`. All scoped `fabric_user`/`powerbi_user` only; every other connector byte-identical. Baked (image rebuild + docker cp backend), **uncommitted**.

1. **Auto-activate tables on sync** — hook at end of `DataSourceService._upsert_user_overlay` (data_source_service.py ~L4225, before final commit; covers BOTH generic + `_merge_all_fabric_endpoints` paths): synced names → `datasource_tables.is_active=true`. Kills the "0 active tables after successful sync" bug.
2. **Token lifecycle on user_status** — `DataSourceUserStatus` gained optional `signed_in_at` (row.created_at) / `last_refreshed_at` (updated_at→last_used_at→created_at) / `token_expires_at` (= last refresh + 90d sliding). Helper `UserDataSourceCredentialsService._token_lifecycle` spread into BOTH builders. ★★KEY FIX (mine, post-agents): `build_user_status_for_connection` only looked up the DS-scoped row `if data_source:` — the `/data_sources/{id}/connections` route (and connection.py callers) pass NO data_source → row never found → `effective_auth:"none"` even when signed in. Fix in the builder: for the 2 user-login types, resolve the DS via `domain_connection` join when `data_source is None`. Verified live: `effective_auth:"user"`, username, dates, expires +90d.
3. **Disconnect ≠ Delete** — FE `ConnectionDetailModal.vue` amber "Disconnect my account" → `DELETE /data_sources/{dsId}/my-credentials` (DS-scoped — ★the connection-scoped `/connections/{id}/my-credentials` deletes the WRONG store for these types, token would survive). Delete demoted to small "Delete connection (admin)" link. BE `delete_my_credentials` (user_data_source_credentials_service.py L652, revoke block L667-716) now also revokes that user's `user_data_source_tables`/`user_data_source_columns` overlay (`is_accessible=false,status='revoked'`) → tables vanish instantly; repopulate on next connect.
4. **Relearn** — `POST /api/data_sources/{id}/relearn` (routes/data_source.py, gated view-perm, audit `data_source.relearned`) → `llm_sync(force_llm=True)` (new param, default False = all existing callers identical). Auto-triggered in background after successful sync: `schedule_overview_relearn` (fire-and-forget, own session, swallows errors) wired in `fabric_user_signin._run_federated_sync` + powerbi_user_signin (replaced its blocking in-request llm_sync). FE: "Use LLM to learn agent" button in TablesSelector save bar + "Your sign-in" panel (dates + 90-day lifebar) in ConnectionDetailModal; select-all banner "New syncs activate all tables automatically". Verified: relearn 200 returned overview grounded on real tables (customer_rfm_2024 etc.), 54/54 tables active, FE strings in dist, both routes registered (401 unauthed).
- ★Did NOT live-fire disconnect (would destroy the real signed-in token; device-code re-signin needed). Browser check pending.

## Session 2026-07-24 (pt4) — 5-fix pack: Test/Reindex token, publish-aware relearn, Sync-now, learn-toggle, universal Disconnect. BAKED + LIVE-VERIFIED

2 sub-agents (be-fix5/fe-fix5) + 3 hand fixes. Backups `*.bak-fix5pack-20260724` (incl. Dockerfile). All gated `fabric_user`/`powerbi_user` except ⑤ (deliberate widening). **Uncommitted.**

1. **Test button (was: "client_id should be the id of a Microsoft Entra application")** — THREE stacked root causes, all fixed:
   a) `ConnectionService.resolve_credentials` (connection_service.py ~L1380): new branch for the 2 types — resolves DS via `domain_connection`, row via `get_primary_active_row`, then delegates to `DataSourceService.resolve_credentials` (mints via `mint_access_token`, persists rotated refresh_token). No row → clean 403 "Connect required"; current_user None (background indexer) → clean 400. Never builds `ClientSecretCredential(None,None,None)` again.
   b) `ConnectionService.construct_client` (~L1324): device-code connections have BLANK `server_hostname/database` config → client connected to "" → HYT00 timeout (~186s). Fix: borrow host/database/tenant from the user's first accessible `user_data_source_tables.metadata_json.fabric` overlay row.
   c) ★★18456 wrong-tenant landmine was LATENT in `DataSourceService.resolve_credentials` (~L2404): mint chain was `config_tenant or stored home tenant` — config blank → home tenant → endpoint rejects. Fix: chain now `config_tenant or OVERLAY endpoint tenant (metadata_json.fabric.tenant_id) or stored or "organizations"`. Verified: Test green 2.7s "Successfully connected".
2. **Relearn → PUBLISHED overview** (`llm_sync`, data_source_service.py): reuse query only matched `status='draft'` but creation PUBLISHES the overview + sets primary → relearn wrote an invisible shadow draft (+ raw-JSON text bug). Fix (force_llm=True path ONLY; classic path byte-identical): query widened to draft+published ordered primary→published→newest, status preserved; JSON-blob guard (`json.loads(strict=False)` when text starts `{`); stray-draft soft-delete after published update. One-time psql: soft-deleted stray draft `41f867d9`. Verified: published primary `83460ff9` now grounded text, 1 live instruction.
3. **Modal Reindex → "Sync now"** (ConnectionDetailModal.vue): for user-login types `reindex()` → `syncNow()` → `POST /connections/{id}/my-schema/refresh` (= `get_user_data_source_schema`, SAME federated path as post-signin sync; returns real table_count). Verified live: 54. Generic Reindex untouched for others.
4. **Learn toggle** (TablesSelector.vue): "Use LLM to learn agent" button REMOVED → `UToggle` "Learn agent after saving" (default ON, component-state only — PUT /data_sources needs manage-perm but members hold view; relearn needs only view). Save → tables saved → if toggle: POST /relearn with inline progress; relearn failure = warning toast, never fails the save.
5. **Universal sign-in panel + Disconnect** — BE: `_token_lifecycle` generalized to ANY per-user row (dates always; expiry only if actually known — +90d for the 2 types, else row.expires_at/metadata); new `DataSourceUserStatus.credential_scope` ("data_source"|"connection"). FE: panel gates on `signed_in_at` (any connector), lifebar only with expiry; amber Disconnect whenever `effective_auth==='user'`, endpoint picked by `credential_scope` (defensive type fallback); red Delete only when NO own credential; old red Disconnect buttons re-gated `!hasOwnCredential` (no double-show).
- ★★★BAKE LANDMINE: `docker compose build` returned **CACHED for `COPY ./frontend`** after live component edits → exit-0 no-op image (old image ID, FE strings missing from dist). Fix: edit the comment line above `COPY ./frontend /app/frontend` in Dockerfile to bust the layer, rebuild (yarn generate reruns, apt/yarn-install layers stay cached). ALWAYS verify new image ID + grep new strings in `/app/frontend/dist/_nuxt` after an FE bake.
- Verified on final bake: Test success 2.7s · my-schema/refresh table_count 54 · user_status `user/data_source/dates` · 54/54 tables active · 1 live overview · all 3 FE strings in dist. Disconnect still NOT live-fired (protects real token).

## Session 2026-07-24 (pt5) — Phase A+B: upload-agent 32s→0.6s + auto-activate + learn-once. BAKED + LIVE-VERIFIED

2 sub-agents (be/fe-phaseAB), backups `*.bak-phaseAB-20260724`. **Uncommitted.**

- **B1 upload speed**: file_service.py:~300 CSV branch no longer awaits `llm_sync` in-request (was the measured 32s). Now: sync `refresh_data_source_schema` (fast, no LLM) + background `schedule_overview_relearn` (gated `learn` param AND `use_llm_sync`). Measured after: **0.5-0.6s upload**, draft ready ~28s later in background.
- **B2 learn-once**: `POST /data_sources/{id}/files?learn=false` (new bool query param, default true) — FE sends false for all but LAST file. Server-side dedup: module `_RELEARN_INFLIGHT` set in data_source_service (schedule skips if ds pending; bg task clears in finally) — fabric/pbi sign-in callers get idempotency free.
- **B3 draft fetch**: new `GET /data_sources/{id}/onboarding_instruction` → {id,title,text,status} newest live onboarding instr (primary→published→newest) or 404, view-perm. Wizard Set-context now POLLS it (2.5s × 16) instead of re-running llm_sync ×2 (double LLM spend killed); fallback single llm_sync if poll exhausts.
- **A1 auto-activate uploads**: end of `sync_domain_tables_from_connection` — `connection.type=='csv'` only → synced names `is_active=true`. Review step shows tables pre-checked (TablesSelector seeds checkboxes from backend `is_active` — verified, no FE change needed).
- **A2 stale endpoints**: `_merge_all_fabric_endpoints` — rows not seen in a FULLY-clean sync (failed_endpoints==0 guard) relabeled `status='stale'`+is_accessible=false. Note `_upsert_user_overlay` already revoked not-seen rows; this refines label, never mass-stales partial syncs.
- **A3 toggle persist**: TablesSelector `learnAfterSave` ↔ localStorage `bow.learnAfterSave.<dsId>` (default true, per-DS, reloads on DS switch).
- FE also: upload loop `?learn=false` except last, duplicate post-upload `GET /refresh_schema` removed, "Learning agent in background…" chip on Review step.
- Bake: Dockerfile cache-bust comment edited (phaseAB) — CACHED landmine again avoided; verified new image ID + 4 FE strings in dist + 0.5s smoke on final container.

## Session 2026-07-24 (pt6) — Phase C: Reconnect-on-expiry + admin "who's connected" roster. BAKED + SMOKE-VERIFIED

2 sub-agents (be/fe-phaseC), backups `*.bak-phaseC-20260724`. **Uncommitted.**

- **C2 BE roster**: `GET /connections/{connection_id}/user_roster` (routes/connection.py:786) — admin-gated via existing `_is_org_admin` (full_admin_access OR manage_connections; member → 403). Unions BOTH per-user stores: `user_data_source_credentials` (DS-scoped, via connection→data_sources link) + `user_connection_credentials` (connection-scoped OAuth), `is_active` only. ONE row per user (freshest `last_refreshed_at` wins). Schema `ConnectionUserRosterEntry` (data_source_schema.py): user_id/email/name/signed_in_at/last_refreshed_at/token_expires_at/credential_scope/expired — ZERO token fields. Lifecycle via `_token_lifecycle` (same as user_status). Both credential models `lazy="selectin"` → no async lazy-load crash; `connection.data_sources` eager via get_connection selectinload. Note: live roster only (revoked users drop off); expired=false with no known expiry = "unknown".
- **C1 FE Reconnect**: ConnectionDetailModal.vue — `isTokenExpired` computed (expired flag / token_status / token_expires_at <= now) → red warning "Your sign-in has expired. Reconnect to restore access." + blue Reconnect button → SAME `openCredentialsModal()` device-code flow (no reimplementation); lifebar flips red. After creds saved, `refreshUserStatus()` re-fetches `/data_sources/{dsId}/connections` → statusOverride updates dates in place (no reload). Non-expired UI unchanged.
- **C2 FE roster table**: collapsed "Connected users" expander (user-login types + `usePermissions()` includes full_admin_access only), lazy-fetch on first expand; columns user/signed-in/refreshed/expires (red expired badge)/scope; 403/404/any error → section hides silently (`rosterHidden`).
- Bake: cache-bust → phaseC, image `439794dafacf`, yarn generate re-ran; 8 backend files docker cp'd + md5-verified; 4 FE strings in dist (`Your sign-in has expired` / `Connected users` / `user_roster` / `No one has connected yet`). Smoke: admin roster → 1 row (admin, expires 2026-10-22, expired=false, scope data_source); member → 403.
- Pending browser checks: Reconnect button (needs actually-expired token) + roster expander render.

## Session 2026-07-24 (pt7) — Phase D: powerbi_user zero-config VERIFIED (read-only, no code changes)

Verify agent ran the fabric_user parity checklist against live :8095 — ALL PASS: fields clean (no system-cred leak, `default_tenant_id` optional), config-less create works, signed-out user_status `effective_auth:"none"` (Connect shows), device-code routes mounted (401 not 404), Test signed-out → clean "Connect required" in 0.03s (403 carried in 200 envelope `{success:false}` — same wrapper as fabric), roster 200-empty/member-403, `_token_lifecycle`+auto-activate+relearn wiring all gate both types. Test objects cleaned.
- ★Connect-button mechanism note: live fix lives in `user_data_source_credentials_service.py` `build_user_status`/`build_user_status_for_connection` (type-gated), NOT `connection_identity.py` `USER_LOGIN_TOKEN_TYPES` (that variant only in a .bak). Functionally same result.
- Known parity gaps (all LOW, not bugs): G1 `powerbi_user` NOT flag-gated in catalog (fabric_user hides behind `HYBRID_FABRIC_USER`) — decide intent; G2 overlay-tenant 18456 fix fabric-only (PBI REST doesn't hit 18456; only matters if cross-tenant PBI SQL ever wanted); G3 stale-endpoint relabel fabric-only (PBI uses `_merge_all_tenants`, `_upsert_user_overlay` still revokes not-seen rows → no leak, just no "stale" label).

## Session 2026-07-24 (pt8) — Learn controls for ALL connectors. BAKED

User request: the Fabric-only learn UX must exist on every connector (e.g. IT Ops DB agent gets new tables → wants retrain). `TablesSelector.vue` (backup `.bak-learnall-20260724`):
- **"Learn agent after saving" toggle un-gated** — removed `v-if="isUserLoginDs"` from the label AND the `isUserLoginDs &&` from the post-save chain → every connector's Save can chain relearn (localStorage per-DS preference unchanged).
- **NEW "Learn now" button** in the save bar (all connectors) — `onLearnNow()` → `runRelearn()` (`POST /data_sources/{id}/relearn`) without saving; spinner + green/orange toasts; disabled while saving/relearning.
- Safe because the relearn route is connector-agnostic (view-perm, `force_llm=True` overrides stored `use_llm_sync`, publish-aware from pt4 fix ②). `isUserLoginDs` still used by the select-all banner + connect prompts (untouched).
- Bake: cache-bust→learnall, image `1675333eb233`, both strings in dist (`Learn now`, `Learn agent after saving`), 8 backend files re-copied md5-verified, health 200.

## Session 2026-07-24 (pt9) — DEFAULT AGENTS SEEDER: fresh install auto-creates 3 agents. TESTED ON FRESH DB

First signup on a fresh install now seeds **Microsoft Fabric** (fabric_user, config-less, per-user Connect), **Power BI** (powerbi_user, same), **City Mart Retail** (duckdb sample + 11 tables active + 6 teaching instructions + 6 starters). All `is_public=true`, `use_llm_sync=false` (NO LLM on signup path). Backups `.bak-seed-20260724`.
- NEW `backend/app/services/default_agents_seeder.py` (`seed_default_agents`); hook in `core/auth.py` `_ensure_org_for_first_uninvited_user` right after `create_organization` (~L650) — the single funnel for local-register + OAuth first signups; whole call try/except-swallowed (signup unbreakable). Runs only when `total_users==1 && total_orgs==0` → existing installs can NEVER retro-seed (verified live: 0 new agents).
- Idempotent: marker `default_agents_seeded` in `OrganizationSettings.config` + per-agent name guard (checks soft-deleted rows too). Flag `SEED_DEFAULT_AGENTS` (config.py, default true, compose passthrough). `hybrid_fabric_user` DEFAULT flipped False→True (Fabric always in catalog; powerbi_user was never gated).
- ★★SEEDER BUG FOUND+FIXED in fresh-DB test: citymart tables seeded 11/11 but ALL `is_active=false` — `create_data_source` onboarding sync inserts rows inactive (`ONBOARDING_MAX_TABLES=0`) and the demo `_load_tables` re-sync **preserves is_active on existing rows** (its `max_auto_select=9999` only applies to NEW rows). Fix: seeder directly sets `is_active=true` on all DS rows after load. Same landmine as the pt-earlier "raw-sync leaves is_active=false" — any second sync path will NOT activate.
- Fresh-DB test (throwaway stack `-p seedtest`, ports 8099/5443; ★compose pins container_name → must sed-rename containers in a copied compose file to run parallel): register → 3 agents public, citymart 11/11 active + 6 published instructions + 6 starters; fabric/pbi `effective_auth:none` (Connect shows); restart + marker `t` + second signup (400 self-signup-disabled) → still exactly 3 DS. Stack torn down `-v`.
- ★rtk hook TRUNCATES long curl stdout (~500 chars, breaks json parse) — write to file then parse.
- Deploy = backend docker cp (seeder+auth+config) + container recreate for compose env line. AWS/new-box: everything in image, nothing machine-local.

## Session 2026-07-24 (pt10) — Onboarding flow fix for seeded installs + role-aware onboarding. BAKED

Bug: on a seeded fresh install, Welcome→Next dead-looped — `OnboardingView.vue` guard `!llmDone && dataDone → replace('/')` (predates seeder; assumed data-without-LLM = broken state), and `pages/index.vue` onMounted redirected right back into onboarding. Backups `*.bak-obflow-20260724`.
- `OnboardingView.vue` guard: `!llmDone && dataDone` → ADMIN (`useCan('full_admin_access')`) may stay on `/onboarding` + `/onboarding/llm` (Next now reaches the LLM key step); MEMBER → replace('/'). After key saved all steps green → auto `/?setup=done`.
- `pages/index.vue`: onboarding redirect + setup banner both admin-only (members land straight on seeded workspace; banner was a dead end for them).
- `locales/en.json` onboarding intro: "workspace is ready with three agents — Microsoft Fabric, Power BI and City Mart Retail. One step left: connect your AI model" (+ intro2 "Let's connect your model!").
- Flow now: admin signup → Welcome (3 agents named) → Next → OpenRouter key → home with agents live. Members: no onboarding ever.
- Image `4fa1c3a6e3b6` (cache-bust→obflow); both stacks (live :8095 `bow-app-cai` + fresh-product :8097 `bow-app-fresh`, compose copy in scratchpad `compose-fresh.yaml`, project `freshproduct`, pg :5444) recreated + 11 backend files re-copied; strings verified in dist.

## Session 2026-07-24 (pt11) — "Coworker AI" → "Insights" rename. BAKED
Brand string lived ONLY in `frontend/pages/users/sign-in.vue` (logo alt, cw-name, greeting h1, footer) — all → "CityAgent Insights"; footer tagline → "Your AI analyst for data". Backup `.bak-insightsname-20260724`. Cache-bust→insightsname; both stacks rebuilt+recreated, backend files re-copied, dist grep: "CityAgent Insights" present, zero "Coworker AI" left.

## Session 2026-07-24 (pt12) — first-learn draft-orphan fix. DEPLOYED both stacks
Bug (fresh-product test): Fabric "Save + Learn" / sign-in auto-relearn RAN the LLM but wrote the overview as a **draft** that nothing ever publishes → UI "0 instructions / No primary instruction". Root: llm_sync's create-new branch hardcoded `status="draft"`; publishing lives only in the creation wizard's Finish, which seeded/sign-in agents never pass. pt4 fix ② only covered the reuse-existing branch.
Fix (`data_source_service.py` ~L1181): `force_llm` new-overview → `status="published"` + set `primary_instruction_id` if empty. Classic wizard path keeps draft. One-time psql on :8097 published the 2 orphans (Fabric e61215b6, CRM f8642caa) + set primaries. Verified: 8 published, Fabric/CityMart/CRM have primary; PBI none (not signed in — correct).

## Session 2026-07-24 (pt13) — City Mart no-overview root cause + LLM-config auto-heal + JSON-escape guard. DEPLOYED both stacks
Why City Mart had no overview/description/starters while Fabric did: seeding runs at FIRST SIGNUP, BEFORE the admin enters the OpenRouter key → llm_sync's 3 generators failed ("'NoneType' object has no attribute 'model_id'") → `_maybe_promote_fallback_primary` promoted a random teaching rule ("top X by Y" chart) as primary. Fabric learned AFTER the key existed → fine.
- **Auto-heal hook** (`llm_service.py` `.bak-sealheal-20260724`): after `create_provider` succeeds → `_heal_seeded_agents_missing_overview` — seeded installs only (`default_agents_seeded` marker), for each org DS with ACTIVE tables but zero `ai_source='onboarding'` instructions → `schedule_overview_relearn` (background, swallowed). Signed-out Fabric/PBI (0 tables) skipped — their sign-in flow learns later. So next fresh install: key saved → City Mart auto-learns.
- **★JSON-escape guard widened** (`data_source_service.py` ~L1025): overview blob contained `*\_id` (markdown-escaped underscore) = invalid JSON escape → json.loads fails EVEN with strict=False → raw `{"title":...}` blob stored as instruction text. Fix: retry parse with invalid escapes doubled (`re.sub(r'\\(?!["\\/bfnrtu])')`).
- :8097 repaired in place: cleared wrong primary, ran force llm_sync in-container (★pattern: `docker exec -i ... python -` + `import main` to register FULL ORM registry — partial model imports die on ApiKey/DataSourceApplicationAssociation), new published overview 45a297ed set as primary + description + 4 starters; blob-text row rewritten to clean prose via pg_read_binary_file.

## Session 2026-07-24 (pt14) — Learn-now 3-fix: pending-review, not-primary, duplicate starters. DEPLOYED both stacks
Round-2 fresh test: Learn-now generated a GOOD overview but (a) stuck "Pending review" — create used `auto_finalize=False` so its build never finalized; (b) "Not primary" — seed-time fallback had promoted a chart rule, my primary-set only fired when EMPTY; (c) 8 starters with dupes — two learns stacked 4+4.
Fixes (`data_source_service.py` llm_sync): create-new overview `auto_finalize=bool(force_llm and current_user.id)` (live immediately for real users; blank User() would fail perms and the failed-finalize path SOFT-DELETES the instruction — guard); force_llm now ALWAYS repoints `primary_instruction_id` at the fresh overview (deliberately replaces seed-fallback/stale primary; admin can re-Change); starters deduped case-insensitively, capped 6, replace-not-stack.
:8097 repaired: admin re-learn in-container (reuse-branch updated overview in place, primary now overview, 4 clean starters); stale draft build `bf8b3703` soft-deleted (was the "1 pending" badge).

## Session 2026-07-24 (pt15) — SSO fixed provider list + enable-without-config + login pre-guard. BAKED both stacks
Sub-agent `sso-list`, backups `*.bak-ssolist-20260724`. (1) Settings SSO card: "+ Add provider" dropdown REMOVED; 4 fixed always-visible rows (Keycloak→`keycloak`, Generic OIDC→`oidc`, Google→google block, Entra ID→`entra`) each with enable UToggle + Configure (reuses inline form) + amber "Not configured" badge; toggling an absent row writes a minimal enabled entry (blank issuer/client_id allowed — `update_config` issuer/client-id-required-when-enabled validation REMOVED, format-only http(s) check on issuer when present; slug/secret-encryption unchanged). (2) Public `/api/settings` feed: enabled providers always listed with `configured` bool (google=client_id+secret; oidc=issuer+client_id, secret folded in for NON-PKCE — PKCE public clients legit secretless [my post-agent tweak]). (3) sign-in.vue: button for every enabled provider; `configured:false` click → inline "«Label» sign-in is not available yet — ask your admin to finish setup." no redirect; same-message 400 guard in `auth_providers.build_authorize_url` for direct URL hits. ★Google button now FEED-driven (runtimeConfig googleSignIn no longer wins). Baked (cache-bust→ssolist) + 15 backend files docker-cp'd to BOTH containers; feed verified `{'enabled': False, 'configured': False}`.

## Session 2026-07-24 (pt16) — own version 0.0.482.1 + internal changelog entry. DEPLOYED
Version = repo-root `VERSION` (settings.PROJECT_VERSION reads `../VERSION` from /app/backend cwd); What's-New modal = repo-root `CHANGELOG.md` parsed by `routes/changelog.py` (`_HEADER_RE` `## Version <\S+> (Date)` — 4-part 0.0.482.1 fine). Bumped VERSION→`0.0.482.1`, prepended 6-bullet internal entry (seeder, onboarding, learn-everywhere, upload speed, sign-in lifecycle, SSO list). docker cp both files to /app/ in BOTH containers + restart (VERSION read at startup). Verified `/api/changelog` current 0.0.482.1, top entry 6 bullets. Backups `VERSION.bak-ownver-20260724` + `CHANGELOG.md.bak-ownver-20260724`. Convention: our releases = `<upstream>.N` suffix.

## Session 2026-07-24 (pt17) — v0.0.482.1 PUSHED + install/upgrade runbook
- **PUSHED**: commit `0388d866` + tag `v0.0.482.1` on origin/main (`cityagent-coworker-ai`). 80 files +7259/-503 — the entire uncommitted stack. `.gitignore` += `*.bak-*` (157 backups excluded); `.env` ignored; staged-diff secret scan zero hits; 12MB `demo-datasources/citymart_retail.duckdb` COMMITTED (seeder/image needs it).
- **Version convention**: our releases = upstream version + `.N` suffix (`VERSION` file + `## Version X (Date)` entry prepended to `CHANGELOG.md`; parser regex takes any `\S+`). Deploy of a version bump alone = docker cp VERSION+CHANGELOG.md to `/app/` + restart (VERSION read at startup).
- **Install (fresh server)**: clone → write `.env` (APP_PORT, POSTGRES_PORT, POSTGRES_PASSWORD, BOW_ENCRYPTION_KEY=`openssl rand -base64 32`, flags SEED_DEFAULT_AGENTS/HYBRID_FABRIC_USER/PER_USER_INSTRUCTIONS/CLEAN_FILE_TABLE_NAMES=true) → `docker compose -p cityagentinsights -f docker-compose.dev.yaml build app && up -d` → first signup = super admin → OpenRouter key → 3 agents ready.
- **Upgrade**: `git pull` → same build + `up -d app`. Volumes (`postgres_data_dev`, `uploads_data_dev`) survive; migrations auto-run; seeding fires ONLY on empty DB (never re-seeds). ★BOW_ENCRYPTION_KEY must NEVER change post-install (loses every stored credential). ★NEVER `down -v` in prod (wipes volumes).

## Session 2026-07-24 (pt18) — Per-user table selection + training (Fabric + Power BI). Flag `HYBRID_PER_USER_TABLE_SELECT`, BAKED, UNCOMMITTED
Each member who signed in with their own Microsoft token picks which of THEIR accessible tables their agent uses, and runs a Learn scoped to those → a PRIVATE overview (`Instruction.user_id`, `is_private`). Isolated per `user_id`; shared connectors untouched.
- **Guard** `app/schemas/data_source_registry.py` `is_per_user_connector()` + `PER_USER_TOKEN_TYPES={fabric_user, powerbi_user}` (explicit set). ★★★connector type is on `connection.type`, NOT `data_source.type` — a `DataSource` has no `.type`; the guard resolves via `ds.connections[0].type`, so callers pass `selectinload(DataSource.connections)`. First cut used `ds.type` → guard always False → SHARED catalog got written (silent leak); fake-object unit tests hid it, only a live service-layer test caught it.
- **Migration** `ca03putbl01act` adds `user_data_source_tables.is_active` (per-user pick; backfilled = is_accessible). Read path `read_user_data_source_schema` filters `is_active` (agent context); paginated UI read overlays per-user is_active onto checkboxes; writes go to `_set_user_overlay_active` (id-or-name match). RBAC: `permissions_decorator.py` Tier-5 lets a connected per-user owner past the `manage` gate (own creds), gated by flag. FE `utils/perUserConnector.ts` + `useAppSettings.perUserTableSelectOn` + `KnowledgeExplorer.panelCanUpdate`. Training: `llm_sync` writes `is_private`/user-scoped overview + skips shared-primary repoint (needs `PER_USER_INSTRUCTIONS` too). Flag in config + compose + `.env`; feed `features.per_user_table_select`.

## Session 2026-07-24 (pt19) — Live Learn-agent progress (all connectors) + merged Save & Learn button. Flag `HYBRID_LEARN_PROGRESS`, BAKED, UNCOMMITTED
Replaces the bare "Learning…" spinner with live stages, for EVERY connector (shared `TablesSelector` + `llm_sync`).
- **UI = inline bottom bar** (`components/datasources/LearnProgressBar.vue`) sliding up from the panel bottom: 4-step pips + stage + "N tables · M cols" + step X/4 + elapsed → "Agent learned" → collapse. ★A first attempt used a right-side `<Teleport>` drawer — it did NOT render reliably; replaced with an in-flow `max-height` strip (no Teleport/scrim/fixed).
- **Merged button**: single primary follows the "Learn agent after saving" toggle — ON→"Save & Learn", OFF→"Save"; separate "Learn now" removed. ★ onSave now ALWAYS learns when toggle on even with NO pending table change (old code early-returned on `!hasPendingChanges`).
- **★★★Tracker MUST be DB-backed** (`app/models/learn_progress.py` table + `app/services/learn_progress.py` async): the app runs `--workers 4`, so a module-level in-memory dict (like `fabric_sync_progress.py`, which has the same latent flaw) is invisible to the worker serving the poll — progress showed idle even though `_lp_start` fired. `llm_sync` stamps 4 stages (`reading_tables→analyzing→generating_overview→grounding_publishing`) + done/error, best-effort. Endpoint `GET /data_sources/{id}/learn-status` (perm `view`) → `{status, stage, step, total, tables, columns, elapsed_ms, error, last_done_at}`. Migrations `ca04learnprog01` (table) + `ca05learnprog02` (`last_done_at`, persistent "Last learned: …"). ★You can't inspect a worker's in-memory state from `docker exec python3` (separate process) — use a temp `logger.info` + read container logs.

## Session 2026-07-25 (pt20) — App Analytics real numbers + LOCAL RUNTIME (Cowork-style laptop execution). Flags `HYBRID_APP_ANALYTICS`+`HYBRID_LOCAL_RUNTIME`, BAKED, UNCOMMITTED
**App Analytics real data**: `app/services/app_analytics_service.py` + `GET /api/console/app-analytics` (registered in `routes/console.py`, `manage_settings` gate) — every number live from DB (users/companies-by-email-domain/agents/connectors/questions/cost/ROI-from-baseline-env `ANALYTICS_*`); nulls = "Not tracked", never fabricated. FE `pages/app-analytics.vue` fetches via `useMyFetch`; nav item below Monitoring. ★User has NO `created_at` (fastapi-users base) — join date = `Membership.created_at`. ★FE `COPY ./frontend` layer caches silently: a preceding comment edit does NOT bust a Docker COPY — use `ARG FE_CACHEBUST` + `RUN echo` before the COPY, pass `--build-arg FE_CACHEBUST=$(date +%s)`. ★Final image serves `/app/frontend/dist` (frontend-builder stage), NOT `.output/public` — verify deploys there.

**LOCAL RUNTIME (the big one)**: agent-generated Python executes on the USER'S LAPTOP via a small helper; agent brain untouched. E2E-PROVEN: job dispatched by live server executed on `Rahuls-MacBook-Pro.local` (container=`cf9a062dc38e`), local-folder CSV analyzed in place via duckdb (never uploaded), data fetch proxied back to server connector (creds never left).
- **Seam (only core change)**: `StreamingCodeExecutor.execute_code_async` (code_execution.py) → dispatch: flag+helper-online → `_try_run_remote` (service `app/services/local_runtime_exec.py`) else `_run_server_async` = verbatim old body. Flag OFF = byte-identical. ANY remote doubt/error/timeout → None → server fallback (never breaks chat).
- **DB-backed job queue, NO websockets** (`--workers 4` landmine): models `app/models/local_runtime.py` (`local_runtimes` pairing+token-hash, `local_runtime_jobs` queue), migration `ca06localrt01`. Helper long-polls `GET /api/local-runtime/jobs/next` + posts result (Arrow IPC b64). Routes `app/routes/local_runtime.py`: pair/start (session auth, 6-digit 10-min code) · pair/claim (public, mints token) · status/toggle/DELETE · heartbeat · jobs/next · jobs/{id}/result · **`POST /local-runtime/query` data proxy** (runtime-token auth → `construct_clients` RBAC backstop + `validate_sql_query` write-block + 250k row cap → Arrow).
- **Helper**: `local-runtime/helper.py` (single file: pair/run, RemoteClientProxy execute_query→server, LocalFolderClient duckdb over whitelisted folders, stdout capture, Arrow serialize). Deps: requests pandas numpy pyarrow duckdb. Config `~/.cityagent-local-runtime.json` (0600).
- **FE**: Settings tab `local-runtime` (any member, flag-gated; `layouts/settings.vue` now supports permissionless+`requiredFlag` tabs), page `pages/settings/local-runtime.vue` (pair code + status poll 5s + run-local toggle + unpair), i18n `settings.localRuntime.*`.
- **v0 guards**: excel_files/loadables/http jobs stay server-side (`_job_supported`). Online window 30s; remote timeout 120s then job expired + server fallback (P7 suite: 7/7 pass incl dead-helper, toggle-off, ghost-user).
- ★TestClient/asyncpg loop clash in tests → use `httpx.ASGITransport` single-loop. ★`UID` is zsh-readonly. ★`docker exec python <<heredoc` needs `-i`.
- Rollbacks: tag `pre-local-compute` (+ tarball `bagofwords-BACKUP-20260725-064149.tar.gz`). Unused stub flag `HYBRID_LOCAL_COMPUTE` (browser-WASM Option A) also present, OFF, harmless.

## Session 2026-07-25 (pt21) — LR 3-feature pack: provenance badge + folder-attach-in-chat + Windows helper prep. BAKED+DEPLOYED, UNCOMMITTED

Built by 3 parallel sub-agents, then live-fire tested + 3 bugs fixed. ★★ADMIN CHANGED: live :8095 DB re-created — only user is `raahulgupta07@gmail.com` (org `5b59c42c-57a7-4a27-90cf-94ef3e3f39fc`); old admin@cityagent.io / analyst@ accounts are GONE. Passwordless API testing: mint JWT in-container (`get_jwt_strategy().write_token(user)` + `create_async_session_factory()`; `import main` first for full ORM registry).

### 1. "Computed on your device" provenance badge (gated `HYBRID_LOCAL_RUNTIME`, baks `.bak-lrbadge-20260725`)
- Seam sets `StreamingCodeExecutor.last_execution_provenance` on both paths of `execute_code_async`; `_try_run_remote` fills `{executed_on: local|server, runtime_name, job_id, elapsed_ms, reason}` via `provenance_out`. No paired runtime → None → NO badge (zero noise).
- `create_data.py` copies `execution_provenance` into the step payload (existing completion JSON — no new tables); `completion_v2_schema.py` extended; FE `CreateDataTool.vue` renders green "Computed on your device · host · Xs" / grey "Ran on server" pill; i18n in `en.json`.

### 2. Folder-attach in chat (flag `HYBRID_LOCAL_FOLDER_ATTACH=true` in .env+compose, baks `.bak-lrfolder-20260725`)
- Helper `scan_folder` (DuckDB `DESCRIBE SELECT`, metadata ONLY — names/columns/types/row-counts, never rows): on `run` startup + every 10 min in the long-poll loop + CLI `python3 helper.py scan`. POST `/api/local-runtime/folders` (runtime-token; server rebuilds every field — helper can't smuggle rows); GET same path (session auth) feeds the composer menu.
- Storage: `local_runtimes.folders_schema` JSON + `folders_scanned_at` (migration `ca07lrfolders01`).
- Attachment rides `prompt.local_folders` (names only) on the completion — `resolve_attached_folder_names` walks back ≤40 user turns (sticky like a data source; explicit `[]` detaches). Planner+coder grounding: `app/ai/agents/local_folders_context.py` → `AgentV2._build_local_folders_context` → `PlannerInput.local_folders_context` (after schemas_combined in both prompt builders) + appended to `schemas_excerpt` in create_data for the coder.
- ★Forced local routing: `referenced_local_folders()` on generated code → `require_local=True` → helper offline/unpaired/toggled-off RAISES `LocalFolderUnavailable` (stream BREAKS, no retry — a retry tempts the coder to silently switch to warehouse data). No server fallback by design.
- FE: paperclip menu in `FileUploadComponent.vue` (folders list, chip 📁 name · N tables · "queried on your device"); LANDING PAGE plumbed too (baks `.bak-lrlanding-20260725`): gates dropped `!report_id`, `PromptBoxV2.createReport` carries names in the redirect query (JSON like mentions), report page first-message handler parses → first completion.

### 3. Windows helper prep (baks `.bak-lrwin-20260725`)
- `local-runtime/helper_app_win.py` (pystray tray app, guarded imports, tkinter pair dialog) + `BUILD-WINDOWS.md` (single PyInstaller cmd; do NOT use --onefile). Only the .exe build itself needs a real Windows box; zip lands in `frontend/public/downloads/CityAgentHelper-win.zip` (gitignored) + FE rebuild.
- Settings page: macOS+Windows download cards. ★HEAD lies on this server (SPA catch-all returns 200 index.html for MISSING files, 405 for HEAD) — availability check = 1-byte ranged GET + content-type (application/zip vs text/html) → "Coming soon" when absent.
- ★helper.py Windows compat fixed: `os.uname()` crash (→`platform.node()`), chmod POSIX-guard, cp1252 console→utf-8 reconfigure, DuckDB path literals via `as_posix()`.

### ★Live-fire bugs found by real E2E (baks `.bak-lrfix1-20260725`; fixes docker-cp'd AND in source — ★next `compose build` includes them, container-only until then)
1. `write_csv.py` built its executor WITHOUT `usage_context` → "'X' lives on your computer and this run has no device context" on folder CSV export. Fix: wire usage_ctx (import from `app.services.usage_policy_service`, NOT usage_limit_service) + capture `done.payload["df"]` and MATERIALIZE it server-side when the CSV was written on the laptop (remote runs write `uploads/files/...` on the LAPTOP — file never reaches the server).
2. `_LOCAL_KEY_RE` matched bare `local:` inside STRING LITERALS — a retry echoed the previous error text into codegen, the whole sentence became a "folder name", helper rejected garbage. Fix: regex anchored on the `ds_clients[` subscript.
3. `create_data` pre-flight guard ("No active tables… no files") killed folder-only reports before codegen. Fix: `has_local_folders` via `resolve_attached_folder_names` added to the guard condition.

### E2E PROOF (API, container-minted JWT)
create_data success with provenance `{executed_on: local, Rahuls-MacBook-Pro.local, 711ms}`; channel totals EXACT vs raw CSV ground truth (In-store 376,246,334 / Delivery 354,753,440 / App 341,452,483 MMK); write_csv → laptop query + server-materialized `Sales_by_city_2026.csv` + File record. Helper CLI: `python3 local-runtime/helper.py run --allow-folder ~/Data/demo-sales` (demo CSV at `~/Data/demo-sales/sales_2026.csv`, 2,400 rows).

### Gotchas this session
- Docker Desktop DIED mid-image-unpack at 3.3GiB free disk (`input/output error` + daemon EOF); post-cleanup the build cache was wiped → next bake = FULL rebuild (~12 min). Normal bakes ~2-4 min once cache repopulates.
- Old menu-bar .app (v0.1.0) predates scan support — folder registration needs the NEW helper (CLI or rebuilt .app).
- Laptop remote runs execute relative `df.to_csv('uploads/files/…')` → creates junk dirs in the helper cwd (cosmetic; v2: strip file-writes from remote jobs).
- Rollback: tarball `../bagofwords-BACKUP-lrfeat-20260725-091248.tar.gz`. ★folder-agent's `.bak-lrfolder` snapshots of the 5 SHARED files (code_execution, local_runtime_exec, create_data, helper.py, en.json) are STALE — restoring them reverts the badge/win agents' work; roll back by hunk or tarball.

## Session 2026-07-25 (pt22) — UPSTREAM 483→485 PORT + PBI multi-tenant incremental. BAKED v0.0.485.1, UNCOMMITTED

Phased port of upstream v0.0.483/484/485 onto the fork, then our own PBI speed fix on top. 139/139 unit tests green (7 suites), image rebuilt twice (final bake includes everything below + VERSION/CHANGELOG). Backups `*.bak-up485-20260725` (×27) + `powerbi_multitenant_scan.py.bak-pbimtinc-20260725`; tarball `../bagofwords-BACKUP-upstream485-20260725.tar.gz`; DB settings backup `../org_settings_backup_20260725.json`.

### ★ VERSIONING CONVENTION (corrected, supersedes pt16/17)
Pure upstream port → **plain upstream version** (`0.0.485`). Our OWN additions → **`.N` suffix** (`0.0.485.1`). CHANGELOG has separate entries for each.

### v0.0.485 — the port
- **485 PBI incremental**: `powerbi_client.py` wholesale from upstream (ours was 484-identical) — `get_schemas(prior_tables=)` skips introspection of known datasets (matched on `metadata_json.powerbi.datasetId`), `_INTERNAL_COLUMN_RE` filters `RowNumber-<GUID>` cols in both parsers. `base.py` forwards `prior_tables` via `_accepts_kwarg`. `connection_service.refresh_schema(introspection="full"|"incremental")` + same-request reuse stash (`last_refresh_fresh_tables`/`last_refresh_identity_user_id`); `data_source_service` Reload path passes `introspection="incremental"` and reuses the caller-fetched catalog for the overlay ONLY when `fetched_as == caller_id` (identity-matched — vital with per-user creds). ★Scheduled/background reindex stays FULL introspection (column drift). ★fork's fabric_user federated block runs BEFORE the prefetch shortcut (deliberate, commented).
- **483 drive docs**: `_document_text.py` gained `extract_document_text_from_bytes` — ★★HAND-MERGED, NEVER wholesale: fork carries `_strip_residual_ooxml` XML-leak scrub upstream lacks. graph/google drive clients wholesale (DOC_EXTS extract-text branch + `read_raw_bytes`; Google-native→PDF export); s3_client slimmed (helper moved to shared module). Judge cost gate: `judge_model_allowed(model)` (only a genuinely separate small-default model may judge) hand-merged into diverged `agent_v2.py` (`_llm_judgement_enabled`) + `test_run_service.py` (4 sites).
- **484 limits + data_shape**: `organization_settings_schema.py` — `limit_analysis_steps` (dead) → `agent_max_steps` (100, editable, clamp 1-500 in agent_v2 loop); `limit_code_retries` editable, clamp 1-10 via `code_retries_setting()` in code_execution.py (merged AROUND the local-runtime seam — untouched); codegen `retries: Optional=None`, 4 tool sites unpinned. Settings sync: metadata refresh + prune (`_is_feature_dict` guard = name+description keys → our `ldap`/`onboarding`/`signup_policy`/`default_agents_seeded` blocks SAFE — ★verify in DB, the GET response model hides non-schema keys so they LOOK pruned). data_shape: registry helpers `data_shape_for`/`catalog_nouns_for` + `catalog_nouns` overrides (★applied "model table(s)" to ALL THREE PBI entries; messages for mail), carried on every connection payload (routes/connection ×4, schemas, `_build_connections_list`), indexing stats stamp nouns + per-user catalogs complete honestly. FE: ConnectionDetailModal hand-merged around pt3-6 work (`isToolProvider`→`isIntegrationManaged` + `dataShape`/`countLabel`/`accessibleSummary`), ConnectionIndexingProgress wholesale, 4 keys appended per locale (en/es/he — en carries our strings, APPEND ONLY).
- Tests: upstream `test_drive_clients.py` (935L incl. catalog-nouns + data_shape schema tests), `test_judge_gating.py`, 485 `test_powerbi_client.py` (568L) + `test_refresh_overlay_prefetch.py` NEW.

### v0.0.485.1 — OUR PBI multi-tenant incremental (built by 2 sub-agents)
`powerbi_multitenant_scan.py scan_all_tenants` (~45 lines): canonical `datasource_tables` rows → `prior_tables` per tenant client; serves BOTH `powerbi_user` (`_merge_all_tenants`) + `powerbi_mt` (OAuth scan). Fail-open (any prior-load error → full crawl); first sign-in unchanged; identity = per-tenant dataset listing always runs with the signing-in user's token. ★★priors MUST come from canonical `datasource_tables` (col `datasource_id`) — `user_data_source_tables` has NO columns/pks/fks (only table_name+metadata_json); canonical rows exist for user_required connectors because union-mode `_upsert_user_overlay` creates them from user discovery. Tests `tests/unit/test_powerbi_mt_incremental.py` (4, mocked; patches module attrs — works because the client import is function-local). Fabric DELIBERATELY untouched (works, seconds-scale; research notes in memory).

### Landmines this session
- ★★★rtk mangles `diff | grep -c` (returned 0 on DIFFERENT files) and single-quoted `grep "a\|b"` — decide same/diff with `cmp -s` or python difflib ONLY. rtk truncates piped curl (~500ch) → always `curl -o file`.
- ★fresh-baked image has NO pytest (was container-pip) — `docker exec bow-app-cai pip install pytest pytest-asyncio` after every bake.
- ★build-log waiters false-match Dockerfile RUN text ("install failed") — anchor `^BUILD_OK|failed to solve`.
- ★zsh: `set -- $pair` doesn't word-split; `echo ===` after `&&` parses as glob.
- Settings route is `/api/organization/settings` (NOT `/organizations/{id}/settings`).

### Open
- Real PBI incremental timing proof: needs Rahul's Microsoft device-code sign-in (fresh DB has zero stored tokens), then Reload twice and compare.
- Artifacts: port mockup `claude.ai/code/artifact/b762c2a9-4505-4ad0-802a-37beebe9031e`, PBI-fix mockup `claude.ai/code/artifact/e37ff482-4b4d-477f-b2d7-ae0cc70fd414`.
- ALL UNCOMMITTED (feature/* → dev flow; commit only on explicit ask).

## Session 2026-07-25 (pt23) — Local-folder UX arc + chat attachment chips (v0.0.485.2 → 0.0.485.6). BAKED, UNCOMMITTED

Post-port work on the paperclip/folder experience and per-message attachment visibility. Each step bumped VERSION + CHANGELOG (the version toast below depends on that).

### Folder add/remove from the browser (server + helper)
- `routes/local_runtime.py`: `POST /local-runtime/folders/request` (`{path?, pick}` — queues an `add_folder` job on the user's own paired+online runtime; `pick:true` = native folder dialog on the laptop), `GET /local-runtime/folders/request/{job_id}` (status poll), `DELETE /local-runtime/folders/{folder_name}` (queues `remove_folder`). Job kinds ride `payload.kind`; only the exec seam expires its own jobs, so pick jobs survive ~4-minute dialogs.
- `local-runtime/helper.py`: `config_folders()` (unions `folders` + legacy `allowed_folders` keys), `_pick_folder_native()` (osascript `choose folder` on mac / tkinter on win), `_handle_add_folder` / `_handle_remove_folder`, `post_folder_scan(..., allow_empty=True)` on the remove path (else removing the LAST folder leaves a server ghost). `helper_app.py` (rumps) gained "Add folder…"; `helper_app_win.py` publishes scans on allow-folder.
- ★An old stale frozen `CityAgent Helper.app` will steal jobs with outdated code — keep it closed until rebuilt.

### Paperclip menu = "Micro List" (FileUploadComponent.vue)
Final design after 3 clipping rounds: ~290px panel, Upload files button always TOP, one-line header (Folders + online dot + short device name), search input, single scrolling list (`max-h-40`) of ALL folders — no Show-more. Row: green folder icon when tables exist, name, meta (files · rows) or amber "no data files" pill, blue ✓ when attached, hover ✕ to remove. "Connect a folder…" → native pick; "or type a folder path" fallback. `UPopover :popper="{ placement: 'bottom-end' }"` — opens DOWN on the landing page, flips UP in chat via Popper.
- ★★Menu-clipping root cause was geometry, not CSS: the landing composer sits mid-screen, headroom above it < panel height, and static caps can't fix that — direction-aware placement + a small panel is the answer.

### Version-watch toast (`frontend/plugins/versionCheck.client.ts`)
SPA tabs never reload themselves (index no-cache, `/_nuxt/*` immutable, no service worker). Plugin polls `/api/changelog` every 60s + on visibilitychange; when `current_version` changes → persistent toast "A new version is available" with Reload action. ★Only fires if each bake bumps VERSION.

### Chat attachment chips (the 5-version saga — landmines matter)
Goal: every user message shows what it was asked against (green folder chips + blue file chips), sticky folders across turns/reloads.
- **Data**: `prompt.local_folders` (non-empty=attach, explicit `[]`=REAL detach — only sent when `folderDetachIntent` is set by an actual user detach; undefined=inherit via BE 40-turn walk-back). `prompt.attached_files` = display-only stamp of composer file names (documents are REPORT-scoped; only images ride `completion.files`). ★PromptSchema must DECLARE both fields (`completion_v2_schema.py`) or pydantic silently strips them.
- **Send paths**: `PromptBoxV2.vue` payload + `createReport` query (JSON-encoded `local_folders` + `attached_files`); report page first-message handler parses both, passes into `onSubmitCompletion`, and immediately seeds chips (`seedLocalFolders`); `loadCompletions` walks messages from the END for the last user turn with an array to re-seed after reload.
- **★★★THE renderer landmine (cost 2 wrong "fixes")**: the report page renders user bubbles with its OWN INLINE template (`group/usermsg` / `user-bubble` in `pages/reports/[id]/index.vue` ~L208) — `CompletionMessageComponent.vue` is NOT used for user messages there, so chips added to that component never appeared, live OR after reload. Real fix (0.0.485.6): chip block + `getAttachedFolderNames(m)` / `getAttachedDataFiles(m)` helpers built directly into the page's inline template (after the attached-images div). The optimistic-stub patch (0.0.485.5 — `onSubmitCompletion`'s local `userMsg.prompt` now spreads both fields) is still required: it feeds the live bubble.
- ★Sidebar "vX.Y.Z" is fetched at runtime from `/api/settings` (`plugins/settings.ts`) — it does NOT prove the tab runs a new bundle.

### Headless E2E pattern (proved the bug + the fix, no browser extension needed)
Playwright + Chromium are already in the image (PDF export). In-container script: `import main` (full ORM registry) → mint JWT (`get_jwt_strategy().write_token(user)`) → ★set cookie **`auth.token`** (sidebase-auth's real cookie name — the nuxt.config `cookie.name: auth_token` nested option is IGNORED; see the serialized config in `dist/index.html`) → drive `http://localhost:3000/reports/{id}?new_message=…&attached_files=…` → assert chip text in `document.body.innerText` + dump bubble `outerHTML`. Run with `docker exec -w /app/backend` (VERSION is read relative to cwd). Proof: live chips at t+1s AND after plain reload; test reports soft-deleted after.

### Versions
0.0.485.2 (menu fit + chips-in-component + sticky + version toast) · .3 (Micro List + bottom-end placement) · .4 (attached_files stamp) · .5 (optimistic stub carries fields) · .6 (chips in the REAL inline template — the fix that actually renders). README updated with Local Runtime + attachment-aware chat feature bullets.


## Session 2026-07-25 (pt24) — UPSTREAM v0.0.486 PORT. Merged + guarded + BAKED + SWAPPED + live-proven (shipped as `0.0.486.1`, see pt25), UNCOMMITTED

Phased port (gate between every phase) of upstream **v0.0.486** onto the fork. Rollback net: tag `pre-upstream-486`, `.bak-up486-20260725` on all 33 pre-existing files, tarballs `scratchpad/pre-upstream-486-worktree.tgz` + `~/Desktop/CityAI-Final-Project/_backups/pre-upstream-486-worktree.tgz` (both 86,601,190 bytes).

### Method — classify by byte-compare, then PROVE the merge
- 37-file surface split by `filecmp.cmp(shallow=False)` against a clean `v0.0.485` reference: **12 CLEAN** (worktree == 485 → wholesale-safe), **21 FORKED** (hand-merge), **7 NEW**.
- ★★★**Delta-equivalence proof**: a hand-merge is correct iff `difflib.unified_diff(our.bak → our merged)` add/remove line SETS equal `unified_diff(base485 → new486)` sets. All 9 non-locale forked files matched exactly (agent_v2 171/4 · llm_service 95/0 · AddConnectionModal 56/6 · organization_settings_service 27/0 · reports/[id]/index.vue 19/1 · schema 5/0 · license 1/0 · prompt_builder_v3 1/1 · PromptBoxV2 2/2) → nothing extra added, nothing of ours removed. Use this instead of eyeballing diffs.
- Locales: position-aware additive merge (`object_pairs_hook=OrderedDict`, insert after upstream predecessor, never overwrite), then proved up+N == ours+N / changed=0 / lost=0. ★`locales/en.json` has NO trailing newline; the other 9 DO.
- Guards run: 486 touches none of our 12 landmine files · all 12 CLEAN `.bak` == 485 (Phase 1 lost zero fork work) · container py3.12 parse 782/782 clean · no conflict markers/`.orig`/`.rej` · 22-item fork-marker sweep all present.

### What landed
- **LLM fallback chain** — NEW `app/ai/llm/fallback.py` (`MAX_FALLBACK_CHAIN=10`, `CircuitBreaker`/`breaker`, `get_fallback_order`, `resolve_fallback_chain`, `FallbackController`). EE feature `llm_fallback` — ★our `ee/license.py` grant is DYNAMIC (`list(TIER_FEATURES["enterprise"])`) so adding the string to that list is the whole change. Org settings: `llm_fallback` FeatureConfig (is_lab, off by default) + bare `llm_fallback_order: list` (402 enterprise guard + 400 list-shape guard in `organization_settings_service.py`). `llm_service.get_fallback_order` / `set_fallback_order` (dedupe preserving order, cap at MAX, validate ids against the org's non-deleted models, ★reassign `settings.config = cfg` or SQLAlchemy misses the JSON mutation, audit `llm.fallback_order_set`).
  `agent_v2.py`: routing candidates filtered by open breakers; `_apply_routed_model` → `_apply_effective_model(model, cause="routing"|"fallback")`; `_setup_llm_fallback()` right after `_setup_model_routing()`; 99-line engagement block before `if llm_err_payload:` that swaps the model, persists a `route_model` tool execution (`tool_action="fallback"`) + standalone block "Model fallback" 🔁, emits `block.upsert` + **`llm.fallback` SSE**, then `invalid_retry_count=0; observation=None; break`. FE: `case 'llm.fallback'` in `pages/reports/[id]/index.vue` (★file is TAB-indented; match `'\t\t\t} catch (e) {'`).
- **`read_artifact` windowing** — long artifacts return a line-numbered OUTLINE; agent follows with `offset`/`limit` or `grep_pattern` (+`before`/`after`). Planner bullet updated in `prompt_builder_v3.py`.
- **Tableau incremental reindex** — `prior_tables`, same contract as the PBI 485 work.
- **`AddConnectionModal.vue` discard guard** — `@input.capture`/`@change.capture` set `formDirty`; `isOpen` setter intercepts close while `step==='form' && formDirty`; second sibling `UModal showDiscardConfirm`; `data.discard*` ×4 in all 10 locales. ★Our fork's `goToUpload()` deliberately stays on `isOpen.value=false` (select-step only, never dirty).
- NVIDIA branding icons; `docs/design/llm-fallback.md`.
- ★Upstream's own CHANGELOG documents ONLY the Tableau bullet — fallback / windowing / discard-guard ship undocumented upstream, so OUR entry documents all four.

### Landmines (new this port)
- ★★**Host python is 3.9.6, container is 3.12.3** → host `ast.parse` FALSE-FLAGS f-string-with-backslash and nested-quote files (5 hits, all legal in 3.12). Only the container parse is authoritative.
- ★★`docker exec … python3 - <<'PY'` needs **`-i`** or it silently emits nothing; `rm -rf` inside the container needs **`-u root`**.
- ★**`.vue` tag-balance regex is noise** — `=>` arrows inside attributes contain `>` and break self-closing detection. Compare NET balance CHANGE (ours vs `.bak`) against (486 vs 485) so the noise cancels.
- ★Conflict-marker sweep flags `edit_artifact.py` — legitimate SEARCH/REPLACE prompt-template text (L514-518, L582-586), not a merge conflict.
- ★**`pytest tests/` here = 2,982 tests / 125 files** (whole upstream suite), NOT the "139" of the 485 port (that was 7 fork-relevant suites). `-q` through `docker exec` buffers → no progress output until exit.
- ★Locales are NOT a runtime dir in the image (`/app/locales` absent) — i18n compiles into `frontend/dist/_nuxt`; verify merges by grepping dist chunks (`fallbackOrder`, `Fell back to`).
- ★Pre-existing upstream bug, deliberately NOT fixed: `routes/llm.py` calls `llm_service.create_model`/`update_model`/`delete_model`, which `llm_service.py` never defines — true in 485 AND 486 (those 3 admin endpoints 500 upstream too).
- ★Path with brackets: quote it literally (`"$R/frontend/pages/reports/[id]/index.vue"`) — backslash-escaping the brackets breaks the path.
- ★★★**Delta-equivalence proves line SETS, not PLACEMENT.** Close the gap with an in-situ byte-compare of the merged hunk: extract it by its own delimiters from both files and diff. Did this for the fallback block — `_fb_model = None` → trailing `break`, upstream 3492..3582 vs ours 3557..3647, both 91 lines, **byte-identical**, plus 6 matching lines of enclosing context.
- ★★★**Upstream's test suite cannot validate this fork.** 259 test files in the worktree, 259 in upstream v0.0.486, exactly **1** fork-added (`backend/tests/unit/test_powerbi_mt_incremental.py`). A full `pytest tests/` run is ~3h and ~150 failures with no baseline to read them against — abandoned deliberately. 486's own 4 new suites (54 tests, 79s) ARE worth running; everything fork-specific is hand-verified.
- ★★**Rebuilding over the same image tag DELETES the old image.** `compose build` re-points `cityagentinsights:local` and containerd collects the orphaned parents — `132c6f610ae0` and `d11d22bfb799` are both GONE, so instant image rollback is no longer possible. **Tag before every rebuild**: `docker tag cityagentinsights:local cityagentinsights:<version>` (Analytics already does this with its `pre-*` tags).
- ★★`breaker = CircuitBreaker()` is a module-level global (`fallback.py:107`) and the app runs **6 workers** → threshold/cooldown are per-worker, not shared. Same flaw class as the `learn_progress` in-memory dict (pt19).
- ★★`app/ai/llm/errors.py` `classify()` does NOT recognize `"Connection error."` — it matches only connect/timeout class names, or the raw strings "connection refused" / "name or service not known" / "timed out" / "tls handshake". A dead endpoint with no HTTP status classifies `unknown` → not in `FALLBACK_ELIGIBLE_CODES` → **no fallback**. File is byte-identical to upstream and outside 486's surface, so the gap is upstream's — but the likeliest real outage is exactly the one the chain skips.
- ★Report soft-delete is **`status='archived'`**, not `deleted_at`. Deleting completions directly hits FK `agent_executions_completion_id_fkey` and rolls back the whole `psql -c` transaction — use `DELETE /api/reports/{id}`.
- ★Completion-body `model_id` is silently ignored — pin a model with `PUT /api/reports/{id}` `{"model_id": …}`.
- ★`llm_fallback` is a **top-level** field on `OrganizationSettingsConfig`, sibling to `ai_features` — writing it inside `ai_features` 200s and no-ops.
- ★**Playwright in-container must target `http://localhost:3000`** — 8095 is the host mapping; inside the container it's `ERR_CONNECTION_REFUSED`.
- ★`pgrep -af <pat>` / `pkill -f <pat>` inside `bash -c` **self-match the wrapper's own command line** (false "still running"; a chained `rm` after a self-killing `pkill` never runs). Also **rtk truncates `ps` output** — use `subprocess.run(['/bin/ps','-ww','-o','args=','-p',PID])`.
- ★Helper scripts must live in **`/app/backend/`** (not `/tmp`) or `import main` fails — python puts the *script's* dir on `sys.path`, not cwd.
- ★f-string-with-backslash is a hard SyntaxError on the host's py3.9 — write helper scripts to a FILE instead of `python3 -c` with escapes.

### State (pt24 as merged)
- 486's 4 new suites: **54 passed / 0 failed** (79s).
- Image built with `--build-arg FE_CACHEBUST=$(date +%s)` and verified INSIDE the image (VERSION, changelog header, 6 backend markers, FE dist strings) before any swap. Keep doing that.
- Superseded by **pt25** — see below for the live proof, the fork bug it uncovered, and the shipped version.

## Session 2026-07-25 (pt25) — 486 live-proven + `0.0.486.1`: the fork bug that broke EVERY Power BI query. LIVE image `0d568a5a04c4`, UNCOMMITTED

Live on :8095, VERSION `0.0.486.1`, image tagged BOTH `cityagentinsights:local` and `cityagentinsights:0.0.486.1`. Source == image == container; nothing `docker cp`'d.

### ★★★ THE BUG — two credential resolvers, delegated mint only on one
Every Power BI question failed with `AADSTS7000216: 'client_assertion', 'client_secret' or 'request' is required for the 'client_credentials' grant type`, while per-user sign-in succeeded and the dataset catalog loaded fine — so it read as a config problem, not code. Pre-existing in OUR per-user PBI work (pt18/pt2x), **not** a 486 regression: `data_source_service.py` is not in 486's 37-file surface.

| resolver | returns | `access_token`? |
|---|---|---|
| `DataSourceService.resolve_credentials` (`data_source_service.py:2434`, powerbi_user mint at **:2504**) | `{access_token, tenant_id}` | **True** |
| `DataSourceService.resolve_credentials_for_connection` (**:2994**) — what the query path uses | raw blob `{auth_mode, refresh_token, tenant_id, tenant_tokens, tenants, username}` | **False** |

Chain: `construct_clients` (**:2797**) → `resolve_credentials_for_connection` at **:2887** → legacy DS-scoped short-circuit at **:3013-3015** returns the raw blob → `PowerBIClient` built tokenless → `powerbi_client.py:170` delegated branch skipped → **:177** `grant_type: client_credentials` with no secret → 401.

★**`fabric_user` was immune only by accident of branch ORDER** — `construct_clients` has a dedicated `fabric_user` federated branch at **:2858** (`MsFabricFederatedClient`, mints per-tenant SQL tokens on demand) that `continue`s before the generic path, and **:2876** hard-skips `fabric_user` rather than let it fall through. `powerbi_user` had no such protection. The `_for_connection` resolver is equally wrong for Fabric; Fabric just never reaches it.

**Fix** (1 logic line + comment, `data_source_service.py:3022`, bak `.bak-pbiuserauth-20260725`) — guard the legacy short-circuit so delegated types fall through to `ConnectionService.resolve_credentials` (`connection_service.py:1461`), **which already had the correct minting branch, written for exactly this, and was simply never called**:
```python
if getattr(connection, "type", None) not in ("fabric_user", "powerbi_user"):
    ...existing legacy blob short-circuit, unchanged...
```
**Proof:** resolver → `['access_token','tenant_id']`, `PowerBIClient.has_access_token=True`, `create_data ✓`. Live: PBI `Promotion_test/Promotion` = **88,878 rows** (69,826 distinct PROMOTION_CODE). Fabric unchanged at **571,482** (`DL_POC.dbo.Fct_Transactions`), federated client `endpoints=4 (per-user isolated)`.
★★**Debug method that nailed it in one shot:** a script printing BOTH resolvers' key sets + `has_access_token` side by side. Do that before tracing call chains — it turned a guessing game into a one-line fix.

### Phase A — the 4 upstream 486 features, live
`llm_fallback` off by default (`GET /api/llm/fallback_order` → `{"enabled":false,"order":[]}`); order POST dedupes, **400**s unknown ids, **422**s a non-list; enabling it leaves `model_routing` untouched (routing and fallback are independent). Run pinned to a dud model pointed at a local 429 endpoint → switched to `x-ai/grok-4.5`, answered correctly, persisted `route_model` with `cause=fallback` / `code=rate_limit` / `provider_message`, rendered a "Model fallback" 🔁 block, `block.upsert` captured on the wire.
Not proven: 10-model cap (org has only 2 models), the `llm.fallback` SSE frame itself (fires ~3s in, before a listener attaches), the 402 enterprise guard (our license grants the feature), Tableau incremental (no creds), `read_artifact` windowing live (its suite passed).

### Phase B — regression sweep, 22 PASS / 0 port regressions
Version + boot + `v0.0.486` in the sidebar · chips live AND after reload · helper alive/paired/heartbeat current · folder publish (schema only: 1 table 3 rows typed cols + 1 doc) · **local doc read on the laptop** (`📄 read_document: b3folder/AA-Medical-Program.pdf → 10468 chars`, 0.7s; second job totalled the CSV locally = 93,500, correct) · City Mart Retail 11/11 tables with real counts + widget · Fabric **54** tables (DL_POC 29 / LK_CFC_Sales 15 / DL_POC_Toey 8 / CFC_Lakehouse 2) · PBI **6** (CLI Dashboard ×4, Promotion_test ×2) · **discard guard fired on a real click** ("Discard changes? … Keep editing / Discard") · clean close from the select step (never dirty).
★Method: **Playwright is already in the image** (`~/.cache/ms-playwright`, chromium-1223) — no browser extension needed. Cookie `auth.token`, target `http://localhost:3000`.

### ★New landmines (pt25)
- ★★★**`local_folders` must be INSIDE `prompt`** — `{"prompt": {...}, "local_folders": ["x"]}` at top level is silently dropped (stored `prompt.local_folders = null`) and the agent then invents a folder name from the tool-schema example ('AA-Medical'). Correct: `{"prompt": {"content": ..., "local_folders": ["x"]}}`.
- ★★**The CLI helper reads its shared-folder list ONCE at startup** (`local-runtime/helper.py:664`, before the poll loop) — config edits need a restart. The tray app is fine (`add_folder` → `remember_folder` → immediate `_publish_folders`; loop uses in-memory `self.folders`). The hazard is the mix: tray adds a folder, the CLI helper serves the job and refuses it.
- ★★**Un-sharing does NOT clear the server's published schema** — restarting the helper without `--allow-folder` leaves `folders_schema` still advertising the folder. `DELETE /api/local-runtime/folders/{name}` queues a helper job that does clear it (verified → 0 folders). **FOUND, NOT FIXED.**
- ★`user_data_source_credentials` expiry column is **`expires_at`**, not `token_expires_at` (NULL under the refresh-token model).
- ★`/api/settings` is **public by design** (needs_setup, auth mode, full feature-flag list) while `/api/reports` and `/api/organizations` correctly 401.
- ★`.bak-*` files are baked into the image (`/app/backend/...bak-up485-...`). `.gitignore:82` keeps them out of git but not out of the image.
- ★Open cosmetic: `/agents` badges City Mart Retail **"8"** while all **11** catalog tables are active. Agent used all 11 correctly; off 486's surface, not chased.

### Changelog audit (do this every release)
Cross-checked all **93** `.bak-*` work-batch tags against the changelog's 287 entries. Two shipped-but-undocumented changes found and added to `0.0.485.7`: per-OS helper download cards (`lrwin` — the settings page and "Windows" strings ARE in the shipped dist bundle) and the `_LOCAL_KEY_RE` folder-reference fix (`lrfix1` — a bare `local:` inside echoed error text was parsed as a real folder selection). Now **287 entries / 251 bullets**, all baked. ★Bugs found-but-not-fixed stay OUT of the changelog.

### State / next
- **UNCOMMITTED: 118 files**, of which only **21** are 486's — the rest is older uncommitted work (485 port, local runtime, per-user tables, learn progress). ★So `git reset --hard pre-upstream-486` is **NOT** a 486-only rollback; it would destroy all of it. Git provides no usable rollback here.
- Rollback net = `.bak-up486-20260725` (21 files) + `.bak-pbiuserauth-20260725` + two tarballs (86,601,190 bytes each, in `scratchpad/` and `~/Desktop/CityAI-Final-Project/_backups/`). **No image rollback** — see the tag landmine in pt24.
- Helper runs **from source** (`python3 -u helper.py run`, cwd `local-runtime`), not the frozen `.app` (`rumps` missing on the CLT python) — it dies with the shell that launched it.
- Next: PBI **incremental timing** proof (unblocked by this fix, never measured) · the 5 Phase-A leftovers · helper `.app` rebuild (`pip3 install rumps pyinstaller`) · Windows `.exe` (needs a Win box) · fix the two local-folder landmines above · **commit** (Rahul has explicitly declined so far).

## Session 2026-07-25 (pt26) — CODEGEN TRIM BUG + retry breaker + truthful errors + helper .app rebuild. SHIPPED `0.0.486.2`, image `3678c6dd2b50`, UNCOMMITTED

Live :8095, VERSION `0.0.486.2`, tagged BOTH `cityagentinsights:local` and `:0.0.486.2`. Rollback image `0d568a5a04c4` alive under `:0.0.486.1` + `:pre-0.0.486.1`. Repo tarball `_backups/pre-p1p3-worktree-20260725.tgz` (338,932,882 bytes, sha `a4a6091ae565ce30…`, restore-tested). Backups `*.bak-p1p3-20260725` (coder.py, code_execution.py, VERSION, CHANGELOG.md, CLAUDE.md) + `helper.py.bak-happ020-20260725`.

### ★★★ THE BUG — our own trim amputated the model's code (Power BI analyses failed at random)
`coder.py` post-processed every generation with `re.sub(r'(?s)return\s+df.*$', 'return df', result)`. `(?s)` + `re.sub` matches the **FIRST** `return df` in the text — normally the last line of a nested helper (`def clean_cols(df): … return df`) — so **everything after it was deleted**, including the queries and the real return. The run then failed "Code executed but returned None or an empty DataFrame (0 columns)".
- ★**Success was a coin flip on a variable name.** Proven from two stored runs in the same report: attempt 1 helper ended `return df` → 871 chars, failed; attempt 2 helper ended `return out` → 9,638 chars, succeeded. Same logic, one rename. "It works on the 2nd or 3rd try" was luck, not recovery.
- ★**Scan: 5 of 8 failed code samples in the last 44 tool runs carried the fingerprint** (ends at a `return df` indented >4).
- ★★**The regex lived in TWO places, and grep found only one** — the comment `# Remove any code after return df` sits above the `data_model_to_code` copy (used by **create_widget**), while the copy inside `generate_code` (used by **create_data** — the actually-failing tool) has no comment. Fixing only the grep hit would have missed the reported failure entirely. `generate_inspection_code` has **no** such scrub (so `inspect_data` failures are a DIFFERENT, still-unexplained cause — its code ends cleanly at a top-level `return out`).
- **Fix** `_trim_after_function()`: parse, cut at the first top-level function's `end_lineno`. Never inspects the body, so no naming convention can affect it. ★A first cut using only `fn.end_lineno` was **insufficient**: real trailing chatter is PROSE ("Here is the function that…"), which makes the WHOLE output unparseable → fell back to the very regex being replaced. Final design retries on prefixes ending at column-0 statements and uses the first that parses into a complete function; candidate cuts are **verified by an actual parse**, which is what makes multi-line SQL strings (continuation lines in column 0) safe. `_legacy_trim_after_return_df` kept as the last-resort fallback only.

### Identical-failure breaker (`code_execution.py`)
`_repeated_identical_failure(code_and_error_messages)` — last two attempts identical in BOTH code and error (whitespace-normalized) → stop, emit stage `stopped_identical_failure` + `IDENTICAL_FAILURE_NOTICE`. Deliberately strict (same error + different code may be converging). ★Placed as ONE check at the top of each while-loop — every failure path already appends `(code, error)` and `continue`s, so one insertion covers all six branches and the local-runtime seam is provably untouched (`execute_code_async`→`get_df_info` region byte-identical). Org here has `limit_code_retries=3`, so it saves ~1 attempt per stuck run; the real value is that an identical repeat now *announces* the fault is probably not in the generated code.

### Truthful retry message
The empty-DataFrame nudge always said "Verify you are using the EXACT TABLE name in the FROM clause" — even when no query ran, or when queries had already succeeded and only the result was lost. Now branches on `executed_queries`: none → "No query was executed" (+ available tables); some → "N queries executed successfully, so the connection, the client_key and the SQL are all fine … make sure the final `return` is the last statement of generate_df — a `return` inside a nested helper does not return from generate_df". ★I had planned "query ran, 0 rows, check filters" for branch 2 — **wrong**: this branch only fires on 0 COLUMNS; a 0-row result still has columns and never reaches it.

### ★★Local runs reported zero queries (found by the Phase-O sweep, not by unit tests)
`execute_code_async` RETURNS the remote tuple and never fills the in-place `captured_queries` list, and the loop discarded the third element as `_`. So `executed_queries` was empty for **every** local-runtime execution → the new message would have falsely claimed "No query was executed", and the done payload's query list was empty. Fix: `exec_df, execution_log, _returned_queries = …` then extend when `_returned_queries is not executed_queries` — ★the identity check is required because the **server** path returns that same list object (extending it with itself would double every query). Note the helper only captures `RemoteClientProxy` SQL (`helper.py:622-626`), so folder-only DuckDB queries still report `[]` — known, unfixed.

### Helper `.app` rebuilt → `CityAgentHelper-mac.zip` 77,822,476 bytes (was 76,627,284)
brew py3.12 venv (pyobjc/rumps compiled fine — the CLT-3.9 blocker did not apply). ★**`pypdf` added**: `helper.py:_extract_pdf_text` imports it *optionally*, so without it the shipped app silently loses local PDF reading. ★**`--osx-bundle-identifier io.cityagent.localhelper`** — macOS keys Files-and-Folders grants to the bundle ID; a new ID silently revokes users' folder access. ★**`HELPER_VERSION` 0.1.0→0.2.0**: the server persists it and refreshes it on every folder post, and it is the ONLY runtime discriminator between builds. It immediately earned its keep — a from-source CLI helper (started 20:09, i.e. AFTER the Phase-D edit, so it carried the same fixes) was still alive and racing the frozen app for jobs; provenance can't tell them apart (same runtime_id/token/device name), so the first round of proofs was unattributable until it was killed by PID. Both Phase-D folder fixes then proven with the frozen app as sole helper: config-added folder adopted in ~16-24s, un-share published `[]` and cleared the server ghost. `ditto -c -k --sequesterRsrc --keepParent`; signature survives the round-trip (adhoc, **arm64 only** — no Intel build).

### Landmines (new)
- ★★A regex that looks correct in isolation can be wrong on what the model actually writes. Both the amputation AND my first fix failed only against REAL stored generations — synthetic samples passed.
- ★`sed -n '/async def X/,/async def Y/p'` silently runs to EOF when Y is not `async` (`def get_df_info`) — a clean region then looks modified. Verify the boundary matches.
- ★Provenance lives in `tool_executions.result_json` (`jsonb_path_query_first($.**.execution_provenance)`), NOT in the completion JSON blocks.
- ★`tool_executions` has no `completion_id` — join via `agent_executions`.
- ★`/api/changelog` shape is `{current_version, available, versions[{version,date,entries[]}]}` — not `entries` at the top level.
- ★A folder question asked in the ~24s window before the scan reaches the planner is refused with "Helper has stopped sharing it". Harness race, not a product bug — wait for `folders_schema` then settle.
- ★`frontend/node_modules` DOES exist (987M) — the earlier "not present locally" note is stale. Excluded from the backup tarball (regenerable from yarn.lock).

### Verification
L 21/21 · M 19/19 · N 15/15 · remote-fold 5/5 · 486 suites 68 passed · suites importing the changed modules 84 passed (coder_time_context, sandbox_feedback_loop, db_error_hints, query_timeout, concurrent_tool_dispatch, usage_metering_buffer). Live sweep on the shipped image: PBI promotion (was 4 failed attempts → **0**), PBI CLI expense+training (was fail-then-luck → **0**), City Mart, Fabric — all `attempts_failed=0`, no amputation fingerprint; folder E2E `1,072,452,257` exact vs raw CSV with `executed_on: local`. Volumes all 8 intact across the swap; migration head `ca07lrfolders01`; source == image == container.

### Open
`inspect_data` intermittent failure (unrelated, no recorded errors, appeared in 2 of 3 sweeps) · folder-only runs still report `queries: []` · `classify()` still doesn't recognise `"Connection error."` so model fallback never fires for a dead endpoint (asked twice, not authorized) · `llm_fallback` toggle still ON from Phase-A testing · Windows `.exe` still hardware-blocked · **UNCOMMITTED** (now ~124 files).

---

## pt27 — `0.0.486.3`: model fallback for dead endpoints + folder query capture (2026-07-25)

Shipped image `59772c065a22` (`cityagentinsights:local` + `:0.0.486.3`). Rollback `3678c6dd2b50` under `:0.0.486.2` + `:pre-0.0.486.3`. Baks `*.bak-q-20260725` (errors.py, planner_v3.py, agent_v2.py), `*.bak-r-20260725` (code_execution.py, helper.py), `*.bak-qr-20260725` (VERSION, CHANGELOG.md, CLAUDE.md).

### Q — a dead endpoint never triggered model fallback
Not a hole in `classify()` — it handles connection errors fine **when it holds the real exception**. The information was destroyed upstream:
- `planner_v3.py:345` stored only `PlannerError(message=str(exc))`; `agent_v2.py:3542` then rebuilds a **bare `Exception(err_msg)`**, so `type(exc).__name__` is `"exception"` and the class-name heuristic is dead at the only call site that drives fallback.
- ★★★**OpenAI, Anthropic and Azure all stringify every transport failure to exactly `"Connection error."`** — closed port, bad DNS, dead TLS, indistinguishable. Verified against the real SDKs in-container. The class name is the ONLY signal, which is why preserving it matters more than any wording match.
- Fix: `classify(..., exc_type=...)` override + wider transport-token list; `planner_v3` records `details={"exc_type": type(exc).__name__}` (the `details` field already existed — no schema change); `agent_v2` passes it through.
- Before/after on the real string: `unknown` (not retryable, not fallback-eligible) → `network` (both).
- ★Both mechanisms proven **independently**: the text token alone would have masked a typo in the wire, so the wire was tested with an empty message (`network`) and with the wire cut (`unknown`).

### R — folder runs reported no queries, so the retry advice lied
- `helper.py` wrapped only `RemoteClientProxy`; `LocalFolderClient` (DuckDB over the shared folder) went in raw at the `ds_clients[f"local:{p.name}"]` assignment.
- Consequence: a folder run that lost its return value was told *"No query was executed … you MUST call `ds_clients[...]`"* **plus `Available tables:` from a completely unrelated connector**, steering the next attempt away from the folder.
- ★★`LocalFolderClient` aliases `query = execute_query` as a **class attribute**, binding the original function at class-creation. Overriding `execute_query` alone leaves `query()` uncaptured — and it doesn't just skip the capture, it skips every override in the MRO and calls the ORIGINAL method. Generated code uses both spellings. `capturing(cls)` overrides both.
- ★R1 first probe never reached the bad branch: with no device context a *correct* guard fires first ("lives on your computer and this run has no device context"). The bad path needs the helper to have actually run. Reproduced by standing in for a completed helper job returning `(None, log, [])`.
- ★Because R3 alone flips it to the correct "queries succeeded → your return is missing" branch, the planned third message branch was **not** needed — R4 shrank to one guard: never offer `ds_clients` table names to folder-targeting code.
- `HELPER_VERSION` 0.3.0; zip 77,823,033 bytes (was 77,822,476), sha `36ea8510132c9e75`.

### Landmines (new)
- ★★★`model_id` for a completion goes **inside `prompt`** (`PromptSchema.model_id`). Top-level is silently ignored and the run uses the org default — a pinned-model test then reads as a pass while proving nothing.
- ★`spec_from_file_location` returns `None` for a `.bak-*` filename (unrecognised suffix); copy to a `.py` name to load a backup. Under py3.9 also register it in `sys.modules` first or `dataclass` blows up resolving types.
- ★`find` cannot see pure-Python packages in a PyInstaller bundle — they live in the PYZ inside the executable, and `strings` on the compressed archive shows nothing either. Check `build/*/PYZ-00.toc` instead. (pypdf: 102 entries, present.)
- ★Two pytest runs against the same DB at once manufacture failures. Serialise. Long runs also outlive the harness's background tasks — use `docker exec -d ... > /tmp/x.log` and poll.
- ★The old helper ran from a scratchpad path and kept polling invisibly (`ps aux | grep -i cityagent` missed it; the full `ps -Ao pid,comm` found it). Always kill the previous helper **by PID** before attributing any proof.

### Verification
Q 28/28 · R3 11/11 · R4 13/13 · N 15/15 · M 19/19 · O-fold 5/5 · suites importing the changed modules **200 passed, 0 failed**.
Full `tests/unit`: **23 failed, 1898 passed** — the same 23 fail **byte-identically on the pre-change code** (verified by reverting all four files in-container and re-running the 9 files): oauth/fabric, member email, eval draft helpers, license limits, new_report_command, notification service, permissions registry, session staleness, whatsapp webhook. **Pre-existing, none in the changed modules.**
Live on the shipped image: fallback `code=network`, "Fell back to x-ai/grok-4.5 — dead/model-a unavailable", answer served; folder run `executed_on: local` with `executed_queries: ["SELECT SUM(revenue) AS total_revenue FROM b3_sales"]` and the exact `93,500` (every earlier folder-targeting local run recorded `[]`). Volumes 110 before and after; migration head `ca07lrfolders01`; source == image == container on all four files; helper zip served at `bytes 0-0/77823033`; Windows zip still correctly absent. 2 tracebacks in 25m, both pre-existing OpenTelemetry `Failed to detach context`, 0 app frames.

### Open
`inspect_data` intermittent failure (Phase S, unstarted) · Windows `.exe` hardware-blocked (needs a CI runner, which needs the repo pushed) · P5 learning system (connector recipes + `prev_data_model_code_pair` reuse) unstarted · 23 pre-existing unit failures unowned · **UNCOMMITTED** (~130 files).

---

## pt28 — `0.0.486.4`: `inspect_data` reported failure on runs that succeeded (2026-07-26)

Shipped image `57c0a39ca540` (`:local` + `:0.0.486.4`). Rollback `59772c065a22` under `:0.0.486.3` + `:pre-0.0.486.4`. Baks `*.bak-s-20260726`.

### ★★★ Not intermittent — deterministic, and it had a ~47% hit rate
The stored evidence contradicted itself: `status='error'`, `error_message = "No query was executed…"`, yet the stored `execution_log` showed **four Power BI tables previewed with real rows** and the stored `code` was clean and complete.

Cause: `generate_and_execute_stream_v2` emits `"errors": code_and_error_messages` — the **history of every attempt** — and it does so on the **success path too** (both loops, line ~1440 / ~1756). Three consumers treated a non-empty history as the outcome:
- `app/ai/tools/implementations/inspect_data.py:290` · `app/ai/tools/mcp/inspect_data.py:256` · `app/ai/tools/implementations/write_csv.py:220` — all `if payload.get("errors"): success = False`.
- So **any run that failed once and then succeeded was filed as a failure**, storing the *discarded* first attempt's error as the reason next to the *successful* attempt's code and log. Tally before the fix: 10 success / 9 error.
- ★`create_data` was already correct — it gates on `generated_code is None or exec_df is None` and uses `errors` only for a telemetry count. That asymmetry is why only `inspect_data` looked flaky.

Fix: every one of the six `done` payloads now carries an explicit `"executed_successfully"` (sigkill → False, `df: None` → False, `df: exec_df` → True); the three consumers read it, falling back to the old test only when the key is absent. `errors` is retained as diagnostic history.

### Landmines (new)
- ★★★An accumulated error list emitted on the success path is not an outcome. If a payload carries both "what went wrong along the way" and "how it ended", say how it ended **explicitly** — never let a consumer infer it from the presence of history.
- ★When stored evidence disagrees with itself (`status=error` + a log full of successful output), the bug is in the **reporting**, not the execution. Read the log before believing the status.
- ★"Intermittent, 2 of 3 sweeps" was a symptom of *whether a retry happened*, not of any real nondeterminism. A failure rate near 50% with no error in the log should point at outcome-reporting, not flakiness.

### Verification
S-repro 5/6 (the 6th was the deliberate pre-fix assertion) → S-suite **22/22** after: recovered run → success, clean run → success, all-attempts-failed → still failure, identical-failure breaker → still failure, all six payload flags matched against their own `df`, all three consumers confirmed changed, `create_data` confirmed untouched. Regressions M 19/19 · N 15/15 · O-fold 5/5 · R4 13/13.
Live: 13 `inspect_data` runs after the fix, **all success, zero error** (before: 10/9), including two whose log shows a table that failed to resolve inside the generated code. Post-swap `0.0.486.4` served, 290 versions, migration head `ca07lrfolders01`, volumes 110 → 110, helper zip still `77823033`, helper 0.3.0 paired, 0 tracebacks.
★ Not claimed: the stored data does not record whether a retry occurred in any individual live run (the error history isn't persisted), so the live evidence is the disappearance of the failure shape — the mechanism itself is proven by the deterministic harness.

### Open
Windows `.exe` hardware-blocked · P5 learning system unstarted · 23 pre-existing `tests/unit` failures unowned · **UNCOMMITTED**.

---

## pt29 — `0.0.486.5` → `0.0.486.8`: settings UI, Local Runtime switches, MCP name (2026-07-26)

Four releases, each with its own rollback tag (`pre-0.0.486.5` … `pre-0.0.486.8`). Baks `*.bak-p1-`, `*.bak-tabs-`, `*.bak-lrtoggle-`, `*.bak-lrsuper-`, `*.bak-mcpname-20260726`. Migration head **`ca08lrtoggleoff`**.

### ★★★ Never write an org setting with raw SQL
`FeatureConfig` requires **`description`** (no default). A `jsonb_set` that omits it bypasses validation and every subsequent `GET /api/organization/settings` dies on `ResponseValidationError` → 500 → the AI Settings page renders empty with "Failed to fetch settings". I did exactly this to the live instance from a gate test. Recovery: `config::jsonb - 'local_runtime'` — the schema default refills it. **Drive settings through the validating API, even in tests**, and assert the stored object is a complete FeatureConfig afterwards.

### `.5` — folder table names with spaces
`"AWS Console Login events.csv"` was queried as `AWS`; DuckDB reads an unquoted space as the end of the identifier (3 real failures). Rule added to `local_folders_context.py`, **not** the instruction system — that renderer only runs when folders are attached, so it is scoped by construction. ★Folders have no `data_sources` row, so an instruction could not have been scoped and would have gone **org-global**.

### `.6` — the settings tab row
★The overflow was **3px**: 12 tabs at `space-x-8` need 1251px, `max-w-7xl` minus padding gives 1248px — which is why exactly one tab clipped, and it was the ACTIVE one. Fix: `flex space-x-8` → `flex gap-x-6` + three shorter labels (People, PII, Identity) = 927px, 321px headroom. ★The page `"title"` keys keep the FULL name — short tab, full heading. Wrap and `overflow-x-auto` were both rejected: guidance rates "content fits the viewport" High severity, and a hidden settings tab is a destination nobody finds. es/he don't define these keys and fall back to en — don't guess translations.

### `.7` / `.8` — two different Local Runtime switches
- **Per device**: `local_runtimes.run_local_enabled` was `default=True, server_default="true"`, so a freshly paired laptop began executing agent Python before the user had seen the toggle. Now False, migration `ca08lrtoggleoff`. ★Existing rows are deliberately NOT flipped — an upgrade must not silently disable what someone opted into.
- **Per org (admin)**: new `local_runtime: FeatureConfig` in `organization_settings_schema.py`. Admin-only *by construction* — settings write needs `manage_settings`, which `admin` holds via the `full_admin_access` wildcard and `member` does not, so no new permission was needed. Gates: `local_runtime_exec._org_allows_local_runtime()` (the one that actually stops execution) and `/local-runtime/status` via `_org_gate`. ★Unset = ON and a lookup failure = ON, so it degrades to today's behaviour rather than silently moving everyone's work to the server. ★**Off must never delete pairings** — verified 2 devices survive off → on.
- ★`get_frontend_settings()` in `bow_settings.py` is **unauthenticated** and has no org context, so per-org gating cannot live there; the UI gates on the status endpoint returning 403.

### `.8` — MCP name
The copy-paste config carried the inherited upstream server name, so the server appeared in the user's MCP client under the upstream project's name. Renamed to `cityagent-insights` in `UserProfileModal.vue` and `McpModal.vue` **only**. ★The server `name` and the `ui://…/visualization` resource URI in `backend/app/routes/mcp.py` are deliberately untouched: they are protocol **identifiers** matched by string on both ends — renaming them without the client side in the same release breaks connected clients.

### Landmines (new)
- ★★★Raw SQL into a validated settings blob breaks the whole settings surface, not just the key you wrote.
- ★`create_widget` has **0 executions ever** in this deployment — the v1 executor loop is dead code here.
- ★When deleting a parameter, grep `tests/` as well as `app/`: removing `prev_data_model_code_pair` broke `test_coder_time_context`.
- ★`FeatureConfig` fields: `value, name, description(required), is_lab, editable, state`.

### Verification
Admin switch driven through the real settings API: 7/7 — off → `/local-runtime/status` 403, settings still fetch 200, pairings intact, on → 200, stored entry a complete FeatureConfig. Tab fix + label renames verified in the **built bundle** (`peopleTab":"People"`, `mb-px flex gap-x-6`), not the source. `tests/unit` **1921 passed / 0 failed**. Volumes 110 → 110 across every swap.

### Open
Local Runtime UI still lives in an org Settings tab; the agreed design moves it into the personal **Admin ▸ profile modal** beside API Keys / MCP Server, hidden on 403 (~183-line page to port, then drop the tab). · Super-admin control of members' MCP/API keys (`api_keys.user_id`, per-user) — new surface, not built. · `mcp.py` protocol identifiers. · Code signing declined by decision. · **UNCOMMITTED**.

---

## pt30 — `0.0.486.9` → `0.0.486.13`: Fabric depth, generic skills, Access tab, PRIMARY INSTRUCTION (2026-07-26)

Five releases, each tagged (`pre-0.0.486.9` … `pre-0.0.486.13`). Live image `0.0.486.13`. Baks `*.bak-access-20260726` (11 files), `*.bak-primary-20260726` (4 files), `VERSION.bak-fabskills-20260726`, `*.bak-p4-20260726`.

### `.9` / `.10` / `.11` — Fabric + skills
- `.9`: SSE block-push tasks were fire-and-forget and could be garbage-collected mid-flight (silent, unlogged); chat-channel delivery filter discarded ordinary steps as well as setup steps, and could read the answer before it finished; OAuth token was written only on FIRST sign-in so Entra profile sync used an expired one; a migration used a command one supported engine rejects.
- `.10`: **cross-lakehouse joins** — the connector refused >1 lakehouse and fell back to in-memory joins (millions of rows for a few dozen results). ★**Fabric SQL endpoint hostname is per-WORKSPACE** — same host = same workspace = three-part-name joins are legal. Also: three built-in Fabric skills seeded (`builtin:fabric-*`, `builtin:sql-determinism`); a **false join claim** between two product-code columns with ZERO overlap was being asserted in every overview → claims are now measured against the data before storage; JSON-fence trim only stripped the LEADING fence so a trailing one stored the whole overview as raw machine output; `read_instruction` unblocked outside chat mode.
- `.11`: **★ partial-sync data loss** — `_upsert_user_overlay` removed every table not seen in a sync, but a lakehouse that *failed to answer* also produced no tables, so one unreachable endpoint silently deleted its tables from the user's agent. Now scoped by `_row_in_revoke_scope` to endpoints that actually answered (`revoke_scope=None if failed_endpoints == 0 else ok_scope`). **Absence from a sync only proves lost access if that endpoint answered.**
  - Parallel endpoint crawl (`_FABRIC_CRAWL_CONCURRENCY = 6`, per-tenant `asyncio.Lock` on token mint): crawl 6.5s → 2.9s. ★First timing report was apples-to-oranges — the parallel number included the ~8s DB overlay merge; instrument the phase (`_crawl_seconds`), don't time the whole call.
  - ★★★**#25 was built, measured, and REVERTED**: a client-side connection cache in `ms_fabric_client.connect()`. `pyodbc.pooling = True` by DEFAULT, so the driver manager already pools — cache gained 0.4 ms/query, and the liveness probe it needs is a round trip costing ~35 ms, **doubling** short-query latency. Measurement left as a comment in `connect()` so nobody rebuilds it.
  - Built-in skills genericized (`BUILTIN_SKILLS_VERSION = 2`, seeder updates rows in place): they shipped with the deployment's real table/column names (`Fct_Transactions`, `[Slip No Count]`, `[Shop Name]`) — meaningless to every other user, since **a built-in ships to EVERY user of a connector and no two users see the same tables**. Deployment-specific facts belong in that connector's own overview, which is generated per connector.

### `.12` — Settings ▸ Access (new 13th tab)
Three-state member access (`on` / `coming_soon` / `off`, both non-`on` states refuse at the API) over **shared folders**, **API keys**, **MCP server**. All default **off**.
- `schemas/organization_settings_schema.py`: `ACCESS_STATES`, `access_state()`, `access_allowed()`; `FeatureConfig` gained `options`. `core/access_gate.py` NEW (`require_access`). Gates in `routes/api_key.py`, `routes/local_runtime.py`, `routes/mcp.py`, `dependencies.py`. FE `pages/settings/access.vue` + `useOrgSettings.accessState()`.
- ★★★**THE TRUTHINESS TRAP**: the string `"off"` is **truthy** in Python and JS. Every gate written `if not feature.value:` silently ALLOWS access on the deny state the moment the value becomes a string. Found in **three** places (`routes/mcp.py`, `dependencies.py`, `useOrgSettings.ts` — the FE one via `featureEnabled` falling through to `state === 'enabled'`, and a FeatureConfig holding `"off"` still carries state `enabled`). All now route through one normaliser that **fails closed**.
- ★Legacy bool preserved: `mcp_enabled` shipped as `true`; `access_state()` reads a boolean as on/off so no existing install is switched off by the upgrade.
- ★**Safe-direction exemption**: revoking an API key and un-sharing a folder stay UNGATED — blocking them would strand a live credential with no way to kill it.
- ★Cosmetic: an org whose settings row predates `options` keeps `options=None` (merge preserves the stored object). The Access page derives its buttons from its own `STATES` constant, so enforcement is unaffected.

### `.13` — ★★★ PRIMARY INSTRUCTION: six gaps, three of them live leaks
Symptom: Fabric and Power BI agents showed "No primary instruction" forever. The overview **was** being generated at connect (timestamps: sign-in 12:31:00 → overview 12:32:42), so this was never a timing or learning failure.

Root cause: `primary_instruction_id` is ONE SHARED column. On a per-user connector every member's Learn writes a PRIVATE overview (`is_private=true, user_id=<member>`), and `data_source_service.py:1405` `if force_llm and not _pu_train:` correctly refuses to point a shared column at it. Nothing replaced it, so those agents could never have a primary. `_maybe_promote_fallback_primary` also bailed (`if result.get("onboarding_instruction")`), so even Power BI's shared `Power BI recipe (DAX)` was never promoted.

Six gaps, all verified against the live DB — **3, 4 and 5 were live private-overview leaks**:
1. per-user connectors structurally cannot hold a primary → **per-VIEWER resolution** in `get_data_source` (read-time only, shared column never written; payload carries `scope: "shared" | "personal"`).
2. the `if existing:` re-learn branch never touched the pointer → any NULL primary stayed NULL through unlimited Learns. Now heals when NULL, non-private, published; **never overwrites an explicit choice**.
3. fallback bailed whenever an overview was produced, even one that couldn't become primary.
4. ★fallback had **NO `is_private` filter** — and it prefers `load_mode='always'`, which is exactly what a private overview is, so a member's private overview was the *most likely* row to be promoted org-wide.
5. ★`PUT /data_sources/{id}` accepted any instruction id — the "Change" picker lists the caller's OWN private instructions, so the refusal must live at the **write** (`400`), not in the picker.
6. ★`GET /data_sources/{id}/onboarding_instruction` had no owner filter → handed member B the text of member A's private overview.
   Plus: built-in skills excluded from promotion (generic per-connector advice makes a poor "face of the agent"), and the ordering now prefers a real `onboarding` overview.

### Landmines (new)
- ★★★`DataSource.primary_instruction` is **`lazy="noload"`**. Any plain `select(DataSource)` earlier in the same session puts the object in the identity map with that attribute already None; a later `get_data_source()` reuses the cached object and reports NO primary. Cost one false failure — City Mart looked broken and was not. **Verify a suspected regression against the `.bak` before believing it.**
- ★★★**`update_data_source` COMMITS internally** — a trailing `await db.rollback()` does nothing. My test left Fabric's primary pointing at a City Mart instruction **in the live database**; caught on the post-deploy re-run, reverted by hand, and the test now captures and restores explicitly. Any harness calling a service method that commits must restore by writing, not by rolling back.
- ★Two `asyncio.run()` calls in one script: the asyncpg pool binds to the first loop → `got Future attached to a different loop`. One `asyncio.run` per harness.
- ★`update_data_source`'s parameter is **`data_source=`**, not `data_source_data=`.
- ★Only **three** writers of `primary_instruction_id` exist: llm_sync create-new (1407), fallback promote (1458), the PATCH (3189). The `if existing:` branch was never one of them — that was the gap.

### Verification
`.13` suite **15/15 on the shipped image** (per-viewer resolution for both connectors, City Mart's shared primary intact, anonymous → nothing, a second user → nothing, fallback declines private + builtin, write guard 400 on private / accepts shared, Fabric restored). F3 proven by a REAL `llm_sync(force_llm=True)` on City Mart with the primary cleared → `primary_source = overview_refresh`, healed. Earlier: skills reseed `{'created': 0, 'updated': 3}`; revoke-scope + cache 21/21; crawl semantics 18/18; access normaliser 28/28; access E2E 25/25. Volumes 110 → 110 on every swap; 0 tracebacks.

### Open
Full `tests/unit` NOT re-run for `.13` (user stopped it; 23 pre-existing failures still unowned) · Local Runtime UI → profile modal · per-user admin control of members' MCP/API keys · `mcp.py` protocol identifiers · Windows `.exe` hardware-blocked · **UNCOMMITTED (~140 files)**.

---

## pt31 — UPSTREAM `0.0.486` → `0.0.489` PORT. SHIPPED `0.0.489`, live image `ba48d9d245b7` (2026-07-26)

Live on :8095. Rollback `ee9a9d107e05` under `cityagentinsights:pre-0.0.489`. Baks `*.bak-p5-20260726` (5 merged files). Upstream skipped tag `v0.0.487` in its releases page but the tag exists in the tree; 486→489 carries **no migrations and no dependency changes** (head stays `ca08lrtoggleoff`).

### Method — three gates, because two are not enough
Each forked file was merged hunk-by-hunk and then checked against:
1. **Fork surface unchanged** — the set of lines in our file that exist nowhere in v489. Snapshotted to `scratchpad/forksurface_before/` before any edit; must be byte-identical after.
2. **Upstream end-state realized** — every `+line` present, every `−line` absent.
3. ★★★**In-situ byte-compare** — extract the merged region by its own delimiters from both files and diff. **Gates 1 and 2 both PASSED on a syntactically broken `configs.py`** (`unexpected indent, line 535`): I had spliced "all `+` lines" as a block, but that hunk's `+` region begins mid-field. Line sets do not prove placement. Gate 3 caught it; reverted from `.bak` and re-applied by region-replacement after proving the target region byte-identical to v486.
- **Region map first**: before applying, classify each hunk's target region CLEAN (ours == v486 → safe to region-replace) or FORKED (hand-merge). Caught all 5 hand-merges; the line-set gates never would have.
- **Provenance check** on every line leaving the fork surface: is it in v486 (upstream's, legitimately replaced) or unique to us (real loss)? Without it, upstream-486 lines that 489 rewrote read as fork-code loss.
- **Moved-line exclusion**: a line in both `+` and `−` was moved, not deleted, and must still be present.

### The five forked files (26 hunks, 5 hand-merges, ZERO lines of fork code lost)
- `backend/main.py` — Google Chat Pub/Sub + Slack Socket Mode listeners in `lifespan`, both `if is_scheduler_leader:` (vital at `--workers 4`) + shutdown drains. Surface 17 → 17.
- `backend/app/schemas/data_sources/configs.py` — `PriorityErpPatCredentials` / `…BasicCredentials` / `PriorityErpConfig` + `__all__`. Surface 52 → 52. (The Gate-3 catch above.)
- `backend/app/ai/llm/errors.py` — 266 → 377 lines. 82 lines of quota/transient/AWS marker constants; `ERROR_CODES` gained `"quota"` (my splice anchored below the codes tuple — **caught by the end-state gate**); branch order now Auth → Context → **Quota** → **AWS** → RateLimit → **Network (ours)** → 5xx → unknown; `_extract_status` 19 → 41 lines. Our `_NETWORK_CLS_TOKENS` / `_NETWORK_TEXT_TOKENS` block preserved verbatim.
- `backend/app/services/connection_service.py` — surface 137 → **140**, the only fork code touched all port. Upstream's new `last_credential_identity` (`authoritative = … == "system"`) gates whether a `refresh_schema` crawl may PRUNE the shared `ConnectionTable` catalog. ★Our per-user delegated branch returned **before** any path that sets it, so a single member's Fabric/PBI view would have been treated as authoritative — the exact shape of the `.11` partial-sync data loss. Added `self.last_credential_identity = "user"` (1 functional line + 9 comment) in the delegated branch.
- `backend/app/services/data_source_service.py` — 7 hunks, surface 1252 → 1238 (all provenance-confirmed upstream-486 lines). H5 hand-merged: upstream's N+1 → batch map, wrapped around our `.11` `_row_in_revoke_scope` guard; cascade now serves columns from the batch-loaded map.

### ★ Phase 1 scoping bug — directory-scoped porting is a silent-loss mechanism
The file surface only covered `backend/`, `frontend/`, `locales/`. **8 of 489's new files were never copied** — 7 docs plus `tools/priority/mock_server.py`, which surfaced as `ModuleNotFoundError: No module named 'mock_server'` (28 collection errors). A full-tree comparison then confirmed nothing else was missed. All five files' gates had passed while these were simply never considered.

### `tests/unit` is green for the first time since `0.0.486.3`
**2133 passed / 0 failed** (baseline was 23 failed / 1898 passed). The 23 long-unowned failures were upstream's, fixed across 487–489, and the port brought the fixes in.
- One NEW failure, `test_notify_prefers_verified_teams_over_email`, proven to be **upstream's own bug** by running a clean v489 tree with zero fork code (failed identically). `notify_service` skips a channel on `if hasattr(adapter, "has_dm_space") and not await adapter.has_dm_space(...)`; only `GoogleChatAdapter` defines it, but a bare `MagicMock` fabricates every attribute → `hasattr` True → `await MagicMock()` raises `TypeError` → Bob routed to email. **No production impact.** Fixed in the test with `del adapter.has_dm_space` + a FORK PATCH comment.
- NEW `backend/tests/unit/test_llm_error_classification_fork.py` (24 checks) locks the composition of our `exc_type` fix with upstream's quota axis — they meet at exactly one place, the branch ORDER inside `classify()`, and a future port that reorders it would silently un-fix a dead endpoint.

### Landmines (new)
- ★★★Line-set equality is not merge correctness. Only an in-situ byte-compare proves placement.
- ★★★A directory-scoped file surface silently drops everything outside it and every downstream gate still passes. Compare the FULL tree.
- ★★`hasattr(MagicMock(), anything)` is always True, and `await MagicMock()` raises `TypeError`. A mock standing in for a class that lacks a method must have it deleted.
- ★Two pytest runs against the same DB manufacture failures — and a `docker run` without `--name` gets an auto-name that is easy to orphan (`epic_blackburn` raced the chunk runner).
- ★rtk intercepts `docker logs` and substitutes a "Log Summary" claiming 0 errors — three polls looked empty. Bypass with `rtk proxy docker logs`.
- ★Heredoc into `docker run` needs `-i`, same as `docker exec`. Mount a script file instead.
- ★`ENVIRONMENT=test` is invalid (`development`/`staging`/`production`); mounting the whole repo at `/app` breaks the frontend path — mount only `backend/` and `tools/`.
- ★pytest runs from `/app/backend`, so chunk paths must keep the `tests/` prefix.
- ★I stated `FALLBACK_ELIGIBLE_CODES` already listed `"quota"`. It did not — I had read the repo file that Phase 1 already updated. **Check the reference tree, not the working tree, when asserting what upstream had.**

### Verification
Source == image == container (md5) on all 5 merged files · both listeners logged at boot · `/health` 200 · 300 changelog entries · volumes 110 → 110 · migration head `ca08lrtoggleoff` · 0 tracebacks. Smoke: City Mart 11 active tables, Fabric 54, PBI 6. `.13` per-viewer primary-instruction resolution re-proven live (Fabric/PBI `personal` for the owner, nothing for anonymous; City Mart `shared` for both) — Fabric/PBI hold NULL in the shared column **by design**.

### Open
Local Runtime UI → profile modal · per-user admin control of members' MCP/API keys · `mcp.py` protocol identifiers · Windows `.exe` hardware-blocked · **UNCOMMITTED (~150 files)**.


---

## pt32 — DASHBOARD TRUST RELEASE `0.0.489.3`: complete data, code that compiles, and a grounded insight panel (2026-07-27)

Five phases shipped as one release. Rollback tag `pre-dashtrust`. Baks `*.bak-dashtrust-20260727` (create_artifact, create_data, code_execution, organization_settings_schema, config, VERSION, CHANGELOG, CLAUDE.md) + `*.bak-p2caps-`, `*.bak-p4panel-`, `*.bak-fix-20260727` (two test files).

### The one-line problem
`limit_row_count` (default 1000) caps a query result at write time and ONLY the capped copy is persisted to `steps.data`. Chat preview, the model's data preview AND the dashboard at render time all read that one blob (`ArtifactFrame.vue:1038` → `/api/queries/{id}/default_step`). A cap that exists to protect the browser and the context window was silently deciding what a dashboard was built from. Cut in the query's own sort order, so a month-ordered result lost its most recent months: **56.4B reported against a true 98.9B, 10 of 17 months, undeclared**.

### Phase 1 — completeness gate (`create_artifact.py`)
DEF-004 made truncation VISIBLE; visibility was not enough — the model read the warning, built the dashboard anyway, rendered it, noticed, discarded and rebuilt. **All three live runs did this**, one wasted generation each. Now `create_artifact` REFUSES a truncated viz and returns the real reason (DEF-003's fix is what lets that message survive). Live: *"'CFC Sales Master' has 1000 of 37404 rows"* → agent built a 292-row aggregate → success.

### Phase 2 — the display cap and the artifact cap are different numbers
New org setting `artifact_row_limit` (default **10000**) beside `limit_row_count`. `format_df_for_widget(..., for_artifact=False)` selects which. `create_data` stores a second wider copy as **`rows_artifact`** ONLY when the display cap actually cut something AND the fuller set fits; `rows` is untouched so every existing consumer is byte-identical. `create_artifact` prefers `rows_artifact`.
- ★★★**`step_data["rows_truncated"]` describes the PREVIEW copy.** With Phase 2 in play it is STALE — a 1,200-row dataset has `rows_truncated: True` (preview held 1000) while the artifact holds all 1,200. Trusting it makes the completeness gate refuse COMPLETE data. `step_truncated` is now `rows_total > len(rows)` and nothing else.
- Live payoff: the 1,200-row branch×month dashboard built in **258s vs 540s** — no aggregate rebuild cycle at all.

### Phase 3 — nothing is stored until it compiles (`services/artifact_preflight.py`, NEW)
DEF-008's structural guard catches PROSE; it cannot catch malformed JSX (not Python, `compile()` won't read it). No node/npx/JS parser in the image — but **`/app/frontend/dist/libs/babel-standalone.min.js` is already shipped for the artifact sandbox**, and Chromium is already there for PDF export. So run the SAME Babel the browser runs, over the same code. **~0.5s per check.** Two correction attempts, then honest failure.
- ★★★`page.set_content()` with `<script src="file://…">` is **silently blocked by Chromium** — page loads, `Babel` never defines, check fails OPEN on every artifact while looking like it passed. Use **`page.add_script_tag(path=…)`**, which inlines the file. Comment left in the code; do not "simplify" it back.
- ★Caught live what DEF-008 misses: a reply prefixed *"Building the interactive CFC sales dashboard… monologue"* followed by valid code — the structural guard sees `function`/`return` and passes it; only a real parse rejects it.
- Fails OPEN by design (babel missing / chromium dead / any exception → allow). A broken preflight must never block a dashboard that would have rendered.

### Phase 4 — grounded insight panel (`services/artifact_insights.py` NEW + `ArtifactInsights.vue` NEW)
Headline + ≤5 findings generated from the FINAL data (after the gate, so it can never describe a prefix). **Every figure is verified against the dashboard's own data before storing**; ungrounded findings are DROPPED. Stored as `content.insights` `{headline, findings[{text,viz_id}], rejected_count, generated_at}`; panel renders in `ArtifactFrame.vue` **OUTSIDE the iframe** so every dashboard gets it including ones generated before the feature existed.
- ★★★**A flat percentage tolerance cannot work.** The observed fabrication was an AOV of **11,499 against a true 11,488.57 — 0.09% out**. Any tolerance above a tenth of a percent admits it; any tolerance tight enough to catch it rejects `104.8B` for 104,781,422,679. Fix: derive the allowance from **how precisely the figure was written** (`_write_precision_slack`) — `104.8B` is stated to a tenth of a billion so may be out by half of that; `11,499` is stated to the unit so may be out by half a unit, and 10.43 is not. Precision is a claim; hold the writer to it.
- ★★The magnitude pool must include **derived aggregates**, not just cells: column totals and means, and **per-group totals** (group by each categorical column with ≤250 distinct values, sum each numeric column). A branch total is the sum of 17 monthly rows and appears in NO cell — checking cells alone dropped true findings on two consecutive live runs.
- ★**Dates are not magnitude claims.** "rose from 5.39B in Jan 2025 to 8.02B in May 2026" has four number-like tokens, two of them YEARS. Left in, they failed grounding and sank every correctly-grounded finding. `_DATE_PATTERNS` strips `2025-01`, `2025`, `Q3` first.

### Flags (all default ON, `config.py`)
`HYBRID_ARTIFACT_COMPLETENESS_GATE` · `HYBRID_ARTIFACT_RENDER_PREFLIGHT` · `HYBRID_ARTIFACT_INSIGHTS`. Read via `_read_bool_setting`, which type-checks the value — ★`"off"` is TRUTHY in Python and has bitten this codebase three times.

### Landmines (new)
- ★★★**`settings` is a pydantic BaseSettings instance — it REJECTS assignment to fields it does not declare.** `monkeypatch.setattr(settings, "probe_flag", …)` raises, and junk values fail field validation even on real fields. 37 test cases failed on this. Since `_read_bool_setting` re-imports the module and `getattr`s per call, swap the MODULE ATTRIBUTE for a plain stub instead.
- ★★**`tools/priority/mock_server.py` is NOT copied into the image** — the Dockerfile copies `./backend`, `./locales`, `./frontend`, `./VERSION` and one Rust tool, never `./tools/priority`. `test_priority_erp_client.py` does `sys.path.insert(0, tools); import mock_server` → **28 collection errors on every full run**. pt31 recorded this as fixed; it was fixed IN THE CONTAINER and every rebuild since has wiped it. Add `COPY ./tools/priority /app/tools/priority` to fix properly.
- ★`pytest-timeout` is not installed — `--timeout=` is an unrecognised argument (exit 4).
- ★A test asserting an exact list from a helper encodes that helper's design. `_data_magnitudes(viz) == [42.0]` broke when the pool legitimately gained totals and means; assert the CONTRACT (booleans absent, real value present) instead.

### Verification
Phase suites **208 passed** (P1 93 · P2 33 · P3 15 · P4 67). Full `tests/unit` **2520 passed / 0 failures / 28 pre-existing collection errors** (1h 10m) — baseline 2133 + 415 ours = 2548, minus the 28 that cannot import. Live E2E ×3 on real Fabric: gate refused 1000-of-37404 then succeeded on 292 aggregated rows; preflight caught the "monologue" prefix; Phase 2 used 1,200 artifact-width rows and halved build time; insights stored 4 grounded findings with 1 dropped, headline **98,921,181,279.04** — the exact figure the broken dashboard reported as 56.4B.

### Open
FE insight panel **never seen rendering** (needs the image rebuild). `COPY ./tools/priority`. Fast fork test suite (~415 tests at 0.9s each is conftest overhead, not test cost) — see memory `feedback_fast_fork_test_suite`. OfficeCLI evaluated for slides (decks fail 4 of 7 via python-pptx codegen); recommendation was a prototype against the deck that failed, not adoption.

---

## pt33 — SHIPPABLE INSTALL: one-command upgrade, `0.0.489.4` + `0.0.489.5` (2026-07-27)

Two releases, both live. Rollback tags `pre-0.0.489.4` (contains `.3`) and `pre-0.0.489.5` (contains `.4`). Backups `_backups/{db,env,repo}-pre-phase1-20260727-1200.*` — the tarball was **verified by extracting from it**, not by exit code. **Zero Python changed all day**, so the last green `tests/unit` (2520) still stands; no suite was re-run.

### The theme: three claims that nothing enforced
Every problem this session had the same shape — something asserted a guarantee and no mechanism checked it.
- `.gitignore` said "secret, keep out" → the file was **tracked**, so the rule did nothing (proved: appended a byte, git reported ` M`).
- `upgrade.sh` said "rollback available" → it could never work on a first upgrade.
- `CHANGELOG` said "one command upgrade" → I shipped `0.0.489.4` **by hand**, command by command, without using the script.
★ The lesson that generalises: **fire the real path, not the guard around it.** Gate-testing every failure mode passed while the success path had never once run.

### `0.0.489.4` — install/upgrade tooling
- **NEW `.env.example`** (132 lines) — did not exist; a new install began by reading `config.py`. Opens with the `BOW_ENCRYPTION_KEY` trap, marks 3 REQUIRED, documents every compose var with its real default. `.gitignore:40` already had `!.env.example` waiting.
- **NEW `preflight.sh`** (read-only, `set -uo pipefail` deliberately not `-e`) — discovers the stack from `docker ps` labels, reports version from **all three places it can disagree** (repo file / container / served API), git state, config presence (never values), health, migration head, disk, backups, rollback tags.
- **NEW `upgrade.sh`** — modes: default, `--dry-run`, `--rollback`, `--project`, `--help`; env `CITYAGENT_{BACKUP_DIR,KEEP_BACKUPS,MIN_DUMP_BYTES,PROJECT}`. The four silent failures are hard gates: no dump / no image tag / no `FE_CACHEBUST` / no pre-swap verify.
- **NEW `UPGRADE.md`** — script-first, manual steps as an appendix.
- `ChangelogModal.vue` — removed the upstream GitHub releases link (told any customer this is a fork, and showed version numbers that never match ours). i18n key `changelog.viewOnGithub` deliberately left in all locales: unused keys render nothing, deleting them creates port conflicts.
- ★Proof method for the link removal: grep the **old** image too (1 dist file) so a zero in the new one can't be a meaningless match.

### ★★★ The rollback bug — found only by running the real path
`upgrade.sh:132` computed `ROLLBACK_TAG="$IMAGE_REPO:pre-$OLD_VERSION"`, where `$OLD_VERSION` is the version **currently running**. But a `pre-X` tag is written while *leaving* X, so it holds the image **of** X. Off by exactly one release: after the first upgrade it searches for a tag that only exists if you had already upgraded away from the running version.
- **Rollback aborted 100% of the time on a first upgrade** — and it aborted with a plausible message ("no image tagged…"), which is why gate-testing "refuses when no target exists" passed. It was refusing for the wrong reason.
- Live tags prove name-derivation can never work here: `pre-0.0.489.4` contains `0.0.489.3`, and `pre-0.0.489.3` **also** contains `0.0.489.3`.
- Fix: walk `$IMAGE_REPO:pre-*` newest-first, `docker run --entrypoint sh … cat /app/VERSION` on each, take the first whose version ≠ running. Report the real version, not the tag name. Also filtered the "Available:" error list to the repo (it was dumping `scout` and `cityagent-analytics` tags).

### The throwaway upgrade test (the thing that found it)
Bare origin ← `git clone --bare` of the live repo → deployment clone → compose copy with renamed containers/image (`container_name` is pinned, so parallel stacks need a sed'd file) → ports 8102/5450 → `.env` from `.env.example` alone.
- **Fresh install PASS** — `.env.example`'s 3 REQUIRED blanks were exactly what was needed; migrations to `ca08lrtoggleoff` on an empty DB; `needs_setup: true`.
- **Upgrade PASS — 4 m 44 s unattended**, real `git pull --ff-only` from a real remote, dump 959 TOC entries, `.env` backup md5-identical, build gate confirmed `0.0.489.4` before swap.
- **Rollback PASS after the fix**; a second rollback correctly refuses ("every pre-* already reports the running version").
- ★★**Harness landmine that cost a full rebuild:** seeding the origin with `git init && git add -A` silently dropped `backend/alembic.ini`, because `.gitignore:44` listed it. Fresh install then crash-looped `FAILED: No 'script_location' key found in configuration.` ×3 → exit. **The error names neither git nor the file.** Real clones were fine (the live repo tracks it) — but this is exactly how the product becomes uninstallable.

### `.gitignore` — two rules that lied, both removed with comments
`git ls-files -i -c --exclude-standard` found **3 tracked-but-ignored files**. `.gitignore` only affects UNTRACKED files, so each rule was inert while implying protection.
- `backend/alembic.ini` — un-ignored. Boot-critical, holds no credentials (`sqlalchemy.url` commented out; real URL from `BOW_DATABASE_URL`). One `git rm --cached` from an unbootable product.
- `configs/bow-config.dev.entra.yaml` — **DELETED**. Unused (nothing loads it; app reads `bow-config.yaml`, or `configs/bow-config.dev.yaml` when `ENVIRONMENT=development`), and it carried **upstream's own** Entra tenant + client id (`git log -S` → commit `b264682a` "entra id config", author `yochze@gmail.com`, Apr 2026) with `enabled: true`.
- ★★★**Why an unused config file is not harmless:** the YAML is the FALLBACK when `instance_settings` is empty, which is the state of **every fresh install**. An `enabled: true` provider block sitting in a config file can put a sign-in button for someone else's directory on a login page nobody configured.
- Same identifiers were **also** in `configs/bow-config.dev.yaml` (the file that IS loaded in development) → scrubbed to `${BOW_ENTRA_TENANT_ID}` / `${BOW_ENTRA_CLIENT_ID}` / `${OKTA_DOMAIN}`. Safe: entra is `enabled: false` there and the stack runs `ENVIRONMENT=production`.
- ★No secret was ever exposed — every credential in those files was already a `${ENV_VAR}`. My first assessment ("LOOKS REAL") came from testing **value length**, which is a bad test. The literals were identifiers, not credentials. The ids remain in **6 historical commits**; they are upstream's own and already public in upstream's repo, so zero incremental exposure — but that is a history rewrite, not a file edit, if this ever goes public.
- Remaining tracked-but-ignored: `backend/tests/evals/spider_results.jsonl` (375 KB test artifact, harmless).

### `0.0.489.5` — the stale-UI class of bug, killed permanently
Symptom on `insights.citygpt.xyz`: upgraded server, but one browser shows the old UI forever while a **new** browser shows the new one.
- **NEW `frontend/plugins/serviceWorkerPurge.client.ts`** — unregisters any service worker on this origin, clears Cache Storage, reloads **once** (`sessionStorage` guard against loops). This app registers none (`serviceWorker.register` in dist: **0**; the 18 `serviceWorker` hits are Monaco's bundled TS DOM type definitions, i.e. text).
- ★★A hard-refresh does **NOT** fix an inherited worker — `Cmd/Ctrl+Shift+R` bypasses the HTTP cache, not a controlling worker. So this cannot be delegated to "tell them to hard-refresh".
- ★Scope is per-ORIGIN: a worker on `citygpt.xyz` cannot control `insights.citygpt.xyz`. Only a previous occupant of the exact hostname.
- **NEW `UPGRADE.md` › "Behind a reverse proxy"** — `/` must be `no-cache`, `/_nuxt/*` immutable; the one-line diagnostic (`curl -sI … | grep -iE 'cache-control|age|cf-cache-status'`); Caddy / nginx / Cloudflare configs; plus a Troubleshooting entry for the exact symptom.
- Our own headers verified correct: `/` → `cache-control: no-cache` + etag; `/_nuxt/*.js` → `max-age=31536000, immutable`.

### ★★E2E proof of the kill switch — and the test that lied first
In-container Playwright (already in the image), origin `http://localhost:3000`, a real caching `sw.js` `docker cp`'d into dist then removed.
- **First run reported FAIL** — and the TEST was wrong, not the code. It left the installing tab open, and **an active worker is not torn down while any client it controls is still open**, so it kept serving. Closing that tab gave `regs=0 controlled=False`. ★Real-world consequence worth knowing: a user with 3 tabs needs all 3 to reload before the worker is fully gone; each self-heals on its next navigation.
- ★The bad run did surface a genuine gap: the dying worker **re-created its cache while serving the reload**, and on the next load the plugin returned early (0 registrations) so the orphan survived forever. Fix: `dropForeignCaches()` runs **outside** the registration branch — Cache Storage is only ever written by a service worker and we have none, so anything there is foreign by definition.
- Final: `pass 1 regs=0 controlled=False caches=[]` · `pass 2` identical · console `[CityAgent] Removed 1 stale service worker registration(s)…`. Test artifacts removed; `sw.js` confirmed **absent from the image**.

### Storage
`docker system df` said 108.7 GB reclaimable. Deleting 14 `cityagentinsights:pre-*` tags freed **162.7 MB**.
- ★★"6.02 GB each" is per-tag accounting of **shared layers** — unique bytes per tag were ~12 MB. `docker buildx du` splits it honestly: Shared 71.6 GB / Private 21.0 GB.
- ★`builder prune --filter until=168h` **and** `until=48h` both freed 0 B: every one of 871 cache entries had been used within ~3 days. The filter worked; there was nothing stale.
- Net across the day: images 155.2 → 114.6 GB, host free 100 → 138 Gi (40 GB of that was `cityagent-analytics` pre-* tags removed outside my tool calls — **not attributable to me**).
- ★Plain version tags (`cityagentinsights:0.0.486.1` … `:0.0.489.3`) survived; only `pre-*` were touched.

### Landmines (new)
- ★★★A `pre-<version>` tag holds the image **of** that version, not the one before it. Never derive a rollback target from a name — read `/app/VERSION` out of the candidate.
- ★★★`.gitignore` does nothing to a tracked file. A rule that implies protection it cannot provide is worse than no rule.
- ★★An unused YAML is not inert when it is a config **fallback** and the DB starts empty.
- ★★Testing a service worker: close the tab that installed it, or the worker stays alive and the result is meaningless.
- ★`docker volume ls -q | tail -1` sorts **alphabetically**, not by age — useless for "what just appeared". Sort on `docker volume inspect --format '{{.CreatedAt}}'`.
- ★A volume-count delta may belong to another product entirely (110 → 111 was `rise_appstore`). Check the names before assuming your own stack moved.
- ★`HEAD` returns **405** on this server — the SPA catch-all owns it. Header checks need `curl -s -o /dev/null -D -` (GET).
- ★`git rm` refuses a locally-modified file; `-f` is correct when the file is being deleted anyway and a backup exists.
- ★macOS bash 3.2 has no `mapfile` — it is a builtin, so `bash -n` passes and it fails only at runtime.
- ★`df` cannot see Docker Desktop's root (it lives in a VM) — use `docker system df`.

### State
Live `0.0.489.5`, image `74d7f990071e`, `/health` 200, head `ca08lrtoggleoff`, volumes 110 → 110, 0 tracebacks, data intact (users=2 agents=3 reports=193 instructions=15).
**UNCOMMITTED: 9 files + 4 unpushed commits.** Nothing from today exists anywhere but this laptop — including the upgrade script that would be how it reaches a server.

### Open
Superseded by pt34 below.

---

## pt34 — GITHUB RESET + fresh install + member data-agents + admin agent kill-switch. `0.0.489.6` → `0.0.489.10` (2026-07-27)

### ★★★ THE GIT REPO WAS RESET TO A SINGLE COMMIT
`origin/main` is now **one orphan commit** `91b0e9b7` "CityAgent Insights 0.0.489.6 — baseline", tag `v0.0.489.6`. Was: 2 branches, 227 tags, 3975 commits. Done on explicit instruction after alternatives were laid out.
- ★★★**FULL HISTORY IS IN `_backups/full-history-pre-reset-20260727.bundle` (128 MB) AND NOWHERE ELSE.** Restore-tested before the reset: `git clone <bundle>` → 3975 commits, 236 tags, `v0.0.482.1`/`v0.0.489.3` present. Needed for `git log -S`, `git blame`, and reading any past release. **Do not delete it.**
- ★★★**Any pre-existing clone can no longer `git pull`** — unrelated histories. Fix, once per checkout: `git fetch origin && git reset --hard origin/main`. This also means `upgrade.sh` (which runs `git pull --ff-only`) FAILS on an old checkout until that is done.
- ★Porting is unaffected: `upstream` remote still fetches, so `git diff v0.0.489..v0.0.490` and diffing our tree against an upstream tag both still work. What is lost is our own archaeology.
- Removed on the way out: `frontend/localhost*.pem` — ★a REAL private key inherited from the clone, upstream author's mkcert dev cert (`yochze@Yochays-Laptop.local`, Oct 2024). Localhost-only so harmless, but it was someone else's key. `.gitignore` now blocks it. Also `configs/bow-config.dev.entra.yaml` (unused, upstream's Entra tenant, `enabled: true`).
- ★Whole-tree secret scan before pushing: only remaining hit is `AKIAIOSFODNN7EXAMPLE`, AWS's own doc example in `test_pii_redactor.py`. `.env` confirmed ignored and unstaged.

### ★ AWS DECISION: REINSTALL, NOT UPGRADE
`insights.citygpt.xyz` runs `0.0.482.1`. Rahul has decided to **reinstall from the new baseline**, not upgrade — decision made after the trade-offs were laid out.
- ★★★**A reinstall generates a NEW `BOW_ENCRYPTION_KEY` unless the old `.env` is carried across.** Every stored credential (connector passwords, OAuth refresh tokens, LDAP binds, SSO secrets) becomes permanently unreadable — silently, no error. If anything on that box is worth keeping, copy `.env` first.
- For the record, upgrading WOULD have worked: 482.1 had `ca01`+`ca02`; head is now `ca09`. The 7 intervening migrations are all additive (AST-audited: `add_column`, `create_table`, `create_index`, two `execute` backfills — no `drop_*` in any `upgrade()`; the drops the grep finds are in `downgrade()`, which is correct). Not taken.

### `0.0.489.6` — APScheduler startup race (found by a fresh install)
`scheduler.start()` also CREATES `apscheduler_jobs`, via `jobs_t.create(engine, checkfirst=True)` — a SELECT-then-CREATE. On an EMPTY database all 4 uvicorn workers pass the check within ~14ms and all issue CREATE TABLE; 2 die with `UniqueViolation on pg_type_typname_nsp_index` → `Application startup failed. Exiting.` uvicorn respawns them, so it self-heals in ~13s and `/health` returns 200 the whole time.
- ★The error names **`pg_type`, not the table** — Postgres creates a composite row type alongside every table and that index conflicts first. Does not read as "table already exists".
- Fix: new `start_scheduler()` in `app/core/scheduler.py` wrapping `scheduler.start()` in `pg_advisory_xact_lock` (DB-held, so it covers replicas too; the existing leader lock is `fcntl` and per-host). Fails open, guarded by `scheduler.running`. `main.py:562` calls it.
- Proven by wiping the postgres volume and rebooting: **complete 4 / failed 0 / tracebacks 0**, health in 10s instead of 20s.

### `0.0.489.7` → `.8` — members could not create data agents (2 independent bugs)
Symptom: a member saw Instructions only, no way to add data, nothing to build a dashboard from.
1. ★★★**Two sources of truth for member permissions.** `permissions_registry.DEFAULT_MEMBER_PERMISSIONS` has 8 including `create_file_data_source`; the RBAC migration `e6f7g8h9i0j1_rbac_mvp:28` hardcodes its own copy of **7**, under a comment claiming it "mirrors" the registry. Every install — including brand-new ones — seeded members one short → `routes/data_source.py:214` 403. ★The `permission_resolver:341` fallback DOES use the correct list but only fires when a user has NO role assignments, and the migration backfills them, so it never fires.
   Fix: migration `ca09memberfileds` (idempotent, additive, **skips non-system roles**) + `tests/unit/fork/test_member_permission_seed_parity.py`, which was proven to fail with an actionable message when the migration is removed.
2. `KnowledgeExplorer.vue` gated 5 entry points on `create_data_source` (admin). Split into `canCreateAgent` (admin — connect DB/warehouse/BI) and `canCreateDataAgent` (admin OR `create_file_data_source` — upload files). ★`.7` used ONE combined gate and wrongly offered members the database connector; `.8` is the correct split.
- **Proven in the real browser, both roles**: ADMIN `Agent=YES DataAgent=YES`, MEMBER `Agent=no DataAgent=YES`. API boundary: member `type=csv` → **200**, `type=postgresql` → **403**.

### Fast fork test suite — BUILT
`backend/tests/unit/fork/` + a `conftest.py` overriding the parent's function-scoped autouse `run_migrations` with a no-op. **236 tests: 210.06s → 2.24s (94×)**, same pass count; whole-tree collection unchanged at 2548.
- ★The override takes **no fixture arguments** — requesting `alembic_config`/`sqlite_template` drags the session-scoped setup back in and silently undoes it.
- ★Never put a schema-needing test there; it fails "no such table" and reads as a product bug. Split by COST, not feature.
- Commands are documented at the top of this file.

### ★ Harness landmines that looked like product bugs (all mine, all wasted time)
- ★★`.local` is a **reserved TLD** — pydantic's email validator rejects it, so a test user at `@x.local` makes `/api/users/me` return **500** and the browser bounce to sign-in. Use `@example.com`.
- ★★A `Membership` alone is NOT enough: the FE reads permissions from the org payload, which resolves from **`role_assignments`**. Without a row there the browser gets `permissions: []` while `resolve_permissions()` still returns the right set via its fallback. Columns are `principal_type`/`principal_id`, **not `user_id`**.
- ★★Testing a service worker: **close the tab that installed it**, or the worker stays alive serving its clients and the result is meaningless (this produced a false FAIL).
- ★`git bundle verify` output must be read past the hash-algorithm line; prove a bundle by **cloning from it**.
- ★zsh does NOT word-split an unquoted `$var` — a batched `git push origin $refs` sent all 60 refs as ONE argument and silently failed. Use `xargs`.
- ★`docker volume ls -q | tail -1` sorts alphabetically, not by age.
- ★`HEAD` returns **405** here (SPA catch-all) — header checks need `curl -s -o /dev/null -D -`.

### `0.0.489.9` — ★★★ "Accept all" did nothing for a non-admin
An agent's own creator/manager pressed Accept on a suggested instruction change and nothing happened; the change stayed `pending_approval` and **each click minted another stuck build** (one org reached 15 builds / 10 pending).
- Chain: `resolve_suggestion` promotes accepted text via `create_build(..., copy_from_main=True)` — deliberate, so the promoted build carries every other instruction forward untouched. But `_can_auto_publish_build` then required `manage_instructions` on **every data source in the build**. Copy-from-main drags in every other agent, so:
  `CRM=True, Microsoft Fabric=False, City Mart Retail=False → all() False → pending forever`.
- ★★**The documented "agent admin" tier was unreachable in any org with more than one agent.** Ruled out first: `manage` DOES expand to `manage_instructions` (resolver fine), and there was NO global instruction in the build (the other blocking branch).
- Fix: compare the build's `(instruction_id, version_id)` pairs against **main's** and gate only on what DIFFERS. Inherited-unchanged rows are not authored. Empty diff → `True` (a no-op build must not sit pending forever). ★Main lookup uses `.scalars().first()` not `scalar_one_or_none()` — a duplicate `is_main` row must not turn a permission check into a 500 (upstream's `mainbuild01` partial index is in v0.0.490, unported).
- Live proof, same user: `can_auto_publish False → True`. Tests: 6 new (real gate, fake session) + 97 instruction/build/permission regressions.

### `0.0.489.10` — admin kill-switch for the seeded agents (Settings → Access)
`GET`/`POST /api/organization/settings/builtin-agents`, `manage_settings`-gated, + a card in `pages/settings/access.vue` (per-agent switches, `N of 3 on`, Turn all off/on).
- ★★**Writes `DataSource.publish_status` — the SAME field the Agents-page switch writes.** No second flag, deliberately: the first live `GET` already returned `Power BI: enabled=false` because it had been switched off from the Agents page, with no sync code. Two controls, one truth.
- ★★Target set is intersected with the seeder's own name list (imported from `default_agents_seeder`, not copied), so a forged/mistyped name is ignored — proven live: `Turn all off` left `CRM → published`.
- Card renders only when seeded agents exist (an unseeded org gets no rows controlling nothing).
- Tests: 9 new. Fork suite 262 green.
- ★Unexplained, seen ONCE: a restore call naming 2 agents appeared to re-enable a 3rd. Not reproducible in 3 subsequent deterministic runs. Recorded rather than dismissed.

### ★★★ The recurring theme this whole session
**A claim nothing enforces.** Five instances, all fixed: `.gitignore` said "secret" over tracked files · `upgrade.sh` said "rollback available" (off by one release) · the changelog said "one command upgrade" while I shipped by hand · a migration comment said it "mirrors DEFAULT_MEMBER_PERMISSIONS" (7 vs 8) · a permission gate judged what a build *contained* rather than what it *changed*.
★★And **three times a test caught MY OWN incomplete fix** — each time because production was saved by a SQL `WHERE` while the guarantee lived in a query someone could later widen. Now enforced in the Python loop too, with comments saying the redundancy is deliberate. When a guarantee matters, put it where it fails loudly.

### State
Live `0.0.489.10`, head `ca09memberfileds`, 2 users (`raahulgupta07@gmail.com` admin + `test@gmail.com` member), 4 agents (3 seeded + CRM owned by test@), volumes 110, 0 tracebacks. Database was **wiped and re-created today** — old data is in `_backups/db-pre-phase1-20260727-1200.dump`.
★ **`0.0.489.7` and `.8` are LOCAL ONLY** — GitHub is at the `0.0.489.6` baseline. Also uncommitted: the scheduler fix, member-permission fix, fork test suite.

### Open
Superseded by pt35 below.

---

## pt35 — `0.0.489.11` security + UPSTREAM `v0.0.490` PORT + cleanup. LIVE `0.0.490`, image `d4f4a8f3e21a` (2026-07-27)

Live on :8095. Rollback `cityagentinsights:pre-0.0.490` → `49ab0b81f740` (holds `0.0.489.11`). Snapshot `_backups/pre-490-20260727/` (repo tarball 176 MB / 3651 entries incl. all uncommitted `.7`–`.11`, DB dumps `bagofwords-pre490.sql` + `bagofwords-premigrate.sql`).

### `0.0.489.11` — two dependency facts, one of them a dead feature
- **pdfminer-six** was three years old and carried CVE-2025-64512 + CVE-2025-70559 (both HIGH, "Deserialization of Untrusted Data"), reachable from `DocAgent.get_content()` — i.e. from any PDF a user uploads. Pinned `>=20251107,<20260000`. Extracted text verified byte-identical before/after on real PDFs.
- ★★★**`uv sync --frozen` never checks the lock against the manifest.** `python-docx` was declared in `pyproject.toml` and absent from `uv.lock`, so it was never installed — **Word export returned 500 on every installation since the feature shipped**, and the file classifier silently downgraded uploaded `.docx` to "unknown". No build error, no start-up error. Guard: `tests/unit/fork/test_lockfile_parity.py` (+ a guard-the-guard asserting the lock parser found >100 packages, so a format change can't turn it into a silent pass, + a security-floor assertion on the pdfminer lower bound).
- ★Upstream shipped **the identical pdfminer pin** in v0.0.490 two days later — independent confirmation of the bound, found only because the port diffed `pyproject.toml`.

### ★★★ Upstream v0.0.490 shipped with TWO ALEMBIC HEADS
`idxuser01` and `mainbuild01` both declare `entraprof01` as parent, neither chains onto the other. `alembic upgrade head` **refuses to run** in that state — so this is not a warning, it is an app that will not start, discovered at boot on whichever machine deploys next. In a tagged upstream release. Both re-pointed onto the end of our chain (`ca09memberfileds → idxuser01 → mainbuild01`), documented in each migration's docstring.
- Guard: `tests/unit/fork/test_alembic_single_head.py` — one head, one base, every file on disk reachable, plus a guard-the-guard (`>150` revisions walked) because a `ScriptDirectory` pointed at an empty directory would make every other assertion pass vacuously.
- ★★**A regex cannot parse alembic history.** `down_revision` is sometimes a tuple (merge migrations); my hand-rolled parser matching only the single-string form reported **22 phantom heads** on a tree alembic reads as one. Use `ScriptDirectory` — it reads the files directly, imports no `env.py`, opens no DB, so it stays in the fast fork suite.
- Proven both directions: passes on the fixed tree; on the broken upstream originals fails with `alembic has 3 heads: ['ca09memberfileds', 'idxuser01', 'mainbuild01']`.
- **Sixth instance of the recurring shape** — a claim nothing enforces (see pt34's five: permission registry vs seeded role · `.gitignore` over tracked files · `upgrade.sh`'s rollback promise · auto-publish gate reading *contained* not *changed* · `pyproject.toml` vs `uv.lock`).

### The port — 49 files, no git merge path
★**`git merge-base --is-ancestor v0.0.489 HEAD` → NO.** `HEAD` is the orphan `91b0e9b7` from the GitHub reset, so `v0.0.489` is not an ancestor and **no `git merge`/`cherry-pick` exists**. Mechanism was `git merge-file --diff3` per file (base `v0.0.489:$f`, theirs `v0.0.490:$f`, ours the working file) — a real 3-way merge that does not touch the index, unlike `git apply --3way` which implies `--index`.
- **30 verbatim** (10 new + 20 the fork never touched), verified 0 mismatches · **13 three-way merged, all clean, zero conflicts** · 4 already settled · 2 alembic.
- Upstream's substance: bounded SharePoint connection test with per-step timings, file count capped at "200+", OneDrive catalog build moved to a tracked background job with live progress, pooled HTTP client + concurrent sibling folders, `Indexing`/`Max Files` on both connectors; `mainbuild01`'s repair + partial unique index; `resolve_main_build()` extracted to `app/core/main_build.py`; `input_schema` carried into the planner's tool context; `tool_input` retained on failed calls.

### ★★★ 139 test "errors" that were a file-permission problem
Upstream's 6 new/updated unit suites returned **139 errors**. First diagnosis (read-only mount) was half-right; second ("tmpfs fixes it") was **wrong — the count did not move**. Real cause: `--tmpfs` mounts as `root:root 0755` and the container runs as `uid=999(app)`, so pytest still could not create `db/test_*.db.template`. Correct invocation:
```
docker run --rm -v "$PWD/bagofwords:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc cityagentinsights:local \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest <files> -q -p no:cacheprovider'
```
→ **139 passed.** Not one was a real failure.
★**The baseline is what settled it**: the same suites on the *pre-port* tree errored identically (95 on the 4 shared files vs 103 after — the delta is upstream's new test cases, all erroring for the same reason). Establish the baseline before concluding anything about a port. ★Read-only mounts also break `compileall` (`__pycache__` writes) — `-e PYTHONPYCACHEPREFIX=/tmp/pyc`.

### Cleanup
- ★**`stocks.duckdb` is NOT dead** — `tests/e2e/test_demo_data_source.py:7` uses it and asserts `len(demos) >= 2  # At least chinook and stocks`. Same trap as `chinook.sqlite`; the standing "delete 4 dead files" note was wrong. The other 3 (`configs/bow-config.{google_oauth,multiorg.dev,sandbox}.yaml`) removed → backup `_backups/dead-configs-20260727/`. Verified unreachable: `config.py:258` loads only `bow-config.yaml` or `configs/bow-config.dev.yaml`. `google_oauth.yaml` carried `enabled: true` with dummy creds — the same fallback hazard as the Entra file removed in pt33.
- `COPY ./tools/priority /app/tools/priority` **finally in the Dockerfile** (with a comment recording that it was once fixed inside a container and every rebuild since wiped it). Takes effect next build.
- 14 stuck `pending_approval` builds cleared — each verified to contain **nothing** not already in main. Ids in `_backups/pending-builds-cleared-20260727.csv`.
- Shadow test stack stopped (`bow-app-shadow`/`bow-postgres-shadow`, ~1.4 GB RAM). Containers + volumes kept.
- **Postgres password was literally `bowpassword`, the shipped default**, on a port published to `0.0.0.0:5440`. Now 40 random chars: `ALTER USER` → `.env` → recreate. ★`POSTGRES_PASSWORD` in `.env` is a single source feeding both the postgres container and `BOW_DATABASE_URL`, so one edit covers both — but postgres only reads it at data-dir init, so **`.env` alone changes nothing**. Verified both directions: app queries fine, old password now `FATAL: password authentication failed`. Old `.env` at `_backups/env-pre-pwchange-20260727`. ★★That `.env` is now the only copy — same carry-it-across rule as `BOW_ENCRYPTION_KEY`.

### Landmines (new)
- ★★★A regex cannot parse alembic history (tuple `down_revision`) — use `ScriptDirectory`.
- ★★★`uv sync --frozen` installs the lock and never validates it against the manifest.
- ★★`--tmpfs` is `root:root`; a container running as a non-root uid still cannot write it. Pass `uid=`/`gid=`.
- ★★zsh does **not** word-split an unquoted `$var` — `for f in $FILES` ran once with 13 paths concatenated (`File name too long`; nothing was written). Use `while IFS= read -r f`.
- ★The backend entrypoint is `backend/main.py` → `import main`, never `import app.main`.
- ★A freshly built image has **no pytest** (built `--no-dev`) — `pip install -q pytest pytest-asyncio` first, every time.
- ★`edit_artifact.py:514,518,582,586` contain literal `<<<<<<< SEARCH` / `>>>>>>> REPLACE` **prompt text**, not merge conflicts. Every conflict sweep flags them.
- ★`/api/changelog` output contains control characters — `json.load(..., strict=False)`.
- ★`grep -c` exits 1 on zero matches, which reads as a failed command when zero is the desired answer.

### Verification
Fork suite **269 passed / 4.0s on the shipped image** (was 265; +4 alembic guards). Upstream's 6 suites **139 passed**. Build exit 0 → verified INSIDE the image before any swap (VERSION, changelog header, both migrations, `main_build.py`, `mcp_schema.py`) → migrated → swapped. Post-swap: health 200 in ~10s, served version `0.0.490`, 312 changelog entries, source == container (md5, 5 files), volumes 101 → 101, **0 tracebacks**, data intact (2 users / 4 agents / 11 instructions / exactly 1 main build / head `mainbuild01`).
★**Not claimed:** the SharePoint/OneDrive speed work — the headline of this release — was **not** exercised against a live Microsoft connection. It ships on upstream's tests, not on my own live proof.

### FE insight panel — PROVEN (2026-07-27)
The last unproven piece of `0.0.489.3`. Rendered headless against the live app, admin cookie, report `ae33d6ee-5f24-446f-ad26-c8cedb75042e` (the only artifact carrying `content.insights` — find one with `select id, report_id from artifacts where (content->'insights') is not null`).
- Panel header, headline, **3 findings on expand, 0 console errors**, version chip `v0.0.490`. Screenshot `scratchpad/d2_panel.png`. **No fix needed — nothing to ship.**
- ★The check script tested for `"What this means"` and returned False: `ArtifactInsights.vue` puts `uppercase` on the label, and Playwright's `inner_text()` returns *rendered* text (`WHAT THIS MEANS`). Assert on case-insensitive text, or on the locale key's value lowercased — a CSS text-transform will otherwise read as a missing element.
- Recipe: `scratchpad/d2_panel.py` — mints a JWT via `get_jwt_strategy().write_token(u)`, sets cookie `auth.token` on `domain=localhost`, hits `http://localhost:3000/reports/<id>` from **inside** `bow-app-cai` (playwright + chromium are already in the image; pytest is not).

### Pre-push verification (2026-07-27)
- Fork suite **269 passed / 4.38s** and the 8 port-touched unit files **179 passed / 4m40s**, both run against the **source tree** (the thing being pushed), not the container.
- ★Tests cannot run inside `bow-app-cai` — `No module named pytest`, it is built `--no-dev`. Use the `docker run … --tmpfs …:uid=999,gid=999` recipe at the top of this file.
- Commit plan at `scratchpad/commit-plan.sh`: 4 commits by concern (security/lockfile · v0.0.490 port · fork features · release metadata) + a one-commit fallback. Every group `git add --dry-run`'d — 66 paths resolve, exactly matching `git status --porcelain | wc -l`. ★Grouping is by *concern*, not verified per release; files touching both a fork release and the port sit in the port commit.

### Bake (2026-07-27) — work survives this laptop three ways
- `cityagentinsights:0.0.490` **tagged** onto `d4f4a8f3e21a`. ★It had only ever been `:local` — one rebuild would have orphaned and deleted it.
- `_backups/db-0.0.490-bake-20260727.dump` (837K, `pg_restore -l` → 961 objects)
- `_backups/src-0.0.490-bake-20260727.tgz` (296M, `.git` included, all 66 uncommitted files verified present) — ★contains `.env`, so it holds `BOW_ENCRYPTION_KEY` **and** the postgres password. `chmod 600`. Treat as a secret; never put it anywhere shared.
- `_backups/img-0.0.490-20260727.tgz` (1.5G, `gzip -t` OK, manifest `RepoTags: ["cityagentinsights:0.0.490"]`, 22 layers) → `docker load -i`.
- Docker holds 134GB images + 113GB build cache, 110GB free. `docker builder prune` reclaims ~19GB when wanted.

### State
Live `0.0.490`, head `mainbuild01`, 2 users, 4 agents, volumes 101. **UNCOMMITTED (66 files)** — `.7` through `.11` plus this entire port exist **only on this laptop**; GitHub is still at the `0.0.489.6` baseline. Rahul pushes everything himself, together. Now also mirrored in the three bake artifacts above.

### Open
Push everything (plan ready, tests green) · SharePoint/OneDrive speed work unproven live — **blocked on Microsoft creds** (tenant id, client id, secret, one site URL; ~30 min once they land) · `insights.citygpt.xyz` **reinstall** (carry `.env` — it holds the new DB password) · Local Runtime UI → profile modal · per-user admin control of members' MCP/API keys · `mcp.py` protocol identifiers · Windows `.exe` hardware-blocked · 2 stopped test stacks (`bow-app-fresh`, `bow-app-dev`) still hold volumes.
