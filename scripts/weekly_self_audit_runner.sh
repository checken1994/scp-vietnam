#!/bin/bash
# [V104.38] Systemd timer compatible weekly self-audit runner.
# TẠI SAO: crontab not available in all environments. This script works with
# both crontab AND systemd timers.
#
# Install (systemd):
#   cp scripts/weekly-self-audit.service /etc/systemd/system/
#   cp scripts/weekly-self-audit.timer /etc/systemd/system/
#   systemctl enable --now weekly-self-audit.timer
#
# Install (crontab):
#   crontab -e
#   0 2 * * 0 /home/z/my-project/download/scp_v97/scripts/weekly_self_audit_runner.sh
#
# Install (simple loop — for containers without systemd/cron):
#   nohup python scripts/weekly_loop.py &

set -e

SCP_ROOT="/home/z/my-project/download/scp_v97"
LOG_DIR="$SCP_ROOT/data"
LOG_FILE="$LOG_DIR/weekly_audit_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

echo "=== Weekly Self-Audit-Fix Loop ===" | tee "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "Host: $(hostname)" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Run self-audit-fix loop with max 20 fixes per run
cd "$SCP_ROOT"
python /home/z/my-project/scripts/self_audit_fix_loop.py --max-fixes 20 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "=== Completed: $(date) ===" | tee -a "$LOG_FILE"

# Keep only last 10 weekly logs
cd "$LOG_DIR"
ls -t weekly_audit_*.log 2>/dev/null | tail -n +11 | xargs -r rm

echo "Log saved to: $LOG_FILE"
