# Sửa iso_runner.py đọc JSONL từ cấp benchmark/ gốc
$ErrorActionPreference = "Stop"
$r = Get-Content "benchmark\iso_runner.py" -Raw -Encoding UTF8
$old = 'HERE = Path(__file__).parent
QUESTIONS_FILE = HERE / "questions" / "iso_comprehensive.jsonl"
ATTACKS_FILE = HERE / "attacks" / "iso_adversarial.jsonl"
RESULTS_DIR = HERE / "results"'
$new = 'HERE = Path(__file__).parent
QUESTIONS_FILE = HERE / "iso_comprehensive.jsonl"
if not QUESTIONS_FILE.exists():
    QUESTIONS_FILE = HERE / "questions" / "iso_comprehensive.jsonl"
ATTACKS_FILE = HERE / "iso_adversarial.jsonl"
if not ATTACKS_FILE.exists():
    ATTACKS_FILE = HERE / "attacks" / "iso_adversarial.jsonl"
RESULTS_DIR = HERE / "results"'
if ($r.Contains($old)) {
    $r = $r.Replace($old, $new)
    Set-Content -Path "benchmark\iso_runner.py" -Value $r -Encoding UTF8 -NoNewline
    Write-Host "[OK] Da sua iso_runner.py doc tu cap benchmark/ goc" -ForegroundColor Green
} else {
    Write-Host "[SKIP] iso_runner.py da sua roi hoac khong tim thay anchor" -ForegroundColor Yellow
}
