@echo off
setlocal

set UPLOAD_DIR=backend\data\uploads
set BACKUP_ROOT=backups

if not exist "%BACKUP_ROOT%" (
    mkdir "%BACKUP_ROOT%"
)

for /f "tokens=1-4 delims=/ " %%a in ("%date%") do (
    set TODAY=%%d-%%b-%%c
)

for /f "tokens=1-3 delims=:." %%a in ("%time%") do (
    set NOW=%%a%%b%%c
)

set NOW=%NOW: =0%
set BACKUP_DIR=%BACKUP_ROOT%\uploads_%TODAY%_%NOW%

echo Creating uploads backup...
echo Source: %UPLOAD_DIR%
echo Output: %BACKUP_DIR%

if not exist "%UPLOAD_DIR%" (
    echo Upload directory does not exist. Creating empty backup folder.
    mkdir "%BACKUP_DIR%"
    echo Upload backup completed with empty folder.
    dir "%BACKUP_DIR%"
    endlocal
    exit /b 0
)

xcopy "%UPLOAD_DIR%" "%BACKUP_DIR%" /E /I /Y

if errorlevel 1 (
    echo Upload backup failed.
    exit /b 1
)

echo Upload backup completed successfully.
dir "%BACKUP_DIR%"

endlocal