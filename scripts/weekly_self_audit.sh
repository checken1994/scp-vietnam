#!/bin/bash
# [V104.37] Weekly self-audit-fix cron script.
# TẠI SAO: Run self_audit_fix_loop.py weekly to catch new silent failures
# before they cause production issues.
#
# Install (crontab -e):
#   0 2 * * 0 /home/z/my-project/download/scp_v97/scripts/weekly_self_audit.sh
#
# This runs every Sunday at 2 AM.

set -e

LOG_FILE="/home/z/my-project/download/scp_v97/data/weekly_audit_$(date +%Y%m%d_%H%M%S).log"
mkdir -p /home/z/my-project/download/scp_v97/data

echo "=== Weekly Self-Audit-Fix Loop ===" | tee "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Run self-audit-fix loop
cd /home/z/my-project/download/scp_v97
python /home/z/my-project/scripts/self_audit_fix_loop.py --max-fixes 20 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "=== Completed: $(date) ===" | tee -a "$LOG_FILE"

# Keep only last 10 weekly logs
cd /home/z/my-project/download/scp_v97/data
ls -t weekly_audit_*.log 2>/dev/null | tail -n +11 | xargs -r rm

echo "Log saved to: $LOG_FILE"
