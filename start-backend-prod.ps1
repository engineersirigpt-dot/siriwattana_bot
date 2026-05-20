# Production-style start: multiple workers, NO --reload.
# Use this when deploying to a real server or stress-testing concurrency.
# For day-to-day dev keep using .\start-backend.ps1 (--reload, 1 worker).

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\backend

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "ERROR: ยังไม่ได้ตั้ง Python venv" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "ERROR: ไม่พบไฟล์ backend\.env" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

# Auto-pick workers: (2 * CPU cores) + 1, capped at 9.
$cores = [Environment]::ProcessorCount
$workers = [Math]::Min(($cores * 2) + 1, 9)

Write-Host "===========================================" -ForegroundColor Green
Write-Host " Siriwattan Chatbot Backend (PRODUCTION)" -ForegroundColor Green
Write-Host " http://localhost:8000  (API docs: /docs)" -ForegroundColor Green
Write-Host " Workers: $workers (CPU cores: $cores)" -ForegroundColor Green
Write-Host " HOT RELOAD: OFF — restart manually after code changes" -ForegroundColor Yellow
Write-Host " กด Ctrl+C เพื่อหยุด server" -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor Green

& .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers $workers
