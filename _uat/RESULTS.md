# UAT — cityagentinsights:0.0.543.12 — 2026-08-20

## T1 — Backend fork guard suite
PASS — 3873 passed, 1 skipped, 81s. Host venv, sqlite.
Includes DEF-011/012/015-021 guard tests + 11.1 powerbi fk normalize.

## T2 — Baseline / version
PASS — image VERSION 0.0.543.12, /health 200, / 200.
connections 0, data_sources 0, reports 0, organizations 1, members 1 (admin).

## T3 — DEF-018 completion field documented
PASS at schema level in the running image: default None, description present,
`completion_blocks` named, carried into model_json_schema.
NOTE: not verifiable over HTTP here — swagger is disabled in this config so
`/openapi.json` 404s. See OBS-3.

## T4 — DEF-011/012 spreadsheet read whole (functional, in the running image)
PASS. 14,000-row xlsx + blank row + TOTAL trailer.
- pandas read 14,002 rows — nothing truncated at 1,000 or any other boundary
- detect_total_row found it: removed_rows 1, label TOTAL, summing_columns [revenue]
- notice written, plain language, explains double-counting
- sum after strip = 34,821,156.91 = exact ground truth
- naive raw sum = 69,642,313.82 (exactly 2x) — the defect this guards

## T5 — DEF-017 web search: refusal vs real search (functional)
PASS, both sides.
- enable_web_fetch=false -> success False, blocked_by_policy True, sentence names
  the setting an admin can flip
- enable_web_fetch=true  -> success True, blocked_by_policy False, 8 live sources
  from DuckDuckGo (real egress works from this container)
Frontend half already proved in the baked bundle earlier: "Web search is turned
off" string in /app/frontend/dist/_nuxt/CsdgU70i.js.
METHOD NOTE: my first stub lacked `.get_config()`, so setting_enabled swallowed
the AttributeError and returned default False — the OFF case passed for the
wrong reason and the ON case falsely failed. Redone with a real stub.

## T6 — DEF-021 "Last checked" + 12.1 "Tables 0"
DEF-021 half: PASS.
- fresh connection -> last_checked_at null (correct: never checked, and the UI
  now says so instead of inventing a date)
- after POST /{id}/test -> last_checked_at 2026-08-20T11:23:31, on both LIST and
  DETAIL
12.1 half: FAIL — see DEF-A below.
NOTE: the detail field is `table_count`, not `tables_count`.

## T7 — DEF-019 duplicate-membership guard (functional, real DB)
PASS, all three cases, against the live postgres through the running image:
1. live membership          -> True
2. soft-deleted membership  -> False  (person LDAP dropped is re-addable)
3. live + soft-deleted both -> True   (no MultipleResultsFound; this was the 500)
Test rows cleaned up afterwards; DB back to 1 user / 1 membership.

## T8 — 11.1 Power BI dataset with relationships can be saved
PASS.
- raw pydantic FK -> `TypeError: Object of type ForeignKeySchema is not JSON
  serializable` (the INSERT failure that blocked the save; reproduced)
- normalize_fks(fk) -> plain nested dict, json.dumps clean
- powerbi_multitenant_scan.py:268 does call it: `"fks": normalize_fks(fks)`
- dict and unknown shapes pass through unchanged, as documented (a relationship
  silently dropped is worse than one that fails loudly)
NOT covered: 11.4, re-running real Power BI indexing — needs your Microsoft
sign-in. Still blocked, same as before.

## T9 — 9.2 live agent run, full-table maths (no truncation)
PASS — exact match, first try, real LLM (google/gemini-3.7-flash via OpenRouter).
Ground truth straight from duckdb:  373,932 rows / net_amount 5,136,609,583
Agent answered:                     373,932 rows / net_amount 5,136,609,583
Tool chain: search_agents -> set_report_agents -> describe_tables x2 ->
inspect_data -> create_data. No sampling, no LIMIT, no rounding drift.

Also PASS (unplanned, positive control): the FIRST report had no agent attached
and the agent said so plainly — "no data sources or database agents connected to
this report, so I cannot execute a query" — instead of inventing a number.
`scope: {kind: agents, label: "Reading: connected data", file_count: 0}` was set
correctly. That is the honest-refusal behaviour, on the read path.

Setup learned: a connection is not queryable on its own. It has to be wrapped in
a data_source ("agent") and that is what a report attaches.

## T10 — DEF-018 confirmed live, both paths
PASS (behaviour is as designed, and the trap is real).
- v2 `/api/reports/{id}/completions`        -> `completion: null`, answer (164
  chars) lives in `completion_blocks`
- v1 `/api/reports/{id}/completions.legacy` -> `completion` is a dict holding the
  same answer text
So an integration written against v1 that moved to the documented v2 path really
does read a familiar key and get null. The schema description is the mitigation;
see OBS-3 for why it currently reaches nobody on this deployment.

## T11 — DEF-011/012 end to end through the product (upload -> agent)
PASS on the numbers.
Uploaded the 14,000-row xlsx (blank row + TOTAL trailer) to a report, asked the
agent for row count and exact revenue sum.
- answered 14,000 data rows and $34,821,156.91 — both exactly right
- the TOTAL row was ALREADY gone before the model's own `pd.read_excel` ran:
  its execution log shows "Total row count (raw): 14001" and
  "Sum of revenue (raw): 34821156.91". Un-stripped that sum would have been
  69,642,313.82. So the platform stripped it, silently and correctly.
- nothing truncated at 1,000 rows; tail shows order_id 14,000
FINDING: see DEF-B — it got the number right and then told the user something
false about how.

## T12 — DEF-015 abandoned-sweep, DEF-016 completion status default
PASS, both.
DEF-016: `Completion.status` ORM default is `in_progress`, NOT NULL. A row that
has done nothing is no longer indistinguishable from a finished turn.
DEF-015 (functional, real DB): seeded a ConnectionIndexing with
`started_at = NULL`, `created_at` 6h old, status `running` — the exact row that
could never be swept before. `sweep_abandoned()` picked it up and moved it to
`failed`. The system row with `user_id NULL` was correctly left alone.
Implementation is COALESCE(started_at, created_at), so a row is judged on exactly
one timestamp and cannot qualify under one clause and be excluded by the other.

## T13 — DEF-020 one membership per person, enforced at the database
PASS.
- `uq_membership_user_org` UNIQUE (user_id, organization_id)
  WHERE deleted_at IS NULL AND user_id IS NOT NULL — live in postgres
- attempted a real duplicate INSERT in a transaction:
  `ERROR: duplicate key value violates unique constraint "uq_membership_user_org"`
  rolled back, nothing changed
- partial index is right: a soft-deleted row does NOT block re-adding the person
  (this is the same fact T7 case 3 exercised from the service side)
- code half in auth.py resolves membership BEFORE the seat cap, so an existing
  member is never told "ask your admin" once the org hits its licensed count

## T14 — log sweep, whole session
1 ERROR, 0 tracebacks, in 60 minutes covering every test above.
The one ERROR is the hourly LDAP group sync, unrelated to anything tested — see
OBS-5. Nothing in T1-T13 produced a traceback or a 5xx.

