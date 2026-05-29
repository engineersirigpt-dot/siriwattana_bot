#!/usr/bin/env bash
#
# Daily backup for Sirivatana Chatbot on Linux server.
#
# Backs up:
#   1. PostgreSQL database (pg_dump)
#   2. Upload directory (tar.gz)
#
# Retention: keeps backups for RETENTION_DAYS (default 14 days), deletes older.
#
# Install:
#   chmod +x scripts/backup.sh
#   crontab -e
#     0 2 * * * /home/webadmin/siriwattana_bot/scripts/backup.sh >> /home/webadmin/siriwattana_bot/backups/backup.log 2>&1
#
# Run manually:
#   ./scripts/backup.sh
#

set -euo pipefail

# ----------------------------------------------------------------------------
# Config — change for test vs production
# ----------------------------------------------------------------------------

CONTAINER_NAME="${CONTAINER_NAME:-siriwattana-postgres-test}"
DB_USER="${DB_USER:-chatbot}"
DB_NAME="${DB_NAME:-chatbot_test}"

PROJECT_DIR="${PROJECT_DIR:-/home/webadmin/siriwattana_bot}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
UPLOAD_DIR="${UPLOAD_DIR:-${PROJECT_DIR}/backend/data/uploads}"

RETENTION_DAYS="${RETENTION_DAYS:-14}"

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
DB_BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_backup_${TIMESTAMP}.sql"
UPLOADS_BACKUP_FILE="${BACKUP_DIR}/uploads_${TIMESTAMP}.tar.gz"

echo "============================================================"
echo "Sirivatana Chatbot backup — $(date -Iseconds)"
echo "Container : ${CONTAINER_NAME}"
echo "Database  : ${DB_NAME}"
echo "Retention : ${RETENTION_DAYS} days"
echo "============================================================"

# ----------------------------------------------------------------------------
# 1. PostgreSQL dump
# ----------------------------------------------------------------------------

echo ""
echo "[1/3] Dumping PostgreSQL ${DB_NAME} -> ${DB_BACKUP_FILE}"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: container ${CONTAINER_NAME} is not running. Aborting." >&2
    exit 1
fi

docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" "${DB_NAME}" > "${DB_BACKUP_FILE}"

DB_SIZE="$(du -h "${DB_BACKUP_FILE}" | cut -f1)"
echo "        OK  ${DB_SIZE}"

# ----------------------------------------------------------------------------
# 2. Uploads tarball
# ----------------------------------------------------------------------------

echo ""
echo "[2/3] Archiving uploads -> ${UPLOADS_BACKUP_FILE}"

if [ -d "${UPLOAD_DIR}" ] && [ -n "$(ls -A "${UPLOAD_DIR}" 2>/dev/null)" ]; then
    tar -czf "${UPLOADS_BACKUP_FILE}" -C "$(dirname "${UPLOAD_DIR}")" "$(basename "${UPLOAD_DIR}")"
    UP_SIZE="$(du -h "${UPLOADS_BACKUP_FILE}" | cut -f1)"
    echo "        OK  ${UP_SIZE}"
else
    echo "        skipped (upload dir empty or missing: ${UPLOAD_DIR})"
fi

# ----------------------------------------------------------------------------
# 3. Retention — delete files older than RETENTION_DAYS
# ----------------------------------------------------------------------------

echo ""
echo "[3/3] Pruning backups older than ${RETENTION_DAYS} days"

DELETED_SQL="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name "${DB_NAME}_backup_*.sql" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"
DELETED_TAR="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name "uploads_*.tar.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"

echo "        removed ${DELETED_SQL} old SQL dumps, ${DELETED_TAR} old upload archives"

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo ""
echo "Backup complete. Current backups in ${BACKUP_DIR}:"
ls -lh "${BACKUP_DIR}" 2>/dev/null | tail -n 20 || true

echo ""
echo "============================================================"
