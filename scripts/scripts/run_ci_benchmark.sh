#!/usr/bin/env bash
# ============================================================
# SCP — Chạy CI Benchmark (local hoặc CI)
# ============================================================
# Cách dùng:
#   ./scripts/run_ci_benchmark.sh
#   SCP_URL=http://127.0.0.1:8000 ./scripts/run_ci_benchmark.sh
#   START_SERVER=1 ./scripts/run_ci_benchmark.sh
#
# Biến môi trường:
#   START_SERVER=1     — tự start python start_api.py nền
#   SCP_URL            — mặc định http://127.0.0.1:8000
#   SCP_AUTH_TOKEN     — Bearer token
#   SCP_SKIP_STARTUP_GATE=1 (khi start server)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

URL="${SCP_URL:-http://127.0.0.1:8000}"
TOKEN="${SCP_AUTH_TOKEN:-${SCP_AUTH_TOKEN_SECRET:-scp-ci-test-token}}"
START_SERVER="${START_SERVER:-0}"
WAIT_SEC="${SCP_WAIT_SERVER_SEC:-120}"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "[ci] Dừng server PID ${SERVER_PID}"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "============================================================"
echo " SCP CI Benchmark"
echo " ROOT=${ROOT}"
echo " URL=${URL}"
echo " START_SERVER=${START_SERVER}"
echo "============================================================"

if [[ ! -f "scripts/ci_benchmark.py" ]]; then
  echo "ERROR: không thấy scripts/ci_benchmark.py" >&2
  exit 2
fi

if [[ ! -d "benchmark" ]]; then
  echo "ERROR: không thấy thư mục benchmark/" >&2
  exit 2
fi

# Cài dependency tối thiểu nếu thiếu
python -c "import requests" 2>/dev/null || pip install -q requests python-dotenv

if [[ "${START_SERVER}" == "1" ]]; then
  echo "[ci] Start server nền..."
  export SCP_SKIP_STARTUP_GATE="${SCP_SKIP_STARTUP_GATE:-1}"
  export SCP_DEV_MODE="${SCP_DEV_MODE:-1}"
  # Không bật evolution / poison trên CI
  export SCP_EVOLUTION_ENABLED=0
  export SCP_AUTO_APPROVE_TIER3=0
  export SCP_ENABLE_POISON_COUNTER=0

  if [[ -f "start_api.py" ]]; then
    python start_api.py > logs/ci_server.log 2>&1 &
    SERVER_PID=$!
  elif [[ -f "scp/api_server.py" ]]; then
    python -c "import uvicorn; uvicorn.run('scp.api_server:app', host='127.0.0.1', port=8000)" \
      > logs/ci_server.log 2>&1 &
    SERVER_PID=$!
  else
    echo "ERROR: không tìm thấy start_api.py" >&2
    exit 2
  fi
  mkdir -p logs
  echo "[ci] Server PID=${SERVER_PID}, log=logs/ci_server.log"
fi

mkdir -p benchmark_results logs

python scripts/ci_benchmark.py \
  --url "${URL}" \
  --token "${TOKEN}" \
  --config scripts/ci_thresholds.json \
  --output benchmark_results/ci_latest.json \
  --wait-server "${WAIT_SEC}" \
  "$@"

EXIT_CODE=$?
echo "[ci] Exit code=${EXIT_CODE}"
exit "${EXIT_CODE}"
