# ============================================================
# SCP — CI Benchmark (PowerShell / Windows)
# ============================================================
# Cách dùng:
#   .\scripts\run_ci_benchmark.ps1
#   .\scripts\run_ci_benchmark.ps1 -StartServer
#   $env:SCP_URL = "http://127.0.0.1:8000"; .\scripts\run_ci_benchmark.ps1
# ============================================================
param(
    [string]$Url = $(if ($env:SCP_URL) { $env:SCP_URL } else { "http://127.0.0.1:8000" }),
    [string]$Token = $(
        if ($env:SCP_AUTH_TOKEN) { $env:SCP_AUTH_TOKEN }
        elseif ($env:SCP_AUTH_TOKEN_SECRET) { $env:SCP_AUTH_TOKEN_SECRET }
        else { "scp-ci-test-token" }
    ),
    [switch]$StartServer,
    [int]$WaitServerSec = 120
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " SCP CI Benchmark (Windows)" -ForegroundColor Cyan
Write-Host " ROOT = $Root" -ForegroundColor Yellow
Write-Host " URL  = $Url" -ForegroundColor Yellow
Write-Host " StartServer = $StartServer" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path "scripts\ci_benchmark.py")) {
    Write-Error "Không thấy scripts\ci_benchmark.py — copy bộ CI vào root repo SCP"
}
if (-not (Test-Path "benchmark")) {
    Write-Error "Không thấy thư mục benchmark\"
}

New-Item -ItemType Directory -Force -Path "benchmark_results", "logs" | Out-Null

# requests
python -c "import requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    pip install -q requests python-dotenv
}

$serverProc = $null
try {
    if ($StartServer) {
        $env:SCP_SKIP_STARTUP_GATE = "1"
        $env:SCP_DEV_MODE = "1"
        $env:SCP_EVOLUTION_ENABLED = "0"
        $env:SCP_AUTO_APPROVE_TIER3 = "0"
        $env:SCP_ENABLE_POISON_COUNTER = "0"

        Write-Host "[ci] Start server nền..." -ForegroundColor Green
        if (Test-Path "start_api.py") {
            $serverProc = Start-Process -FilePath "python" -ArgumentList "start_api.py" `
                -RedirectStandardOutput "logs\ci_server.log" `
                -RedirectStandardError "logs\ci_server_err.log" `
                -PassThru -WindowStyle Hidden
        } else {
            Write-Error "Không thấy start_api.py"
        }
        Write-Host "[ci] Server PID=$($serverProc.Id)"
    }

    python scripts\ci_benchmark.py `
        --url $Url `
        --token $Token `
        --config scripts\ci_thresholds.json `
        --output benchmark_results\ci_latest.json `
        --wait-server $WaitServerSec

    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "`nCI BENCHMARK: PASS" -ForegroundColor Green
    } else {
        Write-Host "`nCI BENCHMARK: FAIL (exit $code)" -ForegroundColor Red
    }
    exit $code
}
finally {
    if ($null -ne $serverProc -and -not $serverProc.HasExited) {
        Write-Host "[ci] Dừng server PID=$($serverProc.Id)" -ForegroundColor Yellow
        Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue
    }
}
