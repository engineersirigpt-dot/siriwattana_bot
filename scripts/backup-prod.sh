#!/usr/bin/env bash
#
# Production backup for Sirivatana Chatbot (server 192.168.5.31).
#
# Thin wrapper around backup.sh that points it at the PRODUCTION stack —
# backup.sh's own defaults target the test stack, so running it bare on the
# prod server would silently back up the wrong (or a non-existent) database.
#
# Install (run once on the prod server):
#   chmod +x scripts/backup.sh scripts/backup-prod.sh
#   crontab -e
#     0 2 * * * /home/webadmin/siriwattana_bot/scripts/backup-prod.sh >> /home/webadmin/siriwattana_bot/backups/backup.log 2>&1
#
# Run manually (recommended right after installing, to verify):
#   ./scripts/backup-prod.sh
#
# Restore-test a backup (safe — restores into a throwaway DB, never touches live):
#   CONTAINER_NAME=siriwattana-postgres-prod ./scripts/restore.sh backups/chatbot_prod_backup_<stamp>.sql
#

set -euo pipefail

# Resolve the repo from this script's own location so the paths stay correct
# no matter where it's invoked from (cron runs with a different working dir).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

export CONTAINER_NAME="${CONTAINER_NAME:-siriwattana-postgres-prod}"
export DB_NAME="${DB_NAME:-chatbot_prod}"
export DB_USER="${DB_USER:-chatbot}"
export PROJECT_DIR
export BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
# Prod mounts ./backend/data-prod into the container as /app/data.
export UPLOAD_DIR="${UPLOAD_DIR:-${PROJECT_DIR}/backend/data-prod/uploads}"
export RETENTION_DAYS="${RETENTION_DAYS:-14}"

exec "${SCRIPT_DIR}/backup.sh"
