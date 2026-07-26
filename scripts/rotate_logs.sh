#!/bin/bash
# Safe log rotation: keeps NOISY logs under control without ever touching evidence.
#
# WHY THIS IS CAREFUL (the previous version was a landmine):
#   - logs/raw is the IMMUTABLE hashed vault. gzip-ing a snapshot changes its
#     path and breaks `RawDataVault.verify`. NEVER touch it.
#   - logs/scheduler holds live *.expected.json / *.start.json that the watchdog
#     reads. Compressing or deleting them silently breaks self-monitoring.
#   - logs/incidents are durable evidence; policy is NEVER delete an incident
#     (resolved ones are archived by scripts/cleanup_expired_incidents.py).
#   So this script only compresses/deletes plain *.log and *.stdout/stderr files,
#   and it PRUNES the three protected trees entirely.
#
# Compresses matching logs older than 7 days; deletes compressed ones older than
# 60 days. Only ever removes *.gz it made itself. Paths are derived from the
# script location, not hardcoded.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOGS_DIR="${REPO_ROOT}/logs"
RETENTION_DAYS=60
COMPRESS_AGE_DAYS=7

# Trees that must never be compressed or deleted by rotation.
PROTECTED=(raw scheduler incidents)

if [ ! -d "$LOGS_DIR" ]; then
  echo "[$(date)] no logs dir at $LOGS_DIR; nothing to rotate"
  exit 0
fi
cd "$LOGS_DIR"

# Build the shared -prune expression for the protected trees.
prune_args=()
for tree in "${PROTECTED[@]}"; do
  prune_args+=(-path "./${tree}" -o -path "./${tree}/*" -o)
done

echo "[$(date)] Starting SAFE log rotation (protected: ${PROTECTED[*]})..."

# 1. Compress rotatable text logs older than N days (never the protected trees,
#    never something already compressed). Only *.log / *.stdout.log / *.jsonl
#    that are NOT under a protected tree.
echo "Compressing *.log / *.jsonl older than ${COMPRESS_AGE_DAYS} days..."
find . \( "${prune_args[@]}" -false \) -prune -o \
  -type f \( -name "*.log" -o -name "*.stdout.jsonl" -o -name "*.stderr.log" \) \
  -mtime +${COMPRESS_AGE_DAYS} ! -name "*.gz" -print0 | xargs -0 -r gzip

# 2. Delete only the *.gz we produced, once they age past retention.
echo "Deleting compressed logs older than ${RETENTION_DAYS} days..."
find . \( "${prune_args[@]}" -false \) -prune -o \
  -type f \( -name "*.log.gz" -o -name "*.stdout.jsonl.gz" -o -name "*.stderr.log.gz" \) \
  -mtime +${RETENTION_DAYS} -print0 | xargs -0 -r rm -f

echo "[$(date)] ✅ Safe log rotation complete"
du -sh "$LOGS_DIR" 2>/dev/null | awk '{print "Total size: " $1}'
