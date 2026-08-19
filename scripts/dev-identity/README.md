# Local identity rig

An OpenLDAP directory and a Keycloak realm, so the three sign-in doors — local
password, directory, single sign-on — can be tested against real servers.

★**Development only.** Every password here is in git on purpose; they protect
nothing but a directory of invented people. Never point a real installation at
this.

```bash
docker compose -f scripts/dev-identity/docker-compose.identity.yaml up -d
./scripts/dev-identity/setup-keycloak.sh

docker cp scripts/dev-identity/login-matrix.py dash-app:/app/backend/
docker exec -w /app/backend dash-app python login-matrix.py
```

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
