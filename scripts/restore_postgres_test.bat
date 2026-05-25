@echo off
setlocal

set CONTAINER_NAME=siriwattana-postgres-prod
set DB_USER=chatbot
set RESTORE_DB=chatbot_restore_test

if "%~1"=="" (
    echo Usage: scripts\restore_postgres_test.bat backups\chatbot_prod_backup_xxx.sql
    exit /b 1
)

set BACKUP_FILE=%~1

if not exist "%BACKUP_FILE%" (
    echo Backup file not found: %BACKUP_FILE%
    exit /b 1
)

echo Restore test target database: %RESTORE_DB%
echo Backup file: %BACKUP_FILE%

echo Dropping old restore test database if exists...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d postgres -c "DROP DATABASE IF EXISTS %RESTORE_DB%;"

if errorlevel 1 (
    echo Failed to drop restore test database.
    exit /b 1
)

echo Creating restore test database...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d postgres -c "CREATE DATABASE %RESTORE_DB%;"

if errorlevel 1 (
    echo Failed to create restore test database.
    exit /b 1
)

echo Enabling pgvector extension...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB% -c "CREATE EXTENSION IF NOT EXISTS vector;"

if errorlevel 1 (
    echo Failed to enable vector extension.
    exit /b 1
)

echo Restoring backup into %RESTORE_DB%...
type "%BACKUP_FILE%" | docker exec -i %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB%

if errorlevel 1 (
    echo Restore failed.
    exit /b 1
)

echo Restore completed successfully.
echo Checking restored tables...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB% -c "\dt"

echo Checking restored users...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB% -c "SELECT id, username, role FROM users ORDER BY id DESC LIMIT 5;"

echo Checking restored knowledge...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB% -c "SELECT id, question, source FROM knowledge ORDER BY id DESC LIMIT 5;"

echo Checking restored chat history...
docker exec %CONTAINER_NAME% psql -U %DB_USER% -d %RESTORE_DB% -c "SELECT id, user_id, session_id, question, source FROM chat_history ORDER BY id DESC LIMIT 5;"

echo.
echo Restore test finished.
echo This script restored into %RESTORE_DB% only.
echo Production database chatbot_prod was not overwritten.

endlocal