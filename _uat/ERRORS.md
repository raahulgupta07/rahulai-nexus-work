# ERRORS OBSERVED (not fixed, per instruction)

## OBS-1 — membership payload has `email: null` at top level
`GET /api/organizations/{org}/members` returns `"email": null` on the membership
object while the nested `user.email` is correct. Cosmetic/unused? Not fixed.

## OBS-2 — SQLAlchemy overlap warning on DataSource
`relationship 'DataSource.user_data_source_credentials' will copy ... conflicts
with 'DataSource.user_credentials'`. Two relationships map the same FK. Emitted
on every mapper configure. Pre-existing. Not fixed.

## OBS-3 — the DEF-018 description never reaches an integrator on this deploy
`swagger.enabled` is false, so `openapi_url` is None and `/openapi.json` 404s.
The fix for DEF-018 was "explain the null where an integrator looks" — which is
the OpenAPI schema. On a deployment with swagger off, nobody can look. Worth a
decision: ship swagger on, or serve the schema separately. Not fixed.

## OBS-4 — SPA catch-all answers unknown paths 200 + HTML
`/docs` and `/redoc` both return 200 with the SPA shell even though neither
route exists. Same shape as the CityBCP landmine: a 200 is not proof a route is
real. Not fixed.

## DEF-A — "Tables 0" still reported by POST /api/connections/{id}/test
Reproduced on 0.0.543.12, local, fresh duckdb connection "UAT City Mart"
(/app/backend/demo-datasources/citymart_retail.duckdb, 11 real tables).

    POST /api/connections/{id}/test
    -> {"success":true,"connectivity":true,"schema_access":false,"table_count":0,
        "message":"DuckDB database connected: ..."}

    GET /api/connections/{id}
    -> {"table_count":11}

So the same connection reports 11 tables on detail and 0 on the test result, in
the same second. `schema_access:false` is wrong too — the schema was read
(11 tables were indexed at create time).

Route is fine: connection.py:758-766 just forwards
`result.get("table_count", 0)` / `result.get("schema_access", False)`. The
default kicks in because `connection_service.test_connection` does not put those
keys in its dict for this client. Same defaulting shape as the DEF-021 family:
absence rendered as a confident zero.

NOT FIXED, per instruction.

## DEF-B — the trailer notice is written, then never shown to anyone
Reproduced on 0.0.543.12, local, T11.

`sheet_trailer.detect_total_row` produces a notice (proved in T4):
  "The last row of this sheet is a TOTAL of the rows above it (revenue,
   labelled 'TOTAL'), not an observation. It was excluded so aggregates are
   not counted twice."
The strip happens — T11's numbers prove it. But the notice reaches neither the
model nor the screen. Grepped every tool_execution result_json in the run:
no `removed_rows`, no `notice`, no trailer mention anywhere.

Consequence, in the agent's own user-visible words:
  "14,000 (covering order_id 1 through 14,000; excluding 1 trailing empty row;
   **no TOTAL summary row was present**)"
There WAS a TOTAL row. The model looked for one, found none — because the
platform had already removed it without saying so — and then stated its absence
as fact. It even wrote its own defensive TOTAL-detection pass, which found
nothing and cost a codegen round trip.

Right number, false explanation. Same family as the `.543.9` "Synced" label:
the system knew something true and rendered something else. A fact that reaches
the database and not the screen is the same as no fact at all — which is what
the completion_v2 schema comment itself argues.

NOT FIXED, per instruction.

## OBS-5 — LDAP group sync fails hourly, every hour, on this local
    ERROR app.ee.ldap.sync_service — LDAP sync failed for org 7ad85eeb...:
    invalid class in objectClass attribute: group

Org LDAP config on this box:
    group_search_base:      null
    group_search_filter:    (objectClass=group)
    group_member_attribute: member
    url:                    ldap://test-ldap:1389   (container is up)

`(objectClass=group)` is Active Directory syntax. The `test-ldap` fixture is
OpenLDAP, where groups are `groupOfNames`, so the server rejects the filter. So
the immediate cause is a wrong local fixture config, NOT a code defect — I am
not claiming one.

Two things still worth a decision:
1. `group_search_base` is null and a group search was attempted anyway. Nothing
   refused to run against an unset base.
2. It logs at ERROR once an hour forever with nothing on any screen telling an
   admin which field is wrong. Whoever runs a real deploy sees an hourly ERROR
   they cannot act on. Same family as DEF-017: the reason exists, nobody is shown
   it. Pre-existing, predates 0.0.543.12.

NOT FIXED, per instruction.

