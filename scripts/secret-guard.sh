#!/usr/bin/env bash
#
# Protect the one secret whose loss cannot be recovered from.
#
# DASH_ENCRYPTION_KEY is the Fernet key for every secret this install stores:
# SMTP passwords, the LDAP bind password, every SSO client secret. They are held
# as ciphertext in Postgres. Lose the key and a database dump does not save you
# — the rows are still there and no longer mean anything, permanently.
#
# It lives in .env, which is gitignored (correctly — it must never be pushed).
# So the key exists in exactly one place on one laptop, and until now the only
# other copy was inside a hand-taken tarball that also lives on that laptop.
# One disk failure loses both.
#
# This stores a second copy in the macOS login Keychain: encrypted at rest,
# outside the repo, outside the working tree, and included in a Keychain backup.
# It is not a remote backup and is not meant to be one — it removes the
# single-file failure, which is the part that is silently fatal.
#
#   ./scripts/secret-guard.sh save     store/refresh the Keychain copy from .env
#   ./scripts/secret-guard.sh check    verify the two copies still agree
#   ./scripts/secret-guard.sh restore  print the Keychain copy (recovery)
#
# `check` is the one to run habitually — a stale Keychain copy is worse than no
# copy, because it looks like protection.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/.env"
SERVICE="cityagent-insights"
ACCOUNT="DASH_ENCRYPTION_KEY"

die() { echo "secret-guard: $*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || die "macOS only — the Keychain is the store here."
command -v security >/dev/null || die "'security' not on PATH"

key_from_env() {
  [[ -f "$ENV_FILE" ]] || die "no .env at $ENV_FILE"
  local v
  v="$(grep -E '^DASH_ENCRYPTION_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')"
  [[ -n "$v" ]] || die "DASH_ENCRYPTION_KEY is not set in .env"
  printf '%s' "$v"
}

key_from_keychain() {
  security find-generic-password -s "$SERVICE" -a "$ACCOUNT" -w 2>/dev/null || true
}

case "${1:-check}" in
  save)
    k="$(key_from_env)"
    # -U updates in place when the item already exists.
    security add-generic-password -U -s "$SERVICE" -a "$ACCOUNT" -w "$k" \
      -j "Fernet key for CityAgent Insights. Losing it makes every stored secret undecryptable."
    echo "secret-guard: saved (${#k} chars) to Keychain as $SERVICE/$ACCOUNT"
    ;;
  check)
    env_k="$(key_from_env)"
    kc_k="$(key_from_keychain)"
    if [[ -z "$kc_k" ]]; then
      echo "secret-guard: NO Keychain copy exists. Run: ./scripts/secret-guard.sh save" >&2
      exit 1
    fi
    if [[ "$env_k" != "$kc_k" ]]; then
      echo "secret-guard: MISMATCH — .env and the Keychain hold different keys." >&2
      echo "  Work out which one the database was encrypted with BEFORE overwriting either." >&2
      echo "  Overwriting the wrong way round destroys the secrets you are trying to protect." >&2
      exit 2
    fi
    echo "secret-guard: ok — .env and Keychain agree (${#env_k} chars)"
    ;;
  restore)
    kc_k="$(key_from_keychain)"
    [[ -n "$kc_k" ]] || die "no Keychain copy to restore from"
    printf '%s\n' "$kc_k"
    ;;
  *)
    die "usage: secret-guard.sh [save|check|restore]"
    ;;
esac
