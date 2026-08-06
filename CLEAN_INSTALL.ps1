# ============================================================
# SCP V5.8 — CLEAN INSTALL SCRIPT (PowerShell)
# ============================================================
# TẠI SAO script này tồn tại?
#   Gà ghi đè zip mới nhưng vẫn dính lỗi cũ vì:
#     1. __pycache__ cũ chứa byte-code stale → Python chạy code cũ
#     2. data/*.jsonl cũ chứa logs cũ → tưởng "dính bug"
#     3. Thư mục sai (scp_v104 cũ vs scp-vietnam mới)
#
# Script này làm sạch toàn bộ trước khi install bản mới.
# ============================================================

param(
    [string]$ScpDir = "C:\Users\check\Downloads\scp-vietnam",
    [switch]$KeepData,    # Giữ data/ (logs, KB) — chỉ clean code
    [switch]$DryRun       # Hiện thị sẽ làm gì, không thực hiện
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SCP V5.8 — CLEAN INSTALL SCRIPT" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Target: $ScpDir" -ForegroundColor Yellow
Write-Host "  KeepData: $KeepData" -ForegroundColor Yellow
Write-Host "  DryRun: $DryRun" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {
    Write-Host "[DRY RUN] Sẽ thực hiện các bước:" -ForegroundColor Magenta
}

# ============================================================
# Bước 1: Xóa __pycache__ (nguyên nhân #1 lỗi stale code)
# ============================================================
Write-Host ""
Write-Host "[1/5] Xóa __pycache__ directories..." -ForegroundColor Green

$pycacheDirs = Get-ChildItem -Path $ScpDir -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue
$count = ($pycacheDirs | Measure-Object).Count
Write-Host "  Tìm thấy: $count thư mục __pycache__"

if ($count -gt 0) {
    if (-not $DryRun) {
        $pycacheDirs | Remove-Item -Recurse -Force
        Write-Host "  ✅ Đã xóa $count thư mục __pycache__" -ForegroundColor Green
    } else {
        Write-Host "  [DRY RUN] Sẽ xóa $count thư mục" -ForegroundColor Magenta
    }
} else {
    Write-Host "  ℹ️  Không có __pycache__ — sạch rồi" -ForegroundColor Gray
}

# ============================================================
# Bước 2: Xóa .pyc files (nếu còn sót)
# ============================================================
Write-Host ""
Write-Host "[2/5] Xóa .pyc files..." -ForegroundColor Green

$pycFiles = Get-ChildItem -Path $ScpDir -Filter "*.pyc" -Recurse -File -ErrorAction SilentlyContinue
$count = ($pycFiles | Measure-Object).Count
Write-Host "  Tìm thấy: $count file .pyc"

if ($count -gt 0) {
    if (-not $DryRun) {
        $pycFiles | Remove-Item -Force
        Write-Host "  ✅ Đã xóa $count file .pyc" -ForegroundColor Green
    } else {
        Write-Host "  [DRY RUN] Sẽ xóa $count file" -ForegroundColor Magenta
    }
} else {
    Write-Host "  ℹ️  Không có .pyc — sạch rồi" -ForegroundColor Gray
}

# ============================================================
# Bước 3: Xóa data/*.jsonl logs cũ (nguyên nhân #2 "dính bug cũ")
# ============================================================
Write-Host ""
Write-Host "[3/5] Xóa data/*.jsonl logs cũ..." -ForegroundColor Green

$dataDir = Join-Path $ScpDir "data"
if (Test-Path $dataDir) {
    $jsonlFiles = Get-ChildItem -Path $dataDir -Filter "*.jsonl" -File -ErrorAction SilentlyContinue
    $count = ($jsonlFiles | Measure-Object).Count
    Write-Host "  Tìm thấy: $count file .jsonl trong data/"

    if ($count -gt 0) {
        if (-not $KeepData) {
            if (-not $DryRun) {
                # Backup trước khi xóa
                $backupDir = Join-Path $dataDir "backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
                New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
                $jsonlFiles | Copy-Item -Destination $backupDir
                Write-Host "  📦 Backup vào: $backupDir" -ForegroundColor Yellow

                # Xóa
                $jsonlFiles | Remove-Item -Force
                Write-Host "  ✅ Đã xóa $count file .jsonl (đã backup)" -ForegroundColor Green
            } else {
                Write-Host "  [DRY RUN] Sẽ backup + xóa $count file" -ForegroundColor Magenta
            }
        } else {
            Write-Host "  ⏭️  Skip xóa data/ (KeepData=$KeepData)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  ℹ️  data/ không có .jsonl" -ForegroundColor Gray
    }
} else {
    Write-Host "  ℹ️  Không có thư mục data/" -ForegroundColor Gray
}

# ============================================================
# Bước 4: Xóa .v5bak, .v5.1bak, .v5.3bak, .v57bak, .evolutionbak, .tier3bak
# ============================================================
Write-Host ""
Write-Host "[4/5] Xóa backup files (.v5bak, .v57bak, etc.)..." -ForegroundColor Green

$backupPatterns = @("*.v5bak", "*.v5.1bak", "*.v5.3bak", "*.v57bak", "*.evolutionbak", "*.tier3bak", "*.bak")
$totalBackups = 0
foreach ($pattern in $backupPatterns) {
    $backups = Get-ChildItem -Path $ScpDir -Filter $pattern -Recurse -File -ErrorAction SilentlyContinue
    $count = ($backups | Measure-Object).Count
    if ($count -gt 0) {
        $totalBackups += $count
        if (-not $DryRun) {
            $backups | Remove-Item -Force
        }
    }
}
Write-Host "  Tìm thấy: $totalBackups backup files"
if ($totalBackups -gt 0) {
    if (-not $DryRun) {
        Write-Host "  ✅ Đã xóa $totalBackups backup files" -ForegroundColor Green
    } else {
        Write-Host "  [DRY RUN] Sẽ xóa $totalBackups files" -ForegroundColor Magenta
    }
} else {
    Write-Host "  ℹ️  Không có backup files" -ForegroundColor Gray
}

# ============================================================
# Bước 5: Verify cấu trúc thư mục
# ============================================================
Write-Host ""
Write-Host "[5/5] Verify cấu trúc SCP..." -ForegroundColor Green

$requiredFiles = @(
    "scp\api_server.py",
    "scp\autofix\engine.py",
    "scp\autofix\evolution.py",
    "scp\autofix\runner.py",
    "scp\autofix\llm_fix.py",
    "scp\autofix\classifier.py",
    "scp\autofix\permission.py",
    "scp\runtime\judge.py",
    "scp\runtime\slms.py",
    "scp\meta\why_engine.py",
    "scp\meta\multi_llm_check.py",
    "scp\meta\metawhy_monitor.py",
    "scp\security\bypass_encrypt.py",
    "scp\security\attack_memory.py",
    "scp\core\code_evolution_agent.py",
    "scp\brain\error_store.py",
    "scp\knowledge\source_reputation.py",
    "scp\data_sources\medical.py",
    "scp\data_sources\cybersecurity.py",
    "scp\data_sources\agriculture.py",
    "start_api.py",
    "requirements.txt",
    ".env",
    "QUICKSTART_V58.md"
)

$missing = @()
foreach ($f in $requiredFiles) {
    $fullPath = Join-Path $ScpDir $f
    if (-not (Test-Path $fullPath)) {
        $missing += $f
    }
}

if ($missing.Count -eq 0) {
    Write-Host "  ✅ Tất cả $($requiredFiles.Count) files cần thiết đều có" -ForegroundColor Green
} else {
    Write-Host "  ❌ THIẾU $($missing.Count) files:" -ForegroundColor Red
    foreach ($f in $missing) {
        Write-Host "     - $f" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  → Gà cần giải nén zip v5.8 ĐÚNG thư mục!" -ForegroundColor Yellow
}

# ============================================================
# Tóm tắt
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  TÓM TẮT CLEAN INSTALL" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "  [DRY RUN] Không thực hiện gì — chạy lại không -DryRun" -ForegroundColor Magenta
} else {
    Write-Host "  ✅ __pycache__ đã xóa" -ForegroundColor Green
    Write-Host "  ✅ .pyc đã xóa" -ForegroundColor Green
    if (-not $KeepData) {
        Write-Host "  ✅ data/*.jsonl đã backup + xóa" -ForegroundColor Green
    }
    Write-Host "  ✅ backup files đã xóa" -ForegroundColor Green
    Write-Host ""
    Write-Host "  🚀 SCP sẵn sàng chạy bản mới!" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Bước tiếp theo:" -ForegroundColor Yellow
    Write-Host "    cd $ScpDir" -ForegroundColor White
    Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "    pip install -r requirements.txt" -ForegroundColor White
    Write-Host "    python scripts\dedup_bypasses.py --input data\bypasses.jsonl" -ForegroundColor White
    Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "    `$env:SCP_SKIP_STARTUP_GATE = '1'" -ForegroundColor White
    Write-Host "    python start_api.py" -ForegroundColor White
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
