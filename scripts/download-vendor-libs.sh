#!/usr/bin/env bash
# Update the vendored JS libraries used inside artifact iframes and headless
# rendering.
#
# ★THIS IS AN UPDATE TOOL, NOT A BUILD STEP. The libraries are COMMITTED under
# frontend/public/libs/. The Docker build copies them; it no longer downloads
# them. Run this by hand to move a version, then review the diff and commit.
#
# Why they are committed rather than fetched during the build:
#   1. Reproducibility. Four of these URLs were unpinned or major-range pinned,
#      so two builds a week apart could bake different code into the artifact
#      sandbox. Not theoretical: `@babel/standalone` was unpinned and had
#      rolled to 8.0.4 — a MAJOR version — silently changing the JSX
#      transpiler every dashboard is rendered with.
#   2. Integrity. There was no checksum on any download. Whatever the CDN
#      returned was baked in and then EXECUTED inside every artifact sandbox.
#   3. Offline builds. The stated purpose of these files is airgapped artifact
#      rendering, yet building the image required nine CDN fetches.
#   4. Tests. tests/unit/fork/test_pdf_export.py needs them present; against a
#      bare checkout it silently skipped itself.
#
# Every version below is exact. Every download is verified against
# frontend/public/libs/libs.sha256 before it is allowed to replace anything.
set -euo pipefail

LIBS_DIR="${1:-frontend/public/libs}"
MANIFEST="$LIBS_DIR/libs.sha256"

TAILWIND_VERSION="3.4.16"
REACT_VERSION="18.3.1"
BABEL_VERSION="8.0.4"
ECHARTS_VERSION="5.6.1"
PDFJS_VERSION="3.11.174"

mkdir -p "$LIBS_DIR"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

fetch() {
  echo "  $2  <-  $1"
  curl -fsSL "$1" -o "$STAGING/$2"
}

echo "Fetching vendored JS libraries (exact versions) ..."

# Tailwind CSS Play CDN — JIT compiler for runtime Tailwind inside artifacts.
fetch "https://cdn.tailwindcss.com/${TAILWIND_VERSION}" "tailwindcss-${TAILWIND_VERSION}.js"

# React 18 UMD builds. The development build is deliberately kept: the artifact
# iframe loads it so a broken dashboard reports a readable error, not a
# minified one.
fetch "https://unpkg.com/react@${REACT_VERSION}/umd/react.production.min.js" "react-18.production.min.js"
fetch "https://unpkg.com/react@${REACT_VERSION}/umd/react.development.js" "react-18.development.js"
fetch "https://unpkg.com/react-dom@${REACT_VERSION}/umd/react-dom.production.min.js" "react-dom-18.production.min.js"
fetch "https://unpkg.com/react-dom@${REACT_VERSION}/umd/react-dom.development.js" "react-dom-18.development.js"

# Babel Standalone — in-browser JSX transpilation.
# ★Pinned to an exact MAJOR version deliberately; this used to float.
fetch "https://unpkg.com/@babel/standalone@${BABEL_VERSION}/babel.min.js" "babel-standalone.min.js"

# ECharts — charting inside artifacts.
fetch "https://cdn.jsdelivr.net/npm/echarts@${ECHARTS_VERSION}/dist/echarts.min.js" "echarts-5.min.js"

# PDF.js UMD build (exposes global `pdfjsLib`) plus its worker, for the
# <BowFile> inline PDF viewer inside the artifact sandbox.
fetch "https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/build/pdf.min.js" "pdf.min.js"
fetch "https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.js" "pdf.worker.min.js"

echo
if [ "${UPDATE_MANIFEST:-}" = "1" ]; then
  # Deliberate version bump: adopt what was downloaded and rewrite the
  # manifest. ★Review the diff before committing — these files are executed
  # inside the artifact sandbox.
  echo "UPDATE_MANIFEST=1 — accepting downloads and rewriting $MANIFEST"
  cp "$STAGING"/*.js "$LIBS_DIR/"
  ( cd "$LIBS_DIR" && shasum -a 256 \
      tailwindcss-*.js react-18.development.js react-18.production.min.js \
      react-dom-18.development.js react-dom-18.production.min.js \
      babel-standalone.min.js echarts-5.min.js pdf.min.js pdf.worker.min.js \
      > libs.sha256 )
  echo "Manifest rewritten. REVIEW THE DIFF before committing."
else
  # Default: verify the downloads still match what is committed. A mismatch at
  # the SAME pinned version is exactly the event worth stopping for.
  if [ ! -f "$MANIFEST" ]; then
    echo "ERROR: $MANIFEST is missing. Run with UPDATE_MANIFEST=1 to create it." >&2
    exit 1
  fi
  echo "Verifying downloads against $MANIFEST ..."
  fail=0
  while read -r want name; do
    [ -n "${name:-}" ] || continue
    if [ ! -f "$STAGING/$name" ]; then
      echo "  MISSING from download: $name" >&2; fail=1; continue
    fi
    got="$(shasum -a 256 "$STAGING/$name" | cut -d' ' -f1)"
    if [ "$got" != "$want" ]; then
      echo "  MISMATCH $name" >&2
      echo "    committed: $want" >&2
      echo "    upstream:  $got" >&2
      fail=1
    else
      echo "  ok  $name"
    fi
  done < "$MANIFEST"
  if [ "$fail" -ne 0 ]; then
    echo >&2
    echo "Upstream bytes differ from the committed files at the SAME pinned" >&2
    echo "version. Nothing was written. Investigate before accepting; re-run" >&2
    echo "with UPDATE_MANIFEST=1 only once you are satisfied." >&2
    exit 1
  fi
  echo
  echo "All downloads match the committed files. Nothing to do."
fi
