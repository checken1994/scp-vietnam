#!/bin/bash
# Run SCP integration tests: start server + run tests + cleanup.
set -u  # don't use -e — we want to kill server on any exit

PORT=8765
LOG=/home/z/my-project/scp_v105_fix/run_tests.log
SERVER_LOG=/home/z/my-project/scp_v105_fix/run_server.log
PIDFILE=/home/z/my-project/scp_v105_fix/run_server.pid

# 1. Kill any leftover server
pkill -9 -f "uvicorn scp.api_server" 2>/dev/null
pkill -9 -f "port ${PORT}" 2>/dev/null
sleep 2

# 2. Start server (detached, log to file)
cd /home/z/my-project/scp_v105_fix
SCP_SKIP_STARTUP_GATE=1 setsid python3 -m uvicorn scp.api_server:app \
    --host 127.0.0.1 --port ${PORT} --log-level warning \
    > ${SERVER_LOG} 2>&1 < /dev/null &
SERVER_PID=$!
echo $SERVER_PID > ${PIDFILE}
echo "[$(date +%T)] Server started PID=$SERVER_PID on port $PORT"

# 3. Poll health endpoint (up to 120s)
HEALTH_OK=0
for i in $(seq 1 40); do
    sleep 3
    health=$(curl -sS -m 3 http://127.0.0.1:${PORT}/health 2>/dev/null)
    if [ -n "$health" ]; then
        echo "[$(date +%T)] Server UP after $((i*3))s — health: $(echo $health | head -c 100)"
        HEALTH_OK=1
        break
    fi
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo "[$(date +%T)] Server died during boot — see ${SERVER_LOG}"
        tail -30 ${SERVER_LOG}
        exit 1
    fi
done

if [ $HEALTH_OK -ne 1 ]; then
    echo "[$(date +%T)] Server failed to come up in 120s — see ${SERVER_LOG}"
    tail -30 ${SERVER_LOG}
    pkill -9 -f "port ${PORT}" 2>/dev/null
    exit 1
fi

# 4. Run integration tests
echo "[$(date +%T)] Running integration tests..."
SCP_URL=http://127.0.0.1:${PORT} python3 tests/test_ask_integration.py 2>&1
TEST_RC=$?
echo "[$(date +%T)] Integration tests exit code: $TEST_RC"

# 5. Cleanup
echo "[$(date +%T)] Killing server..."
kill -9 $SERVER_PID 2>/dev/null
pkill -9 -f "port ${PORT}" 2>/dev/null
pkill -9 -f "uvicorn scp.api_server" 2>/dev/null

# 6. Save test results
echo "[$(date +%T)] Test results saved to ask_integration_results.json"

exit $TEST_RC
