# CityAgent Insights — Docker runbook

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

> **`docker compose config` prints secrets.** It resolves `.env` and renders
> the database password and every key in plain text on your terminal. It is the
> right way to see what Compose will actually run — just do not paste its output
> into a ticket, a chat or a log.

Every operation as plain `docker` / `docker compose` commands. No shell scripts.

`UPGRADE.md` covers the same ground using `preflight.sh` and `upgrade.sh`. This file
is the equivalent for anyone who would rather run the steps themselves. The scripts
do nothing that is not written out below.

Run everything from the repository root.

> **The names changed.** Containers were `bow-app-cai` / `bow-postgres-cai`, the
> configuration file was `bow-config.yaml`, and the environment variables were
> prefixed `BOW_`. They are now `dash-app`, `dash-postgres`, `dash-config.yaml`
> and `DASH_`.
>
> Old names still work: the application resolves either prefix, and the loader
> accepts either configuration filename, so an installation that has not yet
> renamed its `.env` keeps running. It logs which old names it fell back to.
> The database user and database name were renamed too, from `bow` /
> `bagofwords` to `dash` / `dash_insights` — but only for installations created
> *after* the rename. Postgres applies those names once, when it initialises an
> empty data directory, so an older install keeps `bow` / `bagofwords` forever
> regardless of what `.env` says. Ask the running database, as above.

---

## What is running

**First, find out which of the two stacks you are on.** This repository ships a
production compose file and a development one. They are genuinely different
deployments, not a flag, and every command on this page depends on which you
have. Ask the container — Docker recorded the answer when it started, so this
is fact rather than memory:

```bash
docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' dash-app
IMG=$(docker inspect -f '{{.Config.Image}}' dash-app)
```

★ **Judge by the filenames, not by how many there are.** If
`docker-compose.dev.yaml` appears anywhere in that list, this is a development
stack — whether it printed alone or after the base file. This repository's own
development stack was started with `-f docker-compose.dev.yaml` by itself and
prints a single line, so "one file" is not evidence of production.

Set `COMPOSE` to `-f ` plus **each** filename that printed, in the same order,
once per session — every `docker compose` command below repeats it:

```bash
COMPOSE="-f docker-compose.yaml"          # production
# COMPOSE="-f docker-compose.dev.yaml"    # development, that file alone
# COMPOSE="-f docker-compose.yaml -f docker-compose.dev.yaml"   # development, both
```

| | Production | Development |
|---|---|---|
| Compose files | `docker-compose.yaml` | both files |
| Reached at | `https://$DOMAIN`, through Caddy on 80/443 | `http://localhost:8095` |
| App published on host | **no** | yes, `APP_PORT` → 3000 |
| Postgres published on host | **no** | yes, `POSTGRES_PORT` → 5432 |
| Volumes to protect | `postgres_data`, `uploads_data` | `postgres_data_dev`, `uploads_data_dev` |

Constant across both: app container `dash-app`, database container
`dash-postgres`, compose project `cityagentinsights`, health path `/health`
(**not** `/api/health`, which returns 404), and the image tag — read it from
`$IMG` above rather than assuming `cityagentinsights:local`, because a
per-release tag such as `cityagentinsights:0.0.503.10` is normal here.

Database user and database name come from **your** `.env` —
`$POSTGRES_USER` / `$POSTGRES_DB`. Installations created before the rename hold
`bow` / `bagofwords`; newer ones `dash` / `dash_insights`. Every `psql` /
`pg_dump` line below uses the shell variables, so set them once per session
(above) and the commands are correct on either.

> **★ Never mix the two.** Running production commands against a development
> stack, or the reverse, mounts the *other* set of volumes: the container
> starts, passes its health check, and serves an empty installation, while all
> the real data sits in a volume nothing is reading. Nothing errors at any
> point. This is the single most expensive mistake available on this page.

> **★ Never run the development file on a server.** It publishes Postgres on a
> host port. Where a cloud security group allows that port, the database is
> exposed to the internet with the password from `.env`, and application
> traffic bypasses TLS.

