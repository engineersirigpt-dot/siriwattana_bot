# Start the Next.js frontend on http://localhost:3002
# Double-click this file or run: .\start-frontend.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\frontend

if (-not (Test-Path "node_modules")) {
    Write-Host "node_modules ยังไม่มี — กำลังลง dependencies..." -ForegroundColor Yellow
    npm install --no-audit --no-fund
}

Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " Siriwattan Chatbot Frontend" -ForegroundColor Cyan
Write-Host " http://localhost:3002" -ForegroundColor Cyan
Write-Host " กด Ctrl+C เพื่อหยุด server" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan

$env:NEXT_PUBLIC_API_BASE = "http://localhost:8000"
npm run dev -- -p 3002