## BLOCKED — need you, not me
- T-PBI-1 (#9) Power BI user sign-in and the sync it triggers. Needs your
  Microsoft credentials. I do not type credentials into sign-in forms; that has
  not changed. Everything downstream of the sign-in (10.2's tracker start,
  11.4's re-index, 12.4's two-agent credential loss) is blocked behind it.
- DEF-013 the sign-in route itself: source-verified only
  (`prog.start(..., trigger=TRIGGER_SIGNIN)` and `_run_tenant_merge` are both
  called), never exercised against real Microsoft.

## SCOPE NOT COVERED
- 9.3 three artifacts / three totals — deferred by you, design change
- 12.4 connection with two agents loses the user's sign-in — deferred, and
  blocked behind the Power BI sign-in anyway
- frontend interaction (clicking the refusal row open, reading "Last checked" on
  screen). The strings are proved present in the baked bundle; nobody has
  clicked them. That needs a browser session.

---
Run finished 2026-08-20. Nothing fixed. Nothing committed. dev untouched.

## T15 — LDAP + SSO (Keycloak), the login matrix
PASS — 28 of 28, on the baked 0.0.543.13.
Directory door: sign in by username AND by email; wrong password and unknown
user are byte-identical (no employee-enumeration oracle); a directory account is
refused at the LOCAL door.
Local door: still works while LDAP is enabled — this is the `.521.5` lockout,
still fixed. Wrong local password refused.
SSO: new verified identity provisions; a second sign-in reuses the same account
(no duplicate); all four refusal branches produce their own sentence, each
naming the thing to fix (the local account / the provider's verified flag / the
email attribute).
Closed vs open install both exercised: on a closed install a directory or
uninvited-signup-off account links without provider proof; flip the policy open
and the refusals return. That is the `.543.8` narrowing, proved both ways.
Merge: directory-first and SSO-first both end at ONE row with both identities.
Screens: the sign-in column reads ['ldap','sso'] / ['sso'] / ['ldap'] / ['local']
correctly, and `has_password` is true only for the local account.

METHOD NOTE: first run was 25/27 with two failures, both naming
`localmatrix@cityagent.io`. NOT a regression — the rig ASSUMES that fixture
exists and never creates it, and my wipe had deleted it. Planted it (dev password
published in the rig's own README) and re-ran clean. See OBS-6.

## T15b — new-user-journey
PASS — 18 of 18. Two brand-new people, opposite orders:
- freshone: directory first, then SSO joins the same account
- freshtwo: SSO first, then directory joins the same account
In both, BOTH doors still work after the merge, the id never changes, and the
roster shows both ways in. Provider sent `verified=False` throughout and the
link was still allowed, correctly, because uninvited sign-up is off.

## T16 — LDAP group sync
Directory CONNECTION is healthy: `test-connection` -> connected true,
user_count 5. Authentication and user sync work (T15 proves it).
Group sync fails, and found a real defect doing it — see DEF-C.
Measured directly against the server with the app's own decrypted bind:
  (objectClass=group)        -> RAISES LDAPObjectClassError (AD syntax)
  (objectClass=groupOfNames) -> ok, 0 entries
  (objectClass=posixGroup)   -> ok, 0 entries
And the fixture has no groups at all: `scripts/dev-identity/ldif/` is one file,
`01-people.ldif`, with zero group entries. So group sync has never had anything
to find on this rig.

## T17 — DEF-C fixed and verified on 0.0.543.15
The preview now returns 200 with the reason, and — separately — actually runs.
- refused filter -> 200, groups_read false, the full sentence naming the filter,
  the base, and the two spellings that work; groups_to_remove NOT counted
- working filter -> 200, groups_read true, group_error null (POSITIVE CONTROL:
  proves the preview completes, which it never had, rather than only failing
  more politely)
- test-connection -> group_count 0 with no error on a good filter; the same
  sentence in `group_error` on a bad one
Both identity rigs re-run on the fixed build: login matrix 28/28, new-user
journey 18/18. Built-in agents still 1 each.

★.14 was built, deployed, and STILL 500'd. Caught only by hitting the endpoint,
never by the build status or the 22 source-scanning guards. Renumbered to .15
and .14's changelog entry removed rather than ship a note claiming a fix the
build did not deliver.

★The local LDAP group filter is now `(objectClass=groupOfNames)` with base
`dc=cityagent,dc=io` — correct for this OpenLDAP fixture. The hourly ERROR
(OBS-5) stops. Restoring the known-broken `(objectClass=group)` would only
resume it. The fixture still has zero groups, so counts are honestly 0.

## T18 — DEF-D fixed and verified live on 0.0.543.16
`update_ldap` merges on `model_fields_set`.
- the exact PUT that wiped the server before — `{"group_search_filter": …}` —
  now changes ONLY that field; enabled, url, bind_dn, base_dn, use_ssl,
  auto_provision_users and the encrypted bind password all survive
- POSITIVE CONTROL: `{"group_search_base": null}` still CLEARS it, and url is
  untouched. Absence preserves; explicit null clears.
- login matrix 28/28, new-user journey 18/18, preview 200 groups_read true,
  test-connection user_count 5 / group_count 0 with no errors
- built-in agents still 1 each; 0 ERROR lines since deploy
SSO's `update_config` was checked and left alone — already a real merge.

## T19 — Track 1 (bolt-slides discipline) + half of DEF-E, baked as 0.0.543.17
Verified in the BAKED image, not a hot copy:
- the real 39,524-char slides prompt carries all four rules, including
  `PP_ALIGN.CENTER` (the centring rule translated to python-pptx, not pasted CSS)
- grammar tier now FIRES: requests hit and are attributed `named_by_user`;
  mentions ("our meeting in the boardroom ran long") correctly miss
- `theme_id` schema no longer invites omission; the two exceptions (saved report
  theme, org brand) survive so nothing overrides a deliberate choice
- `resolve_with_reason` + 3 reason constants present; `resolve()` behaviourally
  identical (6/6 cases vs the backup)
- 0 ERROR lines since deploy; 3 built-in agents intact
Fork suite 3991 passed, 1 xfailed. The 4 failures are the known load-flaky
`test_retry_does_not_rescan.py` (OBS-7), reproduced with the change reverted.

★OPEN, deliberately: a MENTION still selects a theme
("our christmas revenue" -> christmas). Removing that tier broke a documented
precedence contract and 9 assertions in
`test_the_theme_registry_holds_every_theme.py`, including
`resolve(user_text='a midnight pitch please') == 'midnight-pitch'` — which has no
naming grammar at all. Marked `xfail(strict=True)` so it stays visible.
DECISION OWED: should a phrase with no naming grammar pick a theme?

★NOT YET DONE: no real deck has been generated on .17. The rules are proved
present in the prompt; nobody has looked at a deck they produced. Per the
standing landmine, that means rendering to PDF and reading the pages.

## T20 — Plan B: two real decks on 0.0.543.17, rendered to PDF and READ
Deck 1 — "Q4 sales", NO style words → 7 slides, boardroom, method=resolved.
Deck 2 — "inventory health … Make it in the boardroom style" (no trailing
punctuation) → 4 slides, boardroom, **method=named_by_user**.

PROVEN, end to end in production:
- DEF-E's shipped half works: the grammar request with no full stop was caught
  and attributed as a request. Before .17 that sentence matched nothing.
- Rule 10 obeyed twice: both decks open with `# design system: boardroom — …`.
- Layout discipline held: no team, pricing or divider slides in either deck;
  one KPI row per deck; the 7-slide deck did not waste a slide on signposting.
- NUMBERS EXACT vs duckdb, not just internally consistent:
  deck 1: month sum 440.29M ✓, region sum 440.30M ✓
  deck 2: on_hand 1,310,383 EXACT · on_order 388,077 EXACT ·
          3,590 breaches = 29.9% under `on_hand <= reorder_level` ✓
- theme furniture painted (tracker squares, footer rule, flat grounds).

FOUND: DEF-F (deck 2 only — deck 1 clean, so nondeterministic), see ERRORS.md.

HONEST MISS: the model sent theme_id=None on BOTH calls. The schema rewrite
("NAME ONE ON EVERY DECK") did not move the model — 0/2, same as the measured
defect. The system recovered through the grammar tier and the default, and the
attribution now tells the truth about which happened; but the metric the schema
change targeted did not move on this sample.

## T21 — 0.0.543.18 baked, deployed, and proved with real decks
All five fixes verified INSIDE the baked image, then live:

DEF-A  dead. The exact request that said "0 tables / no schema access" now
       answers `table_count: 11, schema_access: true`.
DEF-F  dead on this sample. Deck A (4 slides): ONE tracker, ONE footer, ONE
       page number per slide — the .17 doubles are gone. Deck B same.
theme_id  both new paths proven live, unprompted:
       - deck A: attempt 1 omitted -> validator's corrective message -> attempt 2
         the model itself sent `theme_id: "boardroom"` (matching the user's
         request). The 0/9 metric is broken.
       - deck B: model sent the literal `"auto"` — explicit deferral.
       Cost as predicted: one retry each.
DEF-E  regex half live: "Make it in the boardroom style" (no punctuation)
       honoured; strong/weak split verified earlier (6 requests hit, 6 mentions
       miss, including "we sat in the boardroom.").
Numbers exact vs duckdb AGAIN, both decks:
       deck A: 1,310,383 / 388,077 / 3,515 = 29.3% (strict <, consistent)
       deck B: earned 21,586,870 -> 21.59M · redeemed 2,764,741 -> 2.76M ·
       rate 12.81% · txn counts 54,703 / 6,564 all EXACT.
Suite 4053 passed / 1 skipped. Rollback pre-0.0.543.18 (db427faafa50). No
migrations .12 -> .18.

NEW (cosmetic): deck B's cover renders kicker + subtitle but NO headline — a
blank band where the title belongs. Not one of the five fixes; logged OBS-8.