---

## Daily operations

**Status**

```bash
docker compose $COMPOSE ps
```

**Health and version**

```bash
docker exec dash-app curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/health
docker exec dash-app curl -s http://localhost:3000/api/settings -o /tmp/s.json
docker exec dash-app python3 -c "import json;print(json.load(open('/tmp/s.json'),strict=False)['version'])"
```

> **These run inside the container on purpose**, so they are correct on both
> stacks. A production install publishes nothing on the host, so
> `curl localhost:8095` there returns *connection refused* — which reads like an
> outage and is not one. From a browser, use `https://$DOMAIN`.

On production, check the edge separately. Plain HTTP answers **308** — that is
the redirect to HTTPS, not a fault:

```bash
curl -s  -o /dev/null -w 'caddy-80  %{http_code}\n' http://localhost/health    # 308
curl -sk -o /dev/null -w 'caddy-443 %{http_code}\n' https://localhost/health   # 200
```

> `HEAD` returns **405** on this server — the single-page-app catch-all owns it. Use
> `GET`. Also write `curl` output to a file before parsing it; piping it straight
> into a parser can truncate the response.

**Logs**

```bash
docker logs --since 30m dash-app
docker logs --since 30m dash-app 2>&1 | grep -c 'Traceback (most recent call last)'
```

**Restart the app without rebuilding** — picks up backend files copied in, nothing else

```bash
docker restart dash-app
```

**Stop and start**

```bash
docker compose $COMPOSE stop
docker compose $COMPOSE up -d
```

> **Never run `down -v`.** The `-v` deletes the named volumes — the entire database
> and every uploaded file. `down` on its own is safe; `down -v` is not recoverable
> without a backup.

---

## Upgrade

Seven steps, in this order. Steps 1, 3 and 4 are the ones that are painful to skip.

### 1. Tag the running image first

```bash
RUNNING=$(docker exec dash-app cat /app/VERSION)
docker tag "$IMG" "cityagentinsights:$RUNNING"
docker tag "$IMG" "cityagentinsights:pre-next"
```

> **Rebuilding re-points that same tag and the old image is deleted.** A tag is the
> only thing that keeps it alive. Two working rollback images have already been lost
> this way. Tag before every build, without exception.

### 2. Get the new code

```bash
git pull --ff-only
```

> If this fails with *refusing to merge unrelated histories*, the checkout predates the
> repository reset. Fix it once with `git fetch origin && git reset --hard origin/main`,
> after confirming there is no local work to lose.

### 3. Build, and bust the frontend cache

```bash
docker compose $COMPOSE build --build-arg FE_CACHEBUST=$(date +%s) app
```

> **Without `FE_CACHEBUST` the frontend layer is silently reused.** The build exits 0,
> reports success, and produces an image whose interface is the old one. Backend changes
> would ship; frontend changes would not.

### 4. Verify inside the new image, before touching the container

```bash
docker run --rm --entrypoint sh "$IMG" -c '
  cat /app/VERSION
  sed -n 3p /app/CHANGELOG.md
'
```

Add a check for something the release actually changed — a backend marker and, if the
interface changed, a string in the built bundle:

```bash
docker run --rm --entrypoint sh "$IMG" -c '
  grep -c "<some new function name>" /app/backend/<file>
  grep -rl "<some new interface text>" /app/frontend/dist | head -3
'
```

> Verify the **image**, not the running container. A container still on the old image
> will happily confirm the old state and look like a pass.
> Note `grep -c` exits 1 when the count is zero, which reads as a failed command.

### 5. Back up the database

```bash
docker exec dash-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > backup.dump
docker cp backup.dump dash-postgres:/tmp/v.dump
docker exec dash-postgres sh -c 'pg_restore -l /tmp/v.dump | wc -l; rm -f /tmp/v.dump'
```

The last line must print a few hundred. A dump is not proven by its exit code — an
empty or truncated file exits 0 too. `pg_restore` usually is not installed on the host,
which is why the check runs inside the container.

### 6. Swap

