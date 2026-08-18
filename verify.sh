#!/usr/bin/env bash
#
# verify.sh — run this before every push. Under two minutes.
#
#   ./verify.sh              full gate
#   ./verify.sh --quick      skip the test suite (compile + imports + invariants only, ~30s)
#
# Each check below exists because its absence has already cost a day, or
# shipped something broken. None of them is theoretical.
#
#   1  compile      the HOST python is 3.9 and reports FALSE f-string errors on
#                   code that is fine. Everything compiles in the container.
#   2  imports      syntax-clean is not the same as importable. A port once left
#                   an import of a module upstream had deleted; every file
#                   compiled and the app died at start-up.
#   3  suite        our own tests. Fast on purpose — see backend/tests/unit/fork.
#   4  alembic      more than one head means migrations stop dead on deploy.
#   5  versions     VERSION, CHANGELOG and README must agree, or the by-hand
#                   upgrade's "does the image match the tree" check is a lie.
#   6  markers      an unresolved merge conflict that happens to parse.
#
# ★This gate deliberately does NOT run upstream's tests/unit. Those pay a
# 0.9s-per-test migration fixture — 2500 tests is ~38 minutes, which means
# nobody runs it, which means it protects nothing. Run those nightly.
#
set -uo pipefail

BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
[[ -t 1 ]] || { BOLD=""; RED=""; GRN=""; YEL=""; DIM=""; OFF=""; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$REPO"
QUICK=0; [[ "${1:-}" == "--quick" ]] && QUICK=1
FAILED=0; STEP=0

step()  { STEP=$((STEP+1)); printf "\n${BOLD}%d. %s${OFF}\n" "$STEP" "$1"; }
ok()    { printf "   ${GRN}ok${OFF}    %s\n" "$1"; }
bad()   { printf "   ${RED}FAIL${OFF}  %s\n" "$1"; FAILED=$((FAILED+1)); }
note()  { printf "   ${DIM}%s${OFF}\n" "$1"; }

printf "\n${BOLD}verify${OFF}  ${DIM}%s${OFF}\n" "$(date '+%H:%M:%S')"

# The image is only a Python/pytest runtime here — the SOURCE comes from the
# mount, so a stale image cannot mask a broken tree.
IMG="$(docker inspect -f '{{.Config.Image}}' dash-app 2>/dev/null || true)"
if [[ -z "$IMG" ]]; then
  IMG="$(docker images cityagentinsights --format '{{.Repository}}:{{.Tag}}' | head -1)"
fi
[[ -n "$IMG" ]] || { printf "\n${RED}no cityagentinsights image and no running dash-app — cannot verify${OFF}\n\n"; exit 2; }
note "runtime image: $IMG"

# ★PYTHONPYCACHEPREFIX is not optional. The source is mounted read-only, and
# without it py_compile fails writing __pycache__ with "[Errno 30] Read-only
# file system" — which reads exactly like a syntax error and is not one.
# ★-i is not optional either. Without it docker does not forward stdin, so a
# `python - <<PY` heredoc reaches an empty stdin: python prints nothing, exits
# 0, and the check reports a pass having run no code at all. Caught on this
# script's first run — the only tell was a blank file count.
DRUN=(docker run --rm -i -v "$REPO:/src:ro" -e PYTHONPYCACHEPREFIX=/tmp/pyc)

# ---------------------------------------------------------------- 1. compile
step "compile every backend file (python 3.12, in the container)"
OUT=$("${DRUN[@]}" -w /src "$IMG" python - <<'PY' 2>&1
import pathlib, py_compile
bad, n = [], 0
for p in pathlib.Path("backend").rglob("*.py"):
    s = str(p)
    if "__pycache__" in s or ".venv" in s or ".bak-" in s: continue
    n += 1
    try: py_compile.compile(s, doraise=True, cfile="/tmp/x.pyc")
    except Exception as e: bad.append(f"{p}: {e}")
print(f"COUNT {n}")
for b in bad[:10]: print("BAD", b)
PY
)
CNT=$(sed -n 's/^COUNT //p' <<<"$OUT")
if grep -q '^BAD' <<<"$OUT"; then bad "$(grep -c '^BAD' <<<"$OUT") file(s) do not compile"; grep '^BAD' <<<"$OUT" | sed 's/^BAD /   /'
else ok "${CNT:-?} files"; fi

# ---------------------------------------------------------------- 2. imports
step "the app actually imports"
if "${DRUN[@]}" --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
     -w /src/backend -e PYTHONPATH=/src/backend "$IMG" python -c 'import main' >/dev/null 2>&1; then
  ok "import main"
else
  bad "import main — run it by hand to see the traceback:"
  note "docker run --rm -v \"\$PWD:/src:ro\" --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 -w /src/backend -e PYTHONPATH=/src/backend $IMG python -c 'import main'"
fi

# ---------------------------------------------------------------- 3. tests
if [[ $QUICK -eq 0 ]]; then
  step "our test suite (backend/tests/unit/fork)"
  T=$("${DRUN[@]}" --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
        -w /src/backend "$IMG" sh -c \
        'pip install -q pytest pytest-asyncio httpx 2>/dev/null; python -m pytest tests/unit/fork -q -p no:cacheprovider --no-header 2>&1 | tail -40')
  LINE=$(grep -E '[0-9]+ (passed|failed)' <<<"$T" | tail -1)
  if grep -qE '[0-9]+ failed|error' <<<"$LINE"; then
    bad "$LINE"; grep '^FAILED' <<<"$T" | sed 's/^/   /' | head -15
  else
    ok "${LINE:-no result line}"
  fi
else
  step "our test suite"; note "skipped (--quick)"
fi

# ---------------------------------------------------------------- 4. alembic
step "migrations have exactly one head"
H=$(python3 - <<'PY'
import re, pathlib
revs, downs = {}, set()
d = pathlib.Path("backend/alembic/versions")
for p in d.glob("*.py"):
    t = p.read_text(errors="ignore")
    m = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", t, re.M)
    if not m: continue
    revs[m.group(1)] = p.name
    for line in re.findall(r"^down_revision[^=]*=\s*(.+)$", t, re.M):
        downs.update(re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", line))
heads = [r for r in revs if r not in downs]
missing = [d_ for d_ in downs if d_ not in revs]
print("HEADS", " ".join(sorted(heads)))
print("MISSING", " ".join(sorted(missing)))
PY
)
HEADS=$(sed -n 's/^HEADS //p' <<<"$H"); MISSING=$(sed -n 's/^MISSING //p' <<<"$H")
[[ $(wc -w <<<"$HEADS") -eq 1 ]] && ok "single head: $HEADS" || bad "expected 1 head, found: ${HEADS:-none}"
[[ -z "$MISSING" ]] && ok "no dangling down_revision" || bad "down_revision points at revisions we do not have: $MISSING"

# ---------------------------------------------------------------- 5. versions
step "VERSION, CHANGELOG and README agree"
V=$(cat VERSION 2>/dev/null | tr -d '[:space:]')
C=$(grep -m1 '^## Version' CHANGELOG.md 2>/dev/null | sed 's/^## Version \([0-9.]*\).*/\1/')
D=$(grep -m1 'Current version' README.md 2>/dev/null | sed 's/.*`\([0-9.]*\)`.*/\1/')
[[ "$V" == "$C" ]] && ok "CHANGELOG newest entry is $C" || bad "VERSION=$V but CHANGELOG's newest is ${C:-none}"
[[ "$V" == "$D" ]] && ok "README says $D"              || bad "VERSION=$V but README says ${D:-none}"

# ---------------------------------------------------------------- 6. markers
step "no unresolved merge conflicts"
# ★Must not match `<<<<<<< SEARCH` — that is edit-block prompt TEXT inside
# tool implementations, not a conflict. A looser pattern cried wolf once.
# Match the marker LINE, then drop the ones that are SEARCH/REPLACE blocks.
# Filtering by "does this file mention SEARCH anywhere" is wrong — it both
# hides real conflicts in such a file and mis-detects when it does not.
M=$(grep -rnE '^(<<<<<<<|>>>>>>>)( |$)' \
      --include='*.py' --include='*.vue' --include='*.ts' --include='*.json' --include='*.md' \
      backend frontend locales docs 2>/dev/null \
    | grep -v '\.bak-' \
    | grep -vE '(SEARCH|REPLACE)['\''"[:space:]]*$' \
    | cut -d: -f1 | sort -u)
[[ -z "$M" ]] && ok "none" || { bad "conflict markers left in:"; sed 's/^/   /' <<<"$M"; }

# ---------------------------------------------------------------- verdict
printf "\n"
if [[ $FAILED -eq 0 ]]; then
  printf "${GRN}${BOLD}PASS${OFF}  safe to push  ${DIM}(%s)${OFF}\n\n" "$(date '+%H:%M:%S')"
  exit 0
fi
printf "${RED}${BOLD}FAIL${OFF}  %d check(s) failed — do not push\n\n" "$FAILED"
exit 1