## DEF-C — LDAP "Preview sync" answers a bare 500, while the reason exists one frame down
Reproduced on 0.0.543.13, local, T16.

    GET /api/enterprise/ldap/sync/preview   -> 500, body: "Internal Server Error"

The traceback names the cause exactly:
    app/ee/ldap/routes.py:113        preview_sync
    app/ee/ldap/sync_service.py:193  preview_sync
    app/ee/ldap/connection.py:211    search_groups
    ldap3.core.exceptions.LDAPObjectClassError:
        invalid class in objectClass attribute: group

Three code paths hit the SAME failure and handle it three different ways:
  * background job  — catches it, logs ERROR, reports "completed with errors"
  * test-connection — swallows it, returns `group_count: null`, HTTP 200
  * preview         — does not catch it at all, 500 with no text

So the admin on Settings ▸ Identity Provider presses Preview, gets a blank
server error, and the one sentence that would tell them their group filter is
Active Directory syntax against an OpenLDAP server never reaches the screen.

★Two further things, both product-side, neither caused by the local fixture:
1. `group_search_base` is None and a group search runs anyway. Nothing refuses
   to search an unset base.
2. USER preview is unavailable because of a GROUP problem. Users sync fine
   (5 found); the whole preview dies on the group half.

Same family as DEF-017 and DEF-B: the product knows why and does not say.
The wrong filter on this box is local config; the 500 is not.

NOT FIXED, per instruction.

## OBS-6 — the identity rig assumes a fixture it does not create
`scripts/dev-identity/login-matrix.py` signs in as `localmatrix@cityagent.io`
(lines 223/225) and checks its roster row (line 320), but nothing in the rig
creates that account — unlike the LDAP and Keycloak users, which the compose
file and `setup-keycloak.sh` provision. On any install where it is absent the rig
reports 2 failures, one of them L1 "a local member can still sign in" — which is
the load-bearing test for the `.521.5` lockout and reads as that defect being
back. Cost me a diagnosis. Worth having the rig plant it, as it plants everything
else. Not fixed.

## OBS-7 — `test_retry_does_not_rescan.py` is intermittently red, and it is not new
Measured 2026-08-20. The fork suite was **3873 passed / 0 failed** and then
**3892 passed / 0 failed** earlier today. After the LDAP work the same suite
reported 4 failed, then 1 failed on an immediate re-run — different tests each
time, all inside `tests/unit/fork/test_retry_does_not_rescan.py`.

Isolated, it fails 3 or 4 of 10, varying run to run:
    test_a_different_query_is_never_served_a_parked_result
    test_separate_runs_never_share_parked_results
    test_without_shared_parking_the_second_attempt_rescans

Proved NOT mine: reverted all four LDAP files to their backups and ran it twice
— 3 failed, then 4 failed. Identical behaviour without the change.

The varying count points at a race in the test, not a stable assertion failure.
It went green in this morning's runs and red this afternoon; the machine is now
also running a docker build and the identity rig, so it looks load-sensitive.

Worth noting because it is exactly the trap CLAUDE.md warns about: a failure in
this suite is not automatically the current change's. Anyone who sees it during
a release and assumes ownership will spend the session in the wrong file.

NOT FIXED, per instruction.

## DEF-D — PUT /api/enterprise/ldap/config was a REPLACE that read as a PATCH — FIXED 0.0.543.16
Found 2026-08-20 while testing DEF-C on 0.0.543.15. It bit me twice in one hour.

`organization_settings_service.update_ldap` (line ~1219) rebuilds the whole block:

    ldap = {"enabled": bool(data.enabled)}
    for f in self._LDAP_FIELDS:
        ldap[f] = getattr(data, f)          # omitted -> None -> stored as None

Every field the caller omits is written as None. Two consequences measured live:

1. A PUT sending only `{"group_search_filter": ...}` returned **200** and wiped
   `enabled`, `url`, `bind_dn` and `base_dn`. The next request answered
   `400 "LDAP is not configured"` — directory sign-in gone for the whole org,
   from a request that reported success.
2. A second PUT that named 13 fields but omitted `auto_provision_users` silently
   set it False. Directory sign-in then returned 400 for every NEW person, with
   `ldap_not_provisioned` in the log, while existing accounts kept working — so
   it looks like an intermittent directory fault. It cost me a wrong diagnosis:
   three journey tests went red and I briefly read them as an LDAP regression.
   `use_ssl` flipped to True against an `ldap://` URL in the same request.

★The author already knew about this shape and solved it for exactly one field:
    ldap["bind_password_enc"] = existing.get("bind_password_enc")   # keep
The password is preserved on omission. Nothing else is.

