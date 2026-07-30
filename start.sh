#!/bin/bash

# Set environment variables
export ENVIRONMENT=production

# =============================================================================
# Required secrets. Refuse to start rather than invent one.
# =============================================================================
#
# ★This used to GENERATE a key when none was given, keep it in memory, and warn.
# The warning scrolled past in the startup log and a new key was minted on every
# single restart, so everything the previous run encrypted — connector
# passwords, OAuth refresh tokens, LDAP binds, SSO secrets, the model API key —
# became unreadable one restart at a time. Nothing errored. Credentials simply
# stopped working, and the cause was a week behind by the time anyone noticed.
#
# Refusing is louder and safer than guessing. A server that will not start gets
# fixed in a minute; a server quietly destroying its own credentials does not.
#
# Deliberately NOT generate-and-save-to-a-file either. The operator owns their
# secrets, and a key this application invented for itself is a key nobody
# knows to back up.
#
fail_missing() {
    echo ""
    echo "============================================================"
    echo " CityAgent Insights cannot start."
    echo "============================================================"
    echo ""
    echo " $1"
    echo ""
    echo " $2"
    echo ""
    echo " Add it to your .env file, then start again."
    echo "============================================================"
    echo ""
    exit 1
}

# ★Accept either spelling while the rename rolls out, preferring the new one.
# The application resolves both too; this guard has to agree with it, or a
# machine that has renamed only one of the two refuses to start.
if [ -n "$DASH_ENCRYPTION_KEY" ]; then
    export BOW_ENCRYPTION_KEY="$DASH_ENCRYPTION_KEY"
elif [ -n "$BOW_ENCRYPTION_KEY" ]; then
    export DASH_ENCRYPTION_KEY="$BOW_ENCRYPTION_KEY"
    echo "NOTE: DASH_ENCRYPTION_KEY is set under its previous name BOW_ENCRYPTION_KEY."
fi

# ★The same mirroring for the database URL, and it is not cosmetic: the
# compose files export DASH_DATABASE_URL only, so $BOW_DATABASE_URL was EMPTY
# on every current installation — and the shipped-password guard below reads
# it. That guard could therefore never fire on the installs it exists to
# protect. Verified against the running container: DASH_ set, BOW_ unset.
if [ -n "$DASH_DATABASE_URL" ]; then
    export BOW_DATABASE_URL="$DASH_DATABASE_URL"
elif [ -n "$BOW_DATABASE_URL" ]; then
    export DASH_DATABASE_URL="$BOW_DATABASE_URL"
    echo "NOTE: DASH_DATABASE_URL is set under its previous name BOW_DATABASE_URL."
fi

if [ -z "$DASH_ENCRYPTION_KEY" ]; then
    fail_missing \
        "DASH_ENCRYPTION_KEY is not set. It decrypts every stored credential." \
        "Generate one once, and never change it:  openssl rand -base64 32"
fi

# A truncated or mistyped key is worse than a missing one: the app starts, and
# every decrypt fails later, one feature at a time. Check it here, once, before
# any worker forks.
if ! python3 -c "
import os, sys
from cryptography.fernet import Fernet
try:
    Fernet(os.environ['DASH_ENCRYPTION_KEY'].encode())
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    fail_missing \
        "DASH_ENCRYPTION_KEY is set but is not a valid key." \
        "It must be 44 characters of url-safe base64. Generate:  openssl rand -base64 32"
fi

# ★The compose files used to default the database password to the literal
# string 'bowpassword', which is published in this repository. An install that
# never set one came up with a password anybody could read, on a port that is
# published to the host. The default is gone; this catches anyone who copied it.
case "$DASH_DATABASE_URL" in
    *:bowpassword@*)
        fail_missing \
            "The database password is still the shipped default, 'bowpassword'." \
            "Set POSTGRES_PASSWORD to something private:  openssl rand -base64 24"
        ;;
esac

