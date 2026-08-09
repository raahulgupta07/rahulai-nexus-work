# Security suite — what it checks, and what it cannot

Run against a **running instance you own**. Everything here is read-only or
cleans up after itself. Nothing in this directory talks to a third party.

```bash
docker cp scripts/mint-user-tokens.py dash-app:/app/backend/
docker exec -w /app/backend dash-app python mint-user-tokens.py /tmp/sec-tokens.json
docker cp dash-app:/tmp/sec-tokens.json /tmp/sec-tokens.json
python3 scripts/security/run-all.py /tmp/sec-tokens.json
```

★The token path is an **argument**, not `> /tmp/sec-tokens.json`. The redirect
form captures the app's start-up log lines into the file and every consumer then
dies on `Unexpected token 'L'` — which reads as the suite being broken.

## The four dimensions

| script | asks |
|---|---|
| `tenancy.py` | can one user reach another user's object, in this org or another |
| `secrets.py` | does any secret leave the server — body, error, log, or prompt |
| `injection.py` | can user input reach SQL, LDAP, a filesystem path, or an outbound request |
| `test_every_route_is_gated.py` (in `backend/tests/unit/fork/`) | is every route actually gated — statically, over the AST |

Each probe prints what it **proved**, not merely that it passed.

## ★★★What this suite cannot see

State this whenever you report a green run. A suite that is described as
"security testing passed" invites a conclusion it does not support.

- **It is not a penetration test.** No fuzzing, no timing attacks, no chained
  exploits, no attempt at privilege escalation through the container or the host.
- **It cannot prove absence.** Every check is a question someone thought to ask.
  The eight-route conversation hole existed for releases while every test passed.
- **It tests the app, not the deployment.** TLS, the reverse proxy, security
  headers added at the edge, network segmentation, IAM, the database's own
  exposure and backup encryption are all outside it — and on AWS those are where
  most real incidents come from.
- **It tests one architecture.** These images are `linux/arm64`. Nothing here
  transfers to an `x86_64` build without being re-run against that image.
- **The model is not in scope.** Prompt injection through an uploaded document or
  a connected table's contents is a real risk for an agent product and is only
  partly addressed here — the generated-code sandbox boundary is checked, the
  model's judgement is not.
- **Dependencies are not audited.** No SCA / CVE scan of the Python or npm tree
  runs here.

## Rules for anything added to this directory

1. **Every probe asserts the OWNER still succeeds**, not only that a stranger is
   refused. A gate set too tight is a bug: `0.0.528.9` gated the reasoning panel
   at administrator and locked members out of their own conversations. A
   refusal-only test passes on a completely broken gate.
2. **Prove a check can fail** before trusting it. Run it against the pre-fix code
   (`git show HEAD:<file>`) and show N detections on the old, 0 on the new.
3. **Assert on the response body**, never on what the UI renders. Withholding a
   value in the frontend while the API still sends it is theatre — devtools reads
   the wire.
4. **Deletion is SOFT.** A row surviving in Postgres with `deleted_at` set is
   correct. Assert absence from the API's LIST; a deleted object still readable
   is the finding.
5. **Never call `GET /data_sources/{id}/test_connection`.** It is spelled GET and
   **writes** `is_active`; one read-only sweep disabled a live agent org-wide.
6. **Never print a real secret.** Describe it ("a 44-char Fernet key"). A finding
   report that quotes the value creates a second copy of the problem.
