# Installing and upgrading CityAgent Insights

- [Upgrade](#upgrade) — one command
- [Fresh install](#fresh-install)
- [Behind a reverse proxy](#behind-a-reverse-proxy) — read this if the new interface never appears
- [Rollback](#rollback)
- [What a version number means](#what-a-version-number-means)
- [Upgrading from 0.0.482.x](#upgrading-from-00482x)
- [Troubleshooting](#troubleshooting)
- [Appendix: the manual steps](#appendix-the-manual-steps)

---

## Upgrade

```bash
./preflight.sh          # optional: see where you are before changing anything
./upgrade.sh
```

That is the whole procedure. The script takes the backups, tags the rollback
image, pulls, builds, verifies the build actually took, swaps, and waits for
health — and it **stops** rather than continuing past any failed check.

If the host runs more than one stack it will refuse to guess, and print the
exact flag to name the one you mean:

```bash
./upgrade.sh --project cityagentinsights
```

Other modes:

| | |
|---|---|
| `./upgrade.sh --dry-run` | backup, tag and pull only — no build, no swap |
| `./upgrade.sh --rollback` | return to the previously tagged image |
| `./upgrade.sh --help` | usage |

Afterwards, hard-refresh the browser (**Cmd/Ctrl + Shift + R**). The open tab
keeps running the old bundle until it reloads.

### Why a script rather than a checklist

The manual upgrade is eight steps and **four of them fail silently** — they do
not error, they look exactly like success:

| Missed step | What actually happens |
|---|---|
| No database dump | No route back to today's data |
| No image tag | `build` re-points `:local`, the daemon collects the orphan, and there is nothing to roll back to. This has already cost two working images. |
| No `FE_CACHEBUST` | Docker reuses the cached `COPY ./frontend` layer; the build succeeds and ships the **old interface** |
| No pre-swap check | You deploy a build that did not take |

Each is a hard gate in the script, so none of them is the operator's problem
any more.

### Settings

Both are environment variables, if the defaults do not suit:

| | Default |
|---|---|
| `CITYAGENT_BACKUP_DIR` | `~/cityagent-backups` |
| `CITYAGENT_KEEP_BACKUPS` | `5` (older dumps pruned) |
| `CITYAGENT_PROJECT` | none — same as `--project` |

---

## Fresh install

```bash
git clone <repo-url>
cd cityagent-coworker-ai

cp .env.example .env
chmod 600 .env
```

Now edit `.env` and set the three values marked **REQUIRED**. The generator
commands are in the file.

> **`BOW_ENCRYPTION_KEY` is generated once and must never change.** It decrypts
> every stored credential — connector passwords, OAuth refresh tokens, LDAP bind
> passwords, SSO client secrets.
>
> If it is left empty the app does **not** fail. It quietly mints a new random
> key at every startup and keeps it in memory only, so each restart orphans
> everything the previous run encrypted. Nothing errors; credentials just stop
> working, one restart at a time.
>
> Set it, then copy `.env` somewhere off the server. A database backup will not
> recover it.

Then build and start:

```bash
docker compose -p cityagentinsights -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
docker compose -p cityagentinsights -f docker-compose.dev.yaml up -d
```

First build takes 10–15 minutes; later ones are ~2–4 minutes thanks to the
BuildKit cache mounts. All migrations run automatically against the empty
database — that is just schema creation.

Check it came up:

```bash
./preflight.sh
```

Then open the app. **The first account created becomes the owner/admin** —
there is no seeded admin and no password to look up, so do this before the box
is reachable by anyone else. Onboarding then asks for one thing: an OpenRouter
key.

Seeding creates three public agents on that first signup: **Microsoft Fabric**
and **Power BI** (both zero-config, each member connects their own Microsoft
account by device code) and **City Mart Retail** (a sample warehouse, 11 tables,
with teaching instructions and conversation starters). Once the model key is
saved, any seeded agent still missing its overview learns itself in the
background.

---

## Behind a reverse proxy

The app serves two kinds of thing, and they need opposite caching. Get this
wrong and the upgrade succeeds while every existing browser stays on the old
interface forever.

| Path | What the app sends | Why |
|---|---|---|
| `/` and any HTML | `cache-control: no-cache` | must be revalidated every load — it names the current bundle |
| `/_nuxt/*` | `max-age=31536000, immutable` | filenames contain a content hash, so a changed file is a changed name |

**Pass both through unchanged.** A proxy that adds caching to `/` pins users to
whichever `index.html` it cached, which points at old `/_nuxt/` hashes, which
are themselves cached forever — the whole UI freezes, and it looks like the
deploy failed.

Check what your proxy actually returns, not what you configured:

```bash
curl -sI https://your-host/ | grep -iE 'cache-control|age|cf-cache-status|x-cache'
```

`cache-control: no-cache` and no `age` header is correct. Anything else —
`max-age` on the HTML, a non-zero `age`, `cf-cache-status: HIT` — means
something in front is caching the entry point.

**Caddy** needs nothing; it does not cache by default. Just reverse-proxy and
leave the upstream headers alone:

```caddy
your-host {
    reverse_proxy localhost:8095
}
```

**nginx** — do not add `proxy_cache` for HTML, and do not set `expires` on `/`:

```nginx
location / {
    proxy_pass http://127.0.0.1:8095;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
}
```

**Cloudflare** is the usual culprit. Its default rules leave HTML alone, but a
"Cache Everything" Page Rule or Cache Rule will serve a stale `index.html`
indefinitely. Either scope such a rule to `/_nuxt/*` only, or add a bypass for
HTML. After changing it, purge the cache once — the rule stops new caching but
does not evict what is already stored.

### If a browser is still stuck after all that

Almost always a **service worker** left behind by whatever ran on that hostname
before this app. A worker intercepts every request and answers from its own
cache, so the server can be fully updated while one browser never sees it.

★ A hard-refresh does **not** clear this. `Cmd/Ctrl + Shift + R` bypasses the
HTTP cache, not a controlling worker.

The app removes any worker it finds on its own origin at startup and reloads
once, so this heals itself on the next visit. To confirm, or to fix a browser
by hand: **DevTools → Application → Service Workers → Unregister**, then
reload. DevTools → Network will also show `(ServiceWorker)` in the Size column
for requests a worker answered.

Note that a worker on `example.com` cannot affect `app.example.com` — scope is
per-origin, so only something previously served from this exact hostname can
cause it.

---

## Rollback

```bash
./upgrade.sh --rollback
```

Retags the previous image and restarts. **The database is not touched** — that
is almost always what you want, because the usual reason to roll back is
behaviour, not data.

Only if a migration is genuinely at fault, restore the dump as well. The
upgrade printed the exact command for its own run; it looks like:

```bash
docker exec -i bow-postgres-cai pg_restore -U bow -d bagofwords -c \
  < ~/cityagent-backups/cityagent-<version>-<timestamp>.dump
```

> Never run `docker compose down -v`. The `-v` deletes the volumes — the whole
> database and every uploaded file. There is no undo.

---

## What a version number means

```
0.0.489      pure upstream port   — upstream bagofwords v0.0.489, ported as-is
0.0.489.3    our own release      — fork changes on top of upstream 489
```

A plain three-part number is an upstream port; a fourth part is work that exists
only in this fork. Both get a `CHANGELOG.md` entry.

So `0.0.482.1 → 0.0.485` is not a skipped release. It is the next release, which
happened to be an upstream port. There was never a `0.0.482.2`.

---

## Upgrading from 0.0.482.x

A 27-release jump. Specifics:

**Six migrations run, all additive.** Nothing is dropped and nothing existing is
rewritten:

| Migration | Effect |
|---|---|
| `ca03putbl01act` | adds `is_active`, backfilled to match current behaviour |
| `ca04learnprog01` | creates `learn_progress` |
| `ca05learnprog02` | adds a column to that new table |
| `ca06localrt01` | creates `local_runtimes`, `local_runtime_jobs` |
| `ca07lrfolders01` | adds columns to a new table |
| `ca08lrtoggleoff` | changes a column **default** — future rows only |

Head afterwards: `ca08lrtoggleoff`.

**New feature flags all default off** — `HYBRID_LOCAL_RUNTIME`,
`HYBRID_LOCAL_FOLDER_ATTACH`, `HYBRID_PER_USER_TABLE_SELECT`,
`HYBRID_LEARN_PROGRESS`, `HYBRID_APP_ANALYTICS`. Nothing new switches on unless
`.env` asks for it.

**Three flags default on**, because they are the point of `0.0.489.3`:

- `HYBRID_ARTIFACT_COMPLETENESS_GATE` — a dashboard built on truncated data is
  refused with the reason, instead of silently rendering wrong numbers
- `HYBRID_ARTIFACT_RENDER_PREFLIGHT` — generated dashboard code must parse
  before it is stored
- `HYBRID_ARTIFACT_INSIGHTS` — every dashboard gets a written summary whose
  every figure is checked against the dashboard's own data first

★ **The visible change:** a dashboard that previously rendered on partial data
will now come back asking the agent to aggregate first. That is the fix working,
not a regression. It is the reason the release exists — a dashboard was
reporting 56.4B against a true 98.9B.

Set any of them to `false` in `.env` to opt out.

**Seeding does not re-fire.** Default agents seed only on a completely empty
database, so an existing install can never be re-seeded.

---

## Troubleshooting

Start with `./preflight.sh` — it reports the version from all three places it
can disagree (repo file, container, served API), git state, config, health,
migration head, disk, and whether any backup exists.

**Build succeeded but the interface is unchanged.**
The `COPY ./frontend` layer was cached. `upgrade.sh` catches this before
swapping. Doing it by hand, rebuild with a fresh
`--build-arg FE_CACHEBUST=$(date +%s)`, and if it persists run
`docker builder prune`.

**`/api/health` returns 404.**
Expected. The health path is `/health` — the SPA catch-all owns any `/api/*` the
backend does not claim.

**Everyone logged out, or connectors report bad credentials.**
`BOW_ENCRYPTION_KEY` changed. Restore it from the `.env` backup (`upgrade.sh`
writes `.env.bak-<version>` before touching anything). If no backup exists the
stored credentials cannot be recovered and every connector must be reconnected.

**App will not start after an upgrade.**
Migrations run before the server binds, so read that output first:

```bash
docker logs bow-app-cai 2>&1 | grep -iE 'alembic|error|traceback' | head -30
```

**Version in the sidebar did not change.**
It is fetched at runtime from `/api/settings`, so an open tab can still be on
the old bundle. Hard-refresh. If it persists the swap did not happen — check
`docker ps` for the container's image ID.

**The upgrade worked but one browser still shows the old interface — and a
different browser shows the new one.**
The server is fine; that browser is being served from somewhere else. Either a
proxy is caching the HTML entry point, or a service worker left by a previous
occupant of the hostname is answering requests locally. A hard-refresh fixes
neither. See [Behind a reverse proxy](#behind-a-reverse-proxy).

**Disk filling up.**
Tagged rollback images accumulate, and this image is ~6 GB.
`docker system df` shows the reclaimable total; `docker images | grep pre-`
lists them. Keep at least the previous release.

---

## Appendix: the manual steps

For when the script cannot run, or something has already gone wrong. This is
exactly what `upgrade.sh` automates.

```bash
# 0. Identify the stack
docker ps --filter label=com.docker.compose.service=app \
          --format '{{.Names}} {{.Label "com.docker.compose.project"}}'

# 1. Back up the database — the real rollback
mkdir -p ~/cityagent-backups
docker exec bow-postgres-cai pg_dump -U bow -d bagofwords -Fc \
  > ~/cityagent-backups/pre-upgrade-$(date +%Y%m%d-%H%M).dump
ls -lh ~/cityagent-backups/          # STOP if it is not several MB

# 2. Back up .env
cp .env .env.bak-$(cat VERSION)

# 3. Tag the running image — without this there is no rollback target
docker tag cityagentinsights:local cityagentinsights:pre-$(cat VERSION)

# 4. Pull
git pull                              # STOP if it refuses; do not force

# 5. Build
docker compose -p cityagentinsights -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app

# 6. Verify the IMAGE, not the running container
docker run --rm --entrypoint sh cityagentinsights:local -c 'cat /app/VERSION'
#    STOP unless this shows the new version

# 7. Swap
docker compose -p cityagentinsights -f docker-compose.dev.yaml up -d app

# 8. Check
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8095/health
docker exec -w /app/backend bow-app-cai alembic current | tail -1
```

Names above are this repo's defaults. Confirm yours with step 0 — the `bow` and
`bagofwords` names are inherited from the upstream project and are deliberately
unchanged, since renaming them breaks a running install.
