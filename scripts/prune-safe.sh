#!/bin/bash
# =============================================================================
# Reclaim disk after a bake — WITHOUT throwing away the cache that makes the
# next bake fast.
# =============================================================================
#
#   ./scripts/prune-safe.sh            # keep the last 7 days of build cache
#   ./scripts/prune-safe.sh 72h        # keep the last 3 days
#
# ★Why not `docker builder prune -af`.
# A full prune deletes every layer, including the ones that cost the most and
# change the least: the apt install (658 MB), `playwright install --with-deps`,
# the Chromium download (1 GB), the uv sync of the whole Python environment.
# None of those depend on your source. Deleting them turns the next one-line
# change into a from-scratch build — which is exactly what the layer timestamps
# on the current image show happened: apt re-ran on the last bake.
#
# Time-filtered pruning keeps the expensive stable layers (they are re-used, so
# their timestamp keeps moving forward) and drops the intermediate layers from
# superseded builds, which is where the reclaimable space actually is.
#
# ★★★What this script will NEVER do, and neither should you by hand:
#   docker image prune -a      deletes untagged AND unreferenced images — the
#                              rollback images are exactly that
#   docker volume prune        deletes the database
# Both have destroyed recoverable state on this project before. This script
# touches build cache only.
set -euo pipefail

KEEP="${1:-168h}"

echo "Disk before:"
df -h /System/Volumes/Data | tail -1

echo
echo "Build cache before:"
docker buildx du 2>/dev/null | tail -3

echo
echo "Pruning build cache older than ${KEEP}…"
docker builder prune -f --filter "until=${KEEP}"

echo
echo "Build cache after:"
docker buildx du 2>/dev/null | tail -3

echo
echo "Disk after:"
# ★`df -h /` reports the read-only system volume on macOS and will happily tell
# you there is plenty of room while the data volume is full. Always the Data
# volume.
df -h /System/Volumes/Data | tail -1