```bash
docker compose $COMPOSE up -d app
```

Database migrations run automatically at start-up (`alembic upgrade head`, with retries
while the database comes up). Nothing to run by hand.

### 7. Confirm

```bash
for i in $(seq 12); do
  code=$(docker exec dash-app curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/health)
  [ "$code" = "200" ] && { echo "healthy"; break; }
  sleep 10
done
docker exec dash-app cat /app/VERSION
docker exec dash-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc 'select version_num from alembic_version'
docker logs --since 10m dash-app 2>&1 | grep -c 'Traceback (most recent call last)'
docker volume ls -q | wc -l
```

Expected: healthy, the new version, a migration head, **0** tracebacks, and the same
volume count as before.

---

## Rollback

**Find the target by reading its version, never by its tag name.**

```bash
for tag in $(docker images 'cityagentinsights:pre-*' --format '{{.Repository}}:{{.Tag}}'); do
  echo "$tag -> $(docker run --rm --entrypoint sh "$tag" -c 'cat /app/VERSION' 2>/dev/null)"
done
```

> A tag named `pre-0.0.490.7` holds the image **of** the version it was taken from,
> which is `0.0.490.6`. Deriving the rollback target from the name is off by one release
> and fails on a first upgrade. Read `/app/VERSION` out of each candidate and pick the
> one whose version differs from what is running.

Then re-point the tag this stack runs and recreate:

```bash
docker tag cityagentinsights:<the tag you chose> "$IMG"
docker compose $COMPOSE up -d --force-recreate app
```

**A code rollback is not a database rollback.** Migrations already applied stay applied.
They are written to be additive, so an older application normally runs against a newer
schema — but if the release you are undoing added a migration and you need the schema
back as well, restore the dump:

```bash
docker cp backup.dump dash-postgres:/tmp/restore.dump
docker exec dash-postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/restore.dump
docker restart dash-app
```

---

## Backups

**Database**

```bash
docker exec dash-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > db-$(date +%Y%m%d).dump
```

**Image** — the only true offline rollback

```bash
docker save "$IMG" | gzip > img-$(date +%Y%m%d).tgz
# restore on any machine:
docker load -i img-YYYYMMDD.tgz
```

**`.env`** — the one that cannot be regenerated

```bash
cp .env env-backup-$(date +%Y%m%d)
```

> `.env` holds `DASH_ENCRYPTION_KEY` and the database password. **The encryption key must
> never change after installation.** Every stored credential — connector passwords,
> Microsoft refresh tokens, directory binds, single-sign-on secrets — is encrypted with
> it and becomes permanently unreadable if it is replaced, silently and with no error.
> Before reinstalling on any server, copy `.env` off that machine first. Keep the copy
> somewhere private; it is a secret.

---

## Database access

```bash
docker exec -it dash-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker exec dash-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc 'select count(*) from users'
```

> Piping a here-document into `docker exec` needs **`-i`**, or it silently sends nothing
> and appears to succeed.

---

## Tests

**Run the suite against the source tree, not inside the running container.** Mount the
repository read-only and use the image only as the runtime:

```bash
docker run --rm -v "$PWD:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 \
  --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc \
  "$IMG" \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest tests/unit/fork -q -p no:cacheprovider'
```

That is the fast suite — about ten seconds. The full suite is the same command with
`tests/unit` and takes roughly an hour.

> **Do not run this with `docker exec` against `dash-app`.** The image deliberately
> ships only the *built* frontend — `/app/frontend/dist` — and no `/app/locales`. A large
> part of the suite reads interface source files and translation files to check them, so
> inside the container those tests fail on missing files: **73 failures that mean nothing
> except that the sources are not there.** Measured on this release: 707 passed against
> the source tree, 635 passed / 73 failed inside the container, same code.
>
> This also means the same command is what you use to test a tree that is *not* running —
> a merge, a backup, an upstream port. Point `-v` at that directory instead of `$PWD`.

> **The `uid=999,gid=999` on those mounts is load-bearing.** A tmpfs mounts owned by
> root; the container runs as user 999, so without it the test runner cannot create its
> scratch database and *every* test fails at setup — which looks exactly like broken code.
> `PYTHONPYCACHEPREFIX` is the same problem for a read-only source tree.

The image is built without development dependencies, which is why each run installs the
test runner first. It is installed into the throwaway container, not into the image.

**One test is the exception and must run inside the container.** The dashboard-to-PDF
render needs the artifact sandbox libraries, which are downloaded during the Docker build
and are not in a source checkout — so against the source tree it reports `1 skipped`
rather than failing. Run that file where the libraries are:

```bash
docker exec dash-app pip install -q pytest pytest-asyncio
docker exec -w /app/backend dash-app \
  python -m pytest tests/unit/fork/test_pdf_export.py -q -rs -p no:cacheprovider
```

Expect 12 passed and **no skips**. If it reports a skip here, the built frontend is
missing from the image and the export feature is broken in it — worth stopping for.

---

## Fresh install

```bash
git clone <repository> && cd <repository>
cp .env.example .env
```

Fill the required values in `.env`:

| variable | note |
|---|---|
| `POSTGRES_PASSWORD` | choose one; never leave the shipped default |
| `DASH_ENCRYPTION_KEY` | `openssl rand -base64 32` — **set once, never change** |
| `DOMAIN` | **production only, and required there.** The DNS name people will type. Omit it and Caddy silently falls back to `localhost`, issues itself a certificate for a name nobody visits, and reports healthy while browsers refuse the site |
| `OPENROUTER_API_KEY` | or configure the model from the interface after first sign-in |

There is no running container to read `$COMPOSE` from yet, so name the file
you want explicitly. On a server, that is the production one:

```bash
docker compose build --build-arg FE_CACHEBUST=$(date +%s) app
docker compose up -d
```

Caddy asks a certificate authority for a real certificate as soon as it starts,
so before that `up -d`: `DOMAIN` must already resolve to this machine, and
inbound **80 and 443** must be open. Port 80 is how the certificate is issued —
closing it prevents HTTPS rather than hardening it. Then open
**`https://$DOMAIN`**.

On a laptop, and only there, the development stack instead:

```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml build \
  --build-arg FE_CACHEBUST=$(date +%s) app
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
```

Then open `http://localhost:8095`.

**The first account created becomes the administrator** and the workspace is
seeded with three agents. There is no default password to change — so create it
before the machine is reachable by anyone else.

---

## Troubleshooting

**A browser shows the old interface after an upgrade, others show the new one**
A service worker left by a previous occupant of the same hostname is serving cached
files. A hard refresh does **not** fix this — it bypasses the HTTP cache, not a
controlling worker. The application removes any it finds on next load; if it persists,
close every tab for that address and reopen one. Behind a reverse proxy, `/` must be
sent with `no-cache` and `/_nuxt/*` may be cached forever. See `UPGRADE.md`.

**Frontend changes did not appear**
The build was run without `--build-arg FE_CACHEBUST=...`. Rebuild with it and confirm
the image ID changed:

```bash
docker images "$IMG" --format '{{.ID}}'
```

**Application will not start on a brand-new database**
Start-up briefly races itself while creating the scheduler's table. It self-heals within
about fifteen seconds and health returns 200 throughout. If it persists, check the logs
for a genuine migration error.

**Removing a file inside the container is denied**
The application runs as a non-root user:

```bash
docker exec -u root dash-app rm -rf /path
```

**Out of disk during a build**
Old release tags share almost all their layers, so deleting them frees less than their
listed size suggests. The build cache is usually the larger win:

```bash
docker system df
docker buildx du            # splits shared from private bytes honestly
docker builder prune        # frees cache; safe, only slows the next build
```

Keep at least one `pre-*` tag — it is the rollback.

**Checking whether the running code matches the source**

```bash
md5 -q backend/main.py
docker exec dash-app md5sum /app/backend/main.py
```

A mismatch means files were copied into the container by hand and will be lost on the
next recreate. Rebuild the image so the two agree.
