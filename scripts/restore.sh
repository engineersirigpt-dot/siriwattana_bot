#!/usr/bin/env bash
#
# Restore a Sirivatana Chatbot PostgreSQL backup into a TEST database
# (NEVER overwrites the live database).
#
# Usage:
#   ./scripts/restore.sh backups/chatbot_test_backup_2026-05-27_091534.sql
#
# What it does:
#   1. Drops the restore-test DB if it exists
#   2. Creates a fresh restore-test DB
#   3. Enables pgvector extension
#   4. Loads the SQL dump
#   5. Sanity-checks: tables, users, knowledge, chat_history counts
#
# Safety:
#   - Hard-coded restore target is "chatbot_restore_test", NOT chatbot_test or chatbot_prod
#   - To clean up after testing: drop chatbot_restore_test (see end of script output)
#

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-siriwattana-postgres-test}"
DB_USER="${DB_USER:-chatbot}"
RESTORE_DB="${RESTORE_DB:-chatbot_restore_test}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.sql>"
    echo ""
    echo "Available backups:"
    ls -lh backups/*.sql 2>/dev/null || echo "  (none found in ./backups/)"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

echo "============================================================"
echo "Restore-test"
echo "Container : ${CONTAINER_NAME}"
echo "Source    : ${BACKUP_FILE}  ($(du -h "$BACKUP_FILE" | cut -f1))"
echo "Target DB : ${RESTORE_DB}   (TEST ONLY — does not touch live data)"
echo "============================================================"

echo ""
echo "[1/5] Dropping old ${RESTORE_DB} if it exists..."
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS ${RESTORE_DB};" >/dev/null

echo "[2/5] Creating fresh ${RESTORE_DB}..."
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d postgres \
    -c "CREATE DATABASE ${RESTORE_DB};" >/dev/null

echo "[3/5] Enabling pgvector extension..."
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${RESTORE_DB}" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

echo "[4/5] Loading backup into ${RESTORE_DB}..."
cat "$BACKUP_FILE" | docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${RESTORE_DB}" >/dev/null

echo "[5/5] Sanity check..."
echo ""
echo "--- Tables restored ---"
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${RESTORE_DB}" -c "\dt"

echo ""
echo "--- Row counts ---"
docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${RESTORE_DB}" -c "
SELECT 'users'     AS table_name, COUNT(*) FROM users
UNION ALL SELECT 'knowledge',     COUNT(*) FROM knowledge
UNION ALL SELECT 'knowledge_vec', COUNT(*) FROM knowledge_vec
UNION ALL SELECT 'chat_sessions', COUNT(*) FROM chat_sessions
UNION ALL SELECT 'chat_history',  COUNT(*) FROM chat_history
UNION ALL SELECT 'attachments',   COUNT(*) FROM attachments
ORDER BY table_name;
"

echo ""
echo "============================================================"
echo "Restore-test complete."
echo "Live database was NOT modified."
echo ""
echo "To clean up restore-test DB when done:"
echo "  docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d postgres \\"
echo "      -c \"DROP DATABASE IF EXISTS ${RESTORE_DB};\""
echo "============================================================"
