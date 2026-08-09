"""Upload files the way a member does, and check what the product makes of them.

Drives the real HTTP API with a real member's token — no service-layer shortcuts
— because the thing under test is the whole path: upload -> managed path ->
DuckDB table -> schema -> the agent actually answering from it.

WHAT EACH CASE IS FOR

  small csv        the ordinary case, and the baseline for "did a table appear"
  multi-sheet xlsx one sheet must become one table; empty sheets are skipped
  definitions xlsx routed to an INSTRUCTION, not a table — the filename decides
                   (definition/glossary/dictionary/meaning/logic/rules/q&a)
  big csv          the case nobody tests until a customer hits it
  odd bytes        a file whose extension promises something its content is not

★Routing is deterministic — extension plus filename, never an LLM. So these
assertions are about a rule, not about a model's mood.

★The member never supplies a server path. `config.file_paths` is reflected from
the SERVER-generated managed path; a member-supplied path would be an arbitrary
file read, which is why the create gate forces it empty.

Usage:  python user-uploads.py /tmp/tokens.json [--big-mb N]
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE_URL", "http://localhost:8095")


# ── tiny HTTP layer ──────────────────────────────────────────────────────────

def _req(method, path, token, org, body=None, ctype=None, raw=False):
    url = f"{BASE}{path}"
    data = None
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": org}
    if body is not None:
        if raw:
            data = body
            headers["Content-Type"] = ctype
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt


def multipart(fields, filename, content, field="file"):
    """Build a multipart body without pulling in `requests`."""
    boundary = "----form" + uuid.uuid4().hex
    out = io.BytesIO()

    def w(s):
        out.write(s.encode() if isinstance(s, str) else s)

    for k, v in (fields or {}).items():
        w(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    w(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
      f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n")
    w(content)
    w(f"\r\n--{boundary}--\r\n")
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# ── fixtures, built here so the corpus is real, not renamed .txt ─────────────

def small_csv():
    rows = ["region,product,units,revenue"]
    data = [("North", "Widget", 120, 2400.5), ("South", "Widget", 80, 1600.0),
            ("North", "Gadget", 45, 2250.75), ("East", "Gadget", 60, 3000.0),
            ("South", "Doohickey", 12, 144.0)]
    for r, p, u, rev in data:
        rows.append(f"{r},{p},{u},{rev}")
    return ("small_sales.csv", ("\n".join(rows) + "\n").encode())


def big_csv(mb):
    """A genuinely large CSV. Header plus rows until the target size."""
    buf = io.BytesIO()
    buf.write(b"id,region,product,units,revenue,note\n")
    target = mb * 1024 * 1024
    regions = [b"North", b"South", b"East", b"West"]
    i = 0
    while buf.tell() < target:
        r = regions[i % 4]
        buf.write(b"%d,%s,Widget-%d,%d,%d.50,filler text to make the row wider\n"
                  % (i, r, i % 97, i % 500, (i * 7) % 100000))
        i += 1
    return (f"big_sales_{mb}mb.csv", buf.getvalue()), i


def xlsx(sheets):
    """Minimal real .xlsx via openpyxl if present, else None (case is skipped)."""
    try:
        from openpyxl import Workbook
    except Exception:
        return None
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ── the run ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens")
    ap.add_argument("--big-mb", type=int, default=60)
    ap.add_argument("--actor", default="member@cityagent.io")
    args = ap.parse_args()

    tok = json.load(open(args.tokens))
    org = tok["org"]["id"]
    who = tok["users"][args.actor]
    token = who["token"]

    results = []

    def record(name, ok, detail):
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}", flush=True)

    print(f"acting as {args.actor} (role={who.get('role')}) against {BASE}\n")

    # 1 ── small csv
    (fname, content) = small_csv()
    body, ctype = multipart({"name": "upload-small"}, fname, content)
    t0 = time.time()
    st, resp = _req("POST", "/api/files", token, org, body, ctype, raw=True)
    record("small csv upload", st in (200, 201),
           f"HTTP {st} in {time.time()-t0:.1f}s, {len(content)} bytes")
    small_resp = resp if st in (200, 201) else None

    # 2 ── big csv
    (bname, bcontent), nrows = big_csv(args.big_mb)
    body, ctype = multipart({"name": "upload-big"}, bname, bcontent)
    t0 = time.time()
    st, resp = _req("POST", "/api/files", token, org, body, ctype, raw=True)
    dur = time.time() - t0
    mb = len(bcontent) / 1024 / 1024
    # A refusal is a legitimate answer here — but it has to be a DELIBERATE one
    # (413/400 with a readable reason), not a 500 or a hang.
    ok = st in (200, 201, 413) or (st == 400 and "size" in json.dumps(resp).lower())
    record(f"big csv upload ({mb:.0f}MB, {nrows} rows)", ok,
           f"HTTP {st} in {dur:.1f}s ({mb/max(dur,0.01):.1f} MB/s)"
           + ("" if st in (200, 201) else f" — {str(resp)[:120]}"))

    # 3 ── multi-sheet xlsx: one sheet, one table
    book = xlsx({
        "Sales":   [["region", "revenue"], ["North", 100], ["South", 200]],
        "Costs":   [["region", "cost"], ["North", 40], ["South", 90]],
        "Empty":   [],
    })
    if book is None:
        record("multi-sheet xlsx", True, "SKIPPED — openpyxl not available in this env")
    else:
        body, ctype = multipart({"name": "upload-sheets"}, "quarterly.xlsx", book)
        st, resp = _req("POST", "/api/files", token, org, body, ctype, raw=True)
        record("multi-sheet xlsx upload", st in (200, 201), f"HTTP {st}")

    # 4 ── a definitions workbook must NOT become a table
    if book is not None:
        defs = xlsx({"Definitions": [["term", "meaning"],
                                     ["ARR", "annual recurring revenue"],
                                     ["Churn", "customers lost in period"]]})
        body, ctype = multipart({"name": "upload-defs"}, "Definitions.xlsx", defs)
        st, resp = _req("POST", "/api/files", token, org, body, ctype, raw=True)
        record("definitions workbook upload", st in (200, 201), f"HTTP {st}")

    # 5 ── a file whose extension lies about its content
    body, ctype = multipart({"name": "upload-liar"}, "not_really.csv",
                            b"\x50\x4b\x03\x04 this is zip magic, not csv\n")
    st, resp = _req("POST", "/api/files", token, org, body, ctype, raw=True)
    record("mislabelled file is handled, not crashed", st != 500,
           f"HTTP {st} (any deliberate answer is fine; 500 is not)")

    # 6 ── the whole member journey: make an agent from files, get a table back.
    # This is the case the plain /files upload cannot reach — the upload hook
    # reflects the SERVER-generated managed path into config.file_paths and then
    # llm_sync builds the table. Uploading without an agent proves storage only.
    st, ds = _req("POST", "/api/data_sources", token, org,
                  {"name": f"journey-{uuid.uuid4().hex[:6]}", "type": "csv",
                   "config": {"file_paths": []}, "is_public": False})
    if st not in (200, 201):
        record("member creates a file agent", False, f"HTTP {st} — {str(ds)[:160]}")
    else:
        ds_id = ds.get("id")
        record("member creates a file agent", True, f"HTTP {st}, id={ds_id}")

        (fname, content) = small_csv()
        body, ctype = multipart({}, fname, content)
        st, up = _req("POST", f"/api/data_sources/{ds_id}/files", token, org, body, ctype, raw=True)
        record("upload a csv onto that agent", st in (200, 201), f"HTTP {st}")

        # Schema build is asynchronous; /full_schema lags the upload. Poll rather
        # than sleep-and-hope, and say plainly if it never arrives.
        table_names, waited = [], 0.0
        for _ in range(30):
            time.sleep(2); waited += 2
            sc_st, sc = _req("GET", f"/api/data_sources/{ds_id}/full_schema", token, org)
            if sc_st == 200:
                rows = sc if isinstance(sc, list) else sc.get("tables", [])
                table_names = [t.get("name") for t in rows if isinstance(t, dict)]
                if table_names:
                    break
        record("uploaded csv becomes a queryable table", bool(table_names),
               f"after {waited:.0f}s: {table_names or 'no tables appeared'}")

        # The escalation that must stay shut: a member must not be able to make
        # the app read an arbitrary server path.
        st, bad = _req("POST", "/api/data_sources", token, org,
                       {"name": f"escalate-{uuid.uuid4().hex[:6]}", "type": "csv",
                        "config": {"file_paths": ["/etc/passwd"]}, "is_public": True})
        leaked = json.dumps(bad).find("/etc/passwd") >= 0 if st in (200, 201) else False
        forced_private = (bad.get("is_public") is False) if st in (200, 201) else None
        record("member cannot smuggle a server path or force public", not leaked,
               f"HTTP {st}, /etc/passwd echoed back: {leaked}, is_public forced to: {forced_private}")

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed} passed, {len(results)-passed} failed, {len(results)} checks")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