★This is the `ReportScheduleRequest` landmine again, already recorded in
CLAUDE.md: pydantic cannot tell "field omitted" from "field explicitly null"
unless someone reads `model_fields_set`. The fix has a known shape here.

The UI never hits it — the settings form posts every field. It is an API-caller
and automation defect, and `enabled` is the dangerous one: omit it and directory
sign-in is switched off with a 200.

FIXED in 0.0.543.16 on Rahul's instruction: merge on `model_fields_set`. Named
(including explicit null) is written so a field can still be cleared; omitted
with a stored value is preserved; omitted on a first write takes the schema
default. Verified live by replaying the exact PUT that caused it. SSO's
`update_config` was checked and left alone — already a real merge.

## DEF-E — the "a mention is not a request" theme fix has never fired
Found 2026-08-20 while seam-checking the deck work. Reproduced in the SHIPPED
image 0.0.543.16, through the real entry point `_select_deck_theme`.

`_named_theme_in` (create_artifact.py:276) is the documented fix for the landmine
where "Atelier" resolved to `christmas` from conversation noise. Its docstring:
"Requiring naming grammar ('in the X style') is what separates a request from a
mention. Scans from the END so the latest instruction wins."

It never fires. `_STYLE_PHRASE` ends with:

    \s*(?:style|theme|look)?\s*[.,;!]

The trailing punctuation class is NOT optional. Measured:

    "make it in the boardroom style"            -> no match
    "make it in the boardroom style."           -> HIT boardroom
    "make it in the boardroom style, please"    -> HIT boardroom
    "build it in the telemetry theme for the board"   -> no match
    "build it in the telemetry theme, for the board"  -> HIT telemetry

A chat message rarely ends in a full stop, so in practice tier 2 is dead and
EVERY deck falls through to `_resolve_deck_theme` -> `themes._match`, whose own
docstring is "Longest alias mentioned ANYWHERE in text" — a plain substring scan.
That is precisely the behaviour the grammar tier exists to prevent.

Consequences measured on the shipped image:

    "our christmas revenue fell 12% year on year"  -> christmas   method=resolved
    "review of the telemetry team headcount"       -> telemetry   method=resolved
    "make it in the boardroom style"               -> boardroom   method=resolved

Note the third: even a textbook grammar request is attributed `resolved`, not
`named_by_user`. So the telemetry ALSO mislabels how the theme was chosen — a
deliberate request and an accidental substring hit are recorded identically.

★A deck about Christmas revenue is built in the Christmas design system. That is
the original landmine, still live, with a fix in the tree that cannot run.

★NOT caused by this session's agents — reproduced in the backup copy and in the
shipped image, both identical.

NOT FIXED — changing theme selection affects every deck, so it is Rahul's call.

## DEF-F — the model paints furniture the theme already painted
Found 2026-08-20, deck 2 of T20, on 0.0.543.17. Deck 1 clean — nondeterministic.

Slides 2–4 of the inventory deck carry DOUBLE furniture:
  * two progress trackers stacked (theme's square-tracker row PLUS a
    model-drawn second row directly beneath it)
  * two source footers overlapping at different sizes — the theme's
    "Source: Supply Chain Analytics · City Mart Retail Network" with a
    model-drawn "Source: team analysis" printed over its left end
  * a stray small page number ("2") beside the theme's own "02 / 04"

Cause shape: `motifs.py` paints tracker/footer/page-number as theme furniture,
and nothing tells the generated code those are already present, so the model
sometimes draws its own. Paint-furniture-then-enforce order exists; a
"do not draw what the theme draws" rule does not.

Cosmetic, but on the first content slide of an executive deck, and exactly the
class of thing an admin screenshots. NOT FIXED — likely a one-paragraph prompt
rule ("the theme already paints tracker, footer and page number — never draw
your own") plus, better, an enforce_theme_rules check that strips a second
tracker/footer. Needs Rahul's go.

## STATUS UPDATE 2026-08-20 (0.0.543.18)
DEF-A FIXED (absent ≠ 0; duckdb reports real count) · DEF-B FIXED ([platform]
line in the model's log) · DEF-E FIXED (strong/weak grammar + conversation cut
from the alias scan) · DEF-F FIXED (conditional prompt + footer-band strip) ·
theme_id omission FIXED (id-or-'auto' required; proven live, one retry each).

## OBS-8 — a cover slide can render without its headline
Deck B of T21 ("City Rewards Loyalty Programme Performance"): the cover carries
the kicker and the subtitle but no visible title — a blank band where the
headline belongs. Possibly white-on-white text or a missing textbox in the
generated code; only seen once. Cosmetic, NOT from this batch (cover code is
model-authored). Worth a look if it recurs.
