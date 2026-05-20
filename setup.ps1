# One-time setup: create Python venv + install backend & frontend dependencies
# Run this once after cloning the repo: .\setup.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " Siriwattan Chatbot — Initial Setup" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan

# 1. Check Python
try {
    $pyVer = (& python --version) 2>&1
    Write-Host "[OK] $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] ไม่พบ Python — โหลดที่ https://www.python.org/downloads/ (เลือก 3.12 หรือใหม่กว่า)" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

# 2. Check Node
try {
    $nodeVer = (& node --version) 2>&1
    Write-Host "[OK] Node $nodeVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] ไม่พบ Node.js — โหลดที่ https://nodejs.org (LTS)" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

# 3. Backend venv + deps
Write-Host "`n--- Backend ---" -ForegroundColor Cyan
if (-not (Test-Path "backend\.venv\Scripts\python.exe")) {
    Write-Host "สร้าง Python venv..."
    python -m venv backend\.venv
}
Write-Host "ลง Python dependencies..."
& .\backend\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 4. Backend .env
if (-not (Test-Path "backend\.env")) {
    Write-Host "`n[WARNING] ยังไม่พบ backend\.env — copy จาก backend\.env.example ให้แล้ว" -ForegroundColor Yellow
    Copy-Item backend\.env.example backend\.env
    Write-Host "อย่าลืมแก้ OPENAI_API_KEY ใน backend\.env ก่อนรันจริง" -ForegroundColor Yellow
}

# 5. Frontend deps
Write-Host "`n--- Frontend ---" -ForegroundColor Cyan
Set-Location frontend
if (-not (Test-Path "node_modules")) {
    Write-Host "ลง Node dependencies..."
    npm install --no-audit --no-fund
} else {
    Write-Host "node_modules มีอยู่แล้ว — ข้าม npm install"
}
Set-Location ..

Write-Host "`n===========================================" -ForegroundColor Green
Write-Host " เสร็จเรียบร้อย!" -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor Green
Write-Host "ขั้นต่อไป:"
Write-Host "  1. แก้ backend\.env ใส่ OPENAI_API_KEY (ถ้ายัง)"
Write-Host "  2. รัน 2 terminals:"
Write-Host "       Terminal 1: .\start-backend.ps1"
Write-Host "       Terminal 2: .\start-frontend.ps1"
Write-Host "  3. เปิด http://localhost:3002"
Read-Host "`nกด Enter เพื่อปิด"
