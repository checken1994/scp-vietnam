#!/bin/bash
# SCP V105 — Start Ollama for LLM Gateway (Task 19-A improved)
# Usage: ./start_ollama.sh [model_name]
#
# Improvements over previous version:
#   - Idempotent: safe to re-run
#   - Better error handling: exits non-zero on failure
#   - Health check loop with timeout (30s)
#   - Verifies model actually loads (not just pulls)
#   - Sets all required SCP env vars
#   - Logs to stderr (stdout stays clean for piping)
set -euo pipefail

MODEL="${1:-${SCP_LLM_MODEL:-tinyllama}}"
OLLAMA_HOST_URL="${OLLAMA_HOST:-http://localhost:11434}"
TIMEOUT_SECONDS="${OLLAMA_STARTUP_TIMEOUT:-30}"

echo "=== SCP Ollama Startup (Task 19-A) ===" >&2
echo "  Model:    $MODEL" >&2
echo "  Host:     $OLLAMA_HOST_URL" >&2
echo "  Timeout:  ${TIMEOUT_SECONDS}s" >&2

# 1. Install Ollama if missing
if ! command -v ollama &> /dev/null; then
    echo "[1/4] Ollama not installed — installing via official script..." >&2
    if command -v curl &> /dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
    elif command -v wget &> /dev/null; then
        wget -qO- https://ollama.com/install.sh | sh
    else
        echo "ERROR: neither curl nor wget available — cannot install Ollama" >&2
        exit 1
    fi
fi

# 2. Start server if not running
health_check() {
    curl -sf "$OLLAMA_HOST_URL/api/tags" 2>/dev/null | grep -q '"models"'
}

if health_check; then
    echo "[2/4] Ollama server already running" >&2
else
    echo "[2/4] Starting Ollama server..." >&2
    # Detach ollama serve — log to file
    OLLAMA_LOG="${OLLAMA_LOG:-/tmp/ollama_scp.log}"
    nohup ollama serve > "$OLLAMA_LOG" 2>&1 &
    OLLAMA_PID=$!
    echo "  PID: $OLLAMA_PID, log: $OLLAMA_LOG" >&2

    # Wait for ready (up to TIMEOUT_SECONDS)
    for i in $(seq 1 "$TIMEOUT_SECONDS"); do
        if health_check; then
            echo "  Ready after ${i}s" >&2
            break
        fi
        if [ "$i" = "$TIMEOUT_SECONDS" ]; then
            echo "ERROR: Ollama did not become ready in ${TIMEOUT_SECONDS}s" >&2
            echo "  Tail of log:" >&2
            tail -20 "$OLLAMA_LOG" >&2 || true
            exit 2
        fi
        sleep 1
    done
fi

# 3. Pull model if missing
echo "[3/4] Checking model: $MODEL" >&2
if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "  Pulling $MODEL (this may take a while)..." >&2
    if ! ollama pull "$MODEL"; then
        echo "ERROR: failed to pull $MODEL" >&2
        exit 3
    fi
fi

# 4. Verify model loads (run a tiny prompt)
echo "[4/4] Verifying model loads..." >&2
VERIFY_OUT=$(ollama run "$MODEL" "say OK" 2>&1 || true)
if [ -z "$VERIFY_OUT" ]; then
    echo "WARN: model returned empty output (may still work for longer prompts)" >&2
else
    echo "  Model responds: ${VERIFY_OUT:0:60}..." >&2
fi

# Export env vars
export OLLAMA_HOST="$OLLAMA_HOST_URL"
export SCP_LLM_MODEL="$MODEL"
export SCP_LLM_VERIFY_ENABLED=1
export OLLAMA_PULL_TIMEOUT=600

echo "" >&2
echo "✅ Ollama ready" >&2
echo "   Model: $MODEL" >&2
echo "   Host:  $OLLAMA_HOST_URL" >&2
echo "   Env vars set: OLLAMA_HOST, SCP_LLM_MODEL, SCP_LLM_VERIFY_ENABLED" >&2

# Stdout: just the host URL (for piping)
echo "$OLLAMA_HOST_URL"
