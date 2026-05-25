@echo off
setlocal

set CONTAINER_NAME=siriwattana-postgres-prod
set DB_USER=chatbot
set DB_NAME=chatbot_prod
set BACKUP_DIR=backups

if not exist "%BACKUP_DIR%" (
    mkdir "%BACKUP_DIR%"
)

for /f "tokens=1-4 delims=/ " %%a in ("%date%") do (
    set TODAY=%%d-%%b-%%c
)

for /f "tokens=1-3 delims=:." %%a in ("%time%") do (
    set NOW=%%a%%b%%c
)

set NOW=%NOW: =0%
set BACKUP_FILE=%BACKUP_DIR%\chatbot_prod_backup_%TODAY%_%NOW%.sql

echo Creating PostgreSQL backup...
echo Container: %CONTAINER_NAME%
echo Database: %DB_NAME%
echo Output: %BACKUP_FILE%

docker exec %CONTAINER_NAME% pg_dump -U %DB_USER% %DB_NAME% > "%BACKUP_FILE%"

if errorlevel 1 (
    echo Backup failed.
    exit /b 1
)

echo Backup completed successfully.
dir "%BACKUP_FILE%"

endlocal