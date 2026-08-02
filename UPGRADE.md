# Installing and upgrading CityAgent Insights

> **Set these once per session before running any database command.** Postgres
> applies `POSTGRES_USER` / `POSTGRES_DB` only when it creates an empty data
> directory, so an installation keeps the names it was built with forever, and
> those names differ by install age. Reading them out of `.env` is the only way
> to be right on both:
>
> ```bash
> set -a; . ./.env; set +a
> echo "$POSTGRES_USER / $POSTGRES_DB"
> ```
>
> If that prints nothing, the install predates those lines being pinned — it is
> `bow` / `bagofwords`. Confirm with
> `docker exec dash-postgres psql -U bow -lqt`.

- [Upgrade](#upgrade) — one command
- [Before every release: the browser smoke gate](#before-every-release-the-browser-smoke-gate) — **required**
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

## Before every release: the browser smoke gate

Run this against the built container **before** you ship it. It is not optional.

```bash
docker exec -w /app/backend dash-app python scripts/browser_smoke.py
```

Roughly 20 seconds. Exit 0 = ship, exit 1 = do not ship, and the output names
the artifact and the exact error. It discovers one dashboard, one document and
one deck from the database — no report id, dataset, connector, tenant or
Microsoft account is involved — and skips cleanly on a fresh install that has
no artifacts yet.

It opens each one in a real Chromium (already in the image, the same one PDF
export uses) and fails on any uncaught page error, **any failed network
request**, any "failed to render" / "is not defined" text, or an artifact frame
that produced nothing.

### The rule it enforces

> **A server-side render is never proof of a browser-side feature.**

Every dashboard in the product once rendered *"Dashboard failed to render —
React is not defined"* for a full release. Two `<script>` tags carried
`crossorigin`; the artifact iframe runs at an opaque origin
(`sandbox="allow-scripts"` without `allow-same-origin`), our `/libs/` responses
send no CORS headers, and the browser refused React.

It survived 3,330 passing tests, a live end-to-end sweep, and every exported
artifact being opened and read page by page — because dashboards were only ever
verified through **PDF export, which inlines the libraries server-side** and
therefore *cannot observe a browser-only failure*. The verification path was
structurally incapable of seeing the fault. Passing tests were never the
problem; the missing browser was.

★ The load-bearing assertion is **zero failed requests**. A CORS-blocked script
is not an exception and not an HTTP error — the server logs a clean 200 — so
`requestfailed` is the only place the browser reports it.

To prove the gate can still fail, it ships with its own reproduction of that
outage:

```bash
docker exec -w /app/backend dash-app python scripts/browser_smoke.py --self-test
```

It builds the broken iframe in the browser (no shipped file is touched) and
asserts the checker flags it. Run it whenever you change the checker.

---

## Fresh install

```bash
git clone <repo-url>
cd rahulai-nexus-work

cp .env.example .env
chmod 600 .env
```

Now edit `.env` and set the two values marked **REQUIRED**. The generator
commands are in the file.

> **`DASH_ENCRYPTION_KEY` is generated once and must never change.** It decrypts
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

Also set **`DOMAIN`** to the DNS name this server answers on. It is required
for a production install and nothing complains if you skip it — Caddy falls
back to `localhost`, issues itself a certificate for a name nobody visits, and
reports healthy while every browser refuses the site. That name must already
resolve to this machine, and inbound **80 and 443** must be open, before the
first start: port 80 is how the certificate is issued.

Then build and start:

```bash
docker compose build --build-arg FE_CACHEBUST=$(date +%s) app
docker compose up -d
```

That is the **production** stack: Caddy terminates TLS on 80/443, and neither
the application nor Postgres is published on a host port.

> **On a server, use exactly those two commands.** Adding
> `-f docker-compose.dev.yaml` publishes Postgres on `POSTGRES_PORT` and the
> app on `APP_PORT`, unencrypted. On a cloud host whose security group allows
> those ports, that puts the database on the internet with the password you
> just generated. The development file is for a laptop.
>
> The two are also not interchangeable later: they use different volume names
> (`postgres_data` vs `postgres_data_dev`), so switching an existing install
> from one to the other silently gives it an empty database.

For a local development stack without SSL, and only then:

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
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

> **The production stack already includes Caddy**, configured from the
> `Caddyfile` in this repository, and it is correct as shipped — there is
> nothing to do here. This section is for an *additional* proxy in front of it:
> a company load balancer, an nginx on the host, Cloudflare. The upstream
> addresses below assume a development stack, where the app is published on
> `APP_PORT`; production publishes no host port, so anything in front must
> reach Caddy on 80/443 instead.

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
docker exec -i dash-postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  < ~/cityagent-backups/cityagent-<version>-<timestamp>.dump
```

> Never run `docker compose down -v`. The `-v` deletes the volumes — the whole
> database and every uploaded file. There is no undo.

---

## What a version number means

```
0.0.489      pure upstream port   — upstream v0.0.489, ported as-is
0.0.489.3    our own release      — fork changes on top of upstream 489
```

A plain three-part number is an upstream port; a fourth part is work that exists
only in this fork. Both get a `CHANGELOG.md` entry.

The fourth part changed meaning on 2026-07-31. It used to mark a *partial*
upstream port; it now marks *our own work* on top of a ported release. So a
suffix on an entry older than `0.0.502` may mean the earlier thing. When a port
is partial, that is stated in the changelog body — never encoded in the number.

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
`DASH_ENCRYPTION_KEY` changed. Restore it from the `.env` backup (`upgrade.sh`
writes `.env.bak-<version>` before touching anything). If no backup exists the
stored credentials cannot be recovered and every connector must be reconnected.

**App will not start after an upgrade.**
Migrations run before the server binds, so read that output first:

```bash
docker logs dash-app 2>&1 | grep -iE 'alembic|error|traceback' | head -30
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
exactly what `upgrade.sh` automates. The same sequence is written out
step-by-step, with what to check after each one, under **"Upgrading by hand"**
in [README.md](README.md) — read that version if you are doing this for the
first time.

```bash
# 0. Identify the stack — including the compose file it was started with.
#    ★ Do not guess this. Production is one file, development is two, they use
#    different volume names, and building with the wrong one hands the app an
#    empty database without printing anything.
docker ps --filter label=com.docker.compose.service=app \
          --format '{{.Names}} {{.Label "com.docker.compose.project"}} {{.Image}}'
IMG=$(docker inspect -f '{{.Config.Image}}' dash-app)
docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' dash-app

#    Set COMPOSE to -f plus EACH filename that printed, same order. Judge by
#    the names, not the count: a stack started with `-f docker-compose.dev.yaml`
#    alone also prints one line, and it is development.
COMPOSE="-f docker-compose.yaml"          # production
# COMPOSE="-f docker-compose.dev.yaml"    # development, started with that file alone
# COMPOSE="-f docker-compose.yaml -f docker-compose.dev.yaml"   # development, both

# 1. Back up the database — the real rollback
#    ★ Dump to a file INSIDE the container and copy it out. Piping -Fc through
#    a terminal can corrupt the binary format, and pg_restore then rejects it
#    with "did not find magic string in file header".
mkdir -p ~/cityagent-backups
source .env
docker exec dash-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  -f /tmp/pre-upgrade.dump
docker cp dash-postgres:/tmp/pre-upgrade.dump \
  ~/cityagent-backups/pre-upgrade-$(date +%Y%m%d-%H%M).dump
ls -lh ~/cityagent-backups/          # STOP if it is not several MB

# 2. Back up .env
cp .env ~/cityagent-backups/env-$(cat VERSION)

# 3. Tag the running image — step 5 rebuilds over this exact tag, so without
#    this there is no rollback target
docker tag "$IMG" "cityagentinsights:pre-$(cat VERSION)"

# 4. Pull
git status --short                    # STOP if this prints anything
git pull --ff-only                    # STOP if it refuses; do not force

# 5. Build
docker compose $COMPOSE build --build-arg FE_CACHEBUST=$(date +%s) app

# 6. Verify the IMAGE, not the running container
docker run --rm --entrypoint sh "$IMG" -c 'cat /app/VERSION'
#    STOP unless this shows the new version

# 7. Swap
docker compose $COMPOSE up -d app

# 8. Check — from inside the container, so this works on production too
#    (production publishes no host port; curl localhost:8095 cannot answer)
docker exec dash-app curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/health
docker exec -w /app/backend dash-app alembic current | tail -1
```

Names above are this repo's defaults — confirm yours with step 0. In particular
the postgres user and database: `.env` says what the application connects **as**,
while the volume decides what actually exists. An installation created before
the rename still holds `bow` / `bagofwords` no matter what `.env` reads, so ask
the running database rather than assuming:

```bash
docker exec dash-postgres psql -U dash -lqt || docker exec dash-postgres psql -U bow -lqt
```