# =============================================================================
# Detect available CPUs (cgroup-aware for containers)
# Works with: K8s, Docker, Docker Compose (with or without CPU limits)
# =============================================================================
get_container_cpus() {
    local cpus=0
    
    # Method 1: cgroups v2 (modern K8s 1.25+, Docker with cgroupv2)
    # File contains "quota period" e.g., "200000 100000" for 2 CPUs, or "max 100000" for unlimited
    if [ -f /sys/fs/cgroup/cpu.max ] 2>/dev/null; then
        local quota period
        read -r quota period < /sys/fs/cgroup/cpu.max 2>/dev/null
        if [ "$quota" != "max" ] && [ -n "$quota" ] && [ -n "$period" ] && [ "$period" -gt 0 ] 2>/dev/null; then
            cpus=$((quota / period))
            if [ "$cpus" -gt 0 ] 2>/dev/null; then
                echo "cgroups-v2:$cpus"
                return
            fi
        fi
    fi
    
    # Method 2: cgroups v1 (older K8s, older Docker)
    # -1 means unlimited
    local cg_base=""
    for path in /sys/fs/cgroup/cpu /sys/fs/cgroup/cpu,cpuacct; do
        if [ -f "$path/cpu.cfs_quota_us" ] 2>/dev/null; then
            cg_base="$path"
            break
        fi
    done
    
    if [ -n "$cg_base" ]; then
        local quota=$(cat "$cg_base/cpu.cfs_quota_us" 2>/dev/null)
        local period=$(cat "$cg_base/cpu.cfs_period_us" 2>/dev/null)
        if [ -n "$quota" ] && [ "$quota" -gt 0 ] && [ -n "$period" ] && [ "$period" -gt 0 ] 2>/dev/null; then
            cpus=$((quota / period))
            if [ "$cpus" -gt 0 ] 2>/dev/null; then
                echo "cgroups-v1:$cpus"
                return
            fi
        fi
    fi
    
    # Method 3: Fallback to nproc (no container CPU limit set)
    cpus=$(nproc 2>/dev/null || echo 1)
    echo "nproc:$cpus"
}

# Detect CPUs and parse result
CPU_RESULT=$(get_container_cpus)
CPU_SOURCE="${CPU_RESULT%%:*}"
CPUS="${CPU_RESULT##*:}"

# Ensure CPUS is a valid number
if ! [[ "$CPUS" =~ ^[0-9]+$ ]] || [ "$CPUS" -le 0 ]; then
    CPUS=1
    CPU_SOURCE="fallback"
fi

# Calculate workers: half of available CPUs
# - Minimum: 1 worker
# - Maximum: 4 workers (safety cap to prevent OOM)
DEFAULT_WORKERS=$(( CPUS > 1 ? CPUS / 2 : 1 ))
DEFAULT_WORKERS=$(( DEFAULT_WORKERS > 4 ? 4 : DEFAULT_WORKERS ))
DEFAULT_WORKERS=$(( DEFAULT_WORKERS < 1 ? 1 : DEFAULT_WORKERS ))

# Allow override via environment variable
WORKERS=${UVICORN_WORKERS:-$DEFAULT_WORKERS}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔧 CPU Detection: $CPU_SOURCE"
echo "🖥️  Available CPUs: $CPUS"
echo "🚀 Uvicorn Workers: $WORKERS (max 4, override with UVICORN_WORKERS)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Run database migrations with retries
cd /app/backend
for i in {1..3}; do
    alembic upgrade head && break
    echo "Migration attempt $i failed. Retrying in $((4 * i)) seconds..."
    if [ $i -eq 3 ]; then
        echo "Migration failed after 3 attempts. Exiting."
        exit 1
    fi
    sleep $((4 * i))
done

# Start uvicorn as the single foreground process (SPA is served from the
# same process via SERVE_FRONTEND=1). tini reaps it on shutdown.
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 3000 \
    --ws websockets \
    --log-level info \
    --workers "$WORKERS" \
    --loop uvloop \
    --http httptools
