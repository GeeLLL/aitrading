#!/bin/bash
# Daily log rotation: keeps logs under control
#
# Deletes logs older than 60 days
# Compresses logs older than 7 days
# Runs at 2:00 AM PT every day

REPO_ROOT="/Users/ge/ge/aitrading"
LOGS_DIR="${REPO_ROOT}/logs"
RETENTION_DAYS=60
COMPRESS_AGE_DAYS=7

echo "[$(date)] Starting log rotation..."

cd "$LOGS_DIR" || exit 1

# 1. Compress old log files (>7 days)
echo "Compressing logs older than $COMPRESS_AGE_DAYS days..."
find . -type f \( -name "*.log" -o -name "*.json" -o -name "*.jsonl" \) -mtime +$COMPRESS_AGE_DAYS ! -name "*.gz" -exec gzip {} \;

# 2. Delete very old logs (>60 days)
echo "Deleting logs older than $RETENTION_DAYS days..."
find . -type f \( -name "*.log.gz" -o -name "*.json.gz" -o -name "*.jsonl.gz" \) -mtime +$RETENTION_DAYS -delete
find . -type f \( -name "*.log" -o -name "*.json" -o -name "*.jsonl" \) -mtime +$RETENTION_DAYS -delete

# 3. Clean empty directories
echo "Cleaning empty directories..."
find . -type d -empty -delete

# 4. Report
echo "[$(date)] ✅ Log rotation complete"
du -sh "$LOGS_DIR" | awk '{print "Total size: " $1}'
