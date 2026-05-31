#!/usr/bin/env bash
# Backs up ~/.claude JSONL and session-meta files to a dated snapshot directory.
# Destination: ~/.claude/backups/YYYY-MM-DD/
# Retention: keeps the 30 most recent daily snapshots, deletes older ones.

set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
BACKUP_ROOT="${CLAUDE_DIR}/backups"
TODAY="$(date +%Y-%m-%d)"
DEST="${BACKUP_ROOT}/${TODAY}"

mkdir -p "${DEST}"

# JSONL conversation files
if [ -d "${CLAUDE_DIR}/projects" ]; then
    rsync -a --relative \
        "${CLAUDE_DIR}/./projects" \
        "${DEST}/" \
        --include="*.jsonl" \
        --exclude="*" \
        2>/dev/null || true
fi

# Session-meta JSON files
if [ -d "${CLAUDE_DIR}/usage-data/session-meta" ]; then
    rsync -a \
        "${CLAUDE_DIR}/usage-data/session-meta/" \
        "${DEST}/session-meta/" \
        2>/dev/null || true
fi

# Prune: keep the 30 most recent snapshots, delete older ones
KEEP=30
DIRS=( $(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name "20*" | sort) )
TOTAL="${#DIRS[@]}"
if (( TOTAL > KEEP )); then
    for DIR in "${DIRS[@]:0:$((TOTAL - KEEP))}"; do
        rm -rf "${DIR}"
    done
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') backup done -> ${DEST}"
