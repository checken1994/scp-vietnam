# ============================================================
# SCP V5.8 — SERVER DIAGNOSE SCRIPT
# Chạy khi "Failed to connect to 127.0.0.1:8000"
# ============================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SCP V5.8 — SERVER DIAGNOSE" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check if Python process running
Write-Host ""
Write-Host "[1] Check Python process running..." -ForegroundColor Yellow
$pythonProcs = Get-Process -Name "python*" -ErrorAction SilentlyContinue
if ($pythonProcs) {
    Write-Host "  ✅ Python process found:" -ForegroundColor Green
    $pythonProcs | ForEach-Object { Write-Host "     PID=$($_.Id) Name=$($_.ProcessName)" }
} else {
    Write-Host "  ❌ KHÔNG CÓ Python process running!" -ForegroundColor Red
    Write-Host "  → Gà cần chạy: python start_api.py" -ForegroundColor Yellow
}

# 2. Check port 8000
Write-Host ""
Write-Host "[2] Check port 8000..." -ForegroundColor Yellow
$portConn = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -WarningAction SilentlyContinue
if ($portConn.TcpTestSucceeded) {
    Write-Host "  ✅ Port 8000 OPEN — server đang chạy" -ForegroundColor Green
} else {
    Write-Host "  ❌ Port 8000 CLOSED — server KHÔNG chạy" -ForegroundColor Red
    Write-Host "  → Gà cần start server:" -ForegroundColor Yellow
    Write-Host "     cd C:\Users\check\Downloads\scp-vietnam" -ForegroundColor White
    Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "     `$env:SCP_SKIP_STARTUP_GATE = '1'" -ForegroundColor White
    Write-Host "     python start_api.py" -ForegroundColor White
}

# 3. Check if SCP folder correct
Write-Host ""
Write-Host "[3] Check SCP folder..." -ForegroundColor Yellow
$scpDir = "C:\Users\check\Downloads\scp-vietnam"
if (Test-Path "$scpDir\scp\api_server.py") {
    Write-Host "  ✅ SCP folder OK: $scpDir" -ForegroundColor Green
} else {
    Write-Host "  ❌ SCP folder MISSING api_server.py!" -ForegroundColor Red
    Write-Host "  → Gà download + giải nén scp_v104_v58_full.zip vào $scpDir" -ForegroundColor Yellow
}

# 4. Check .env file
Write-Host ""
Write-Host "[4] Check .env file..." -ForegroundColor Yellow
if (Test-Path "$scpDir\.env") {
    $envContent = Get-Content "$scpDir\.env" -Raw
    if ($envContent -match "OPENROUTER_API_KEY=sk-or-v1") {
        Write-Host "  ✅ .env has OPENROUTER_API_KEY" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  .env missing OPENROUTER_API_KEY" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ❌ .env file MISSING!" -ForegroundColor Red
}

# 5. Check __pycache__ (stale code)
Write-Host ""
Write-Host "[5] Check __pycache__ (stale code?)..." -ForegroundColor Yellow
$pycacheCount = (Get-ChildItem -Path $scpDir -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
if ($pycacheCount -gt 0) {
    Write-Host "  ⚠️  Found $pycacheCount __pycache__ dirs — có thể stale" -ForegroundColor Yellow
    Write-Host "  → Chạy: .\CLEAN_INSTALL.ps1" -ForegroundColor White
} else {
    Write-Host "  ✅ No __pycache__ — clean" -ForegroundColor Green
}

# 6. Check venv
Write-Host ""
Write-Host "[6] Check venv..." -ForegroundColor Yellow
if (Test-Path "$scpDir\.venv\Scripts\Activate.ps1") {
    Write-Host "  ✅ .venv exists" -ForegroundColor Green
} else {
    Write-Host "  ❌ .venv MISSING!" -ForegroundColor Red
    Write-Host "  → Run: python -m venv .venv" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  KẾT LUẬN" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Nếu [2] = CLOSED → server chưa chạy. Gà cần:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  cd C:\Users\check\Downloads\scp-vietnam" -ForegroundColor White
Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "  .\CLEAN_INSTALL.ps1" -ForegroundColor White
Write-Host "  pip install -r requirements.txt" -ForegroundColor White
Write-Host "  `$env:SCP_SKIP_STARTUP_GATE = '1'" -ForegroundColor White
Write-Host "  python start_api.py" -ForegroundColor White
Write-Host ""
Write-Host "  (GIỮ terminal này mở — server đang chạy)" -ForegroundColor Yellow
Write-Host "  (Mở terminal KHÁC để test curl)" -ForegroundColor Yellow
Write-Host ""
