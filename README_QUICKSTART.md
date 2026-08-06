# SCP Vietnam v5 — Quick Start

## What's New
- Predictive Defense (Perimetr-style): AttackPredictor + EscalationManager + Playbooks
- DDoS Survival: CircuitBreaker + RequestShedder
- CISA KEV feed: 1656 known exploited vulnerabilities
- Model: deepseek-v4-flash-0731 (18x cheaper)
- SCP self-built 5 new modules via LLM AutoFix

## Run
```bash
cd scp-vietnam
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install python-dotenv
export SCP_DEV_MODE=1 SCP_SKIP_STARTUP_GATE=1 SCP_ENABLE_CLOSED_LOOP=1
python start_api.py
```
Open: http://localhost:8000/dashboard

## Test DDoS Survival
```bash
python /home/z/my-project/scripts/scp_ddos_test_100.py
```

See FIXES_CHANGELOG.md for all 27 bug fixes + 5 new modules.
