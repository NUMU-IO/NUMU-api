#!/usr/bin/env bash
# =============================================================================
# NUMU Database Backup Script
# =============================================================================
# Usage:
#   ./scripts/backup.sh                     # Full backup, auto-prune old files
#   ./scripts/backup.sh --no-prune          # Backup only, keep all old backups
#   ./scripts/backup.sh --restore <file>    # Restore from a backup file
#
# Environment variables (or sourced from .env):
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   R2_BACKUP_BUCKET_NAME, BACKUP_RETENTION_DAYS (default: 30)
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL (for R2/S3)
#
# Cron example (daily at 03:00 UTC):
#   0 3 * * * /opt/numu/scripts/backup.sh >> /var/log/numu/backup.log 2>&1
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

# Source .env if it exists
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-numu}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/numu-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BUCKET="${R2_BACKUP_BUCKET_NAME:-numu-db-backups}"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_FILE="numu_${TIMESTAMP}.sql.gz"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILE}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
die() { log "ERROR: $*" >&2; exit 1; }

check_deps() {
    for cmd in pg_dump gzip; do
        command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
    done
}

# ---------------------------------------------------------------------------
# Restore mode
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--restore" ]]; then
    RESTORE_FILE="${2:-}"
    [[ -z "$RESTORE_FILE" ]] && die "Usage: $0 --restore <backup_file.sql.gz>"
    [[ -f "$RESTORE_FILE" ]] || die "Restore file not found: $RESTORE_FILE"

    log "Restoring database from: $RESTORE_FILE"
    log "Target: ${POSTGRES_USER}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
    read -r -p "This will overwrite the database. Continue? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { log "Aborted."; exit 0; }

    PGPASSWORD="${POSTGRES_PASSWORD:-}" \
        gunzip -c "$RESTORE_FILE" | \
        psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"

    log "Restore complete."
    exit 0
fi

# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
PRUNE=true
[[ "${1:-}" == "--no-prune" ]] && PRUNE=false

check_deps
mkdir -p "$BACKUP_DIR"

log "Starting backup: ${POSTGRES_DB} -> ${BACKUP_FILE}"

# Run pg_dump and compress in one pipeline
PGPASSWORD="${POSTGRES_PASSWORD:-}" \
    pg_dump \
        -h "$POSTGRES_HOST" \
        -p "$POSTGRES_PORT" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --no-password \
        --format=plain \
        --no-owner \
        --no-acl \
    | gzip -9 > "$BACKUP_PATH"

BACKUP_SIZE="$(du -sh "$BACKUP_PATH" | cut -f1)"
log "Backup complete: ${BACKUP_PATH} (${BACKUP_SIZE})"

# ---------------------------------------------------------------------------
# Upload to R2 / S3 (optional — requires awscli + R2 credentials)
# ---------------------------------------------------------------------------
if command -v aws >/dev/null 2>&1 && [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
    S3_KEY="daily/${BACKUP_FILE}"
    S3_URI="s3://${BUCKET}/${S3_KEY}"
    ENDPOINT="${AWS_ENDPOINT_URL:-}"

    log "Uploading to ${S3_URI} ..."
    aws s3 cp "$BACKUP_PATH" "$S3_URI" \
        ${ENDPOINT:+--endpoint-url "$ENDPOINT"} \
        --storage-class STANDARD

    log "Upload complete: ${S3_URI}"
else
    log "AWS CLI not available or credentials not set — skipping R2 upload."
    log "Backup stored locally at: ${BACKUP_PATH}"
fi

# ---------------------------------------------------------------------------
# Prune old local backups
# ---------------------------------------------------------------------------
if [[ "$PRUNE" == "true" ]]; then
    log "Pruning local backups older than ${RETENTION_DAYS} days ..."
    find "$BACKUP_DIR" -name "numu_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
    REMAINING="$(find "$BACKUP_DIR" -name "numu_*.sql.gz" | wc -l)"
    log "Pruning complete. ${REMAINING} backup(s) retained locally."
fi

log "Done."
