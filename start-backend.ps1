# Start the FastAPI backend on http://localhost:8000
# Double-click this file or run: .\start-backend.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\backend

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "ERROR: ยังไม่ได้ตั้ง Python venv" -ForegroundColor Red
    Write-Host "รัน setup ก่อน: python -m venv backend\.venv ; .\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt" -ForegroundColor Yellow
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "ERROR: ไม่พบไฟล์ backend\.env" -ForegroundColor Red
    Write-Host "copy backend\.env.example เป็น backend\.env แล้วใส่ OPENAI_API_KEY" -ForegroundColor Yellow
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " Siriwattan Chatbot Backend" -ForegroundColor Cyan
Write-Host " http://localhost:8000  (API docs: /docs)" -ForegroundColor Cyan
Write-Host " กด Ctrl+C เพื่อหยุด server" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan

& .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
