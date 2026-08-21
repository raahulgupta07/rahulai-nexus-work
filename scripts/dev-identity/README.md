# Local identity rig

An OpenLDAP directory and a Keycloak realm, so the three sign-in doors — local
password, directory, single sign-on — can be tested against real servers.

★**Development only.** Every password here is in git on purpose; they protect
nothing but a directory of invented people. Never point a real installation at
this.

```bash
docker compose -f scripts/dev-identity/docker-compose.identity.yaml up -d
./scripts/dev-identity/setup-keycloak.sh

docker cp scripts/dev-identity/kc-forwarder.py dash-app:/app/backend/
docker exec -d -w /app/backend dash-app python kc-forwarder.py   # ★ see below

docker cp scripts/dev-identity/login-matrix.py dash-app:/app/backend/
docker exec -w /app/backend dash-app python login-matrix.py
```

★★★**The matrix needs a local account this rig does NOT create.** It signs in as
`localmatrix@cityagent.io` / `LocalPass123!` and checks its roster row, but
nothing here provisions it — unlike the LDAP and Keycloak users, which the
compose file and `setup-keycloak.sh` do. Where it is absent the rig reports 2
failures, and one of them is **L1 "a local member can still sign in"** — the
load-bearing test for the `0.0.521.5` lockout, where enabling LDAP locked out
every locally-created member. So a missing fixture reads exactly like that
defect having returned. It cost a diagnosis on 2026-08-20. Plant it first:

```python
# inside dash-app, /app/backend
from fastapi_users.password import PasswordHelper
u = User(email="localmatrix@cityagent.io", name="Local Matrix",
         hashed_password=PasswordHelper().hash("LocalPass123!"),
         is_active=True, is_verified=True, is_superuser=False)
# ...then a Membership row on the org, role "member"
```

★★★**`kc-forwarder.py` dies with the container.** Every `docker compose up -d
app` kills it, `localhost:8180` stops resolving inside the container, and the
entire single-sign-on half of the matrix fails in a way that looks like Keycloak
being down. Re-start it after any recreate, before running either rig.

The matrix is **destructive on the database it runs against** — it creates
accounts and links identities. Development installs only.

## What the users are for

The realm's value is the DIFFERENCES between its users. Four of them exist to
reproduce one refusal branch each, so the sign-in messages are proved against a
real provider rather than a fabricated claims dict.

| user | shape | what it proves |
|---|---|---|
| `verified` | email verified | links cleanly |
| `unverified` | `emailVerified=false` | "your provider reports it unverified" |
| `upnonly@…` | email-shaped **username**, no email attribute | "your provider sent a username" — the Entra/AD FS shape |
| `localunver` | verified at the provider | the LOCAL row is unverified — the other half of the gate |
| `bothdoors` | also in LDAP | merge, directory first |
| `ssofirst` | also in LDAP | merge, provider first |

## Landmines this rig has already cost

- ★★★**A brand-new address is auto-provisioned, gate or no gate.** The linking
  gate only fires when an account ALREADY exists — there is nothing to take over
  otherwise. Refusal tests must plant the account first.
- ★★★**A returning linked identity never reaches the gate.** Lookup is by
  `(provider, account_id)`, so a re-run reports every refusal as LINKED, which
  reads exactly like the gate having been deleted. The fixture resets itself.
- ★★★**A username that is not address-shaped can never match an account**, so it
  only ever creates a new one. `upnonly@cityagent.io` is email-shaped for this
  reason — that is what Entra actually sends as `upn`.
- ★★★**Keycloak's realm profile requires email, firstName and lastName.** A user
  missing any of them cannot authenticate at all: the token endpoint answers
  `invalid_grant / "Account is not fully set up"`, which reads like a wrong
  password. `setup-keycloak.sh` lifts the email requirement for the `upnonly`
  case and still supplies a name.
- ★★★**`auto_provision_users` off means a correct directory password gets NO
  account** — logged as `not_provisioned`, deliberately distinguishable from a
  typo. It looks like the directory door is broken.
- ★★**An OIDC issuer must be one URL both the browser and the server can reach.**
  `kc-forwarder.py` forwards the container's own `localhost:8180` to the rig so
  that single URL is correct on both sides, without editing `/etc/hosts`.
- ★★**`bitnami/openldap:2.6` no longer resolves** — Bitnami moved its back
  catalogue to `bitnamilegacy`, and the pull fails with a bare "not found".
- ★Keycloak is on **8180**: Docker Desktop already holds 8080, and the bind
  failure reads as Keycloak being broken.

## Wiring the application to it

LDAP: Settings ▸ Identity Provider (per-org, hot). SSO: the provider block in
`instance_settings` (instance-global, hot). ★The SSO write schema field is
`providers`, not `oidc_providers` — pydantic drops an unknown key silently, so
the update reports success and changes nothing.

★★**`PUT /api/enterprise/ldap/config` used to REPLACE the whole block** (fixed
in `0.0.543.16`; on any older build every field you leave out is reset to its
pydantic DEFAULT and the request still answers 200). Omitting
`enabled` switches directory sign-in off for the organization; omitting
`auto_provision_users` refuses only BRAND-NEW people while existing accounts keep
working, which reads as an intermittent directory fault. The bind password is the
one field deliberately preserved on omission — nothing else is. **Always send the
full field set**, and re-read the config afterwards rather than trusting the 200.
Measured 2026-08-20; both halves of that bit during one hour of testing.
