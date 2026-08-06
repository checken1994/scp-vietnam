# Continuous Usage — SCP Audit Infrastructure

> [G5-FIX P0-02 + P0-10] Previously FABRICATED. Now real.

## 3 layers of audit protection

### Layer 1: Every commit (< 30s)
- **File:** `.pre-commit-config.yaml`
- **Tools:** Ruff + Bandit (HIGH+) + detect-secrets + flake8-bugbear + Dlint
- **Custom:** Protected-paths check (G2-FIX PERM-03)
- **Install:** `pre-commit install`

### Layer 2: Every PR (~5 min)
- **File:** `.github/workflows/audit-pr.yml`
- **Tools:** Ruff + Bandit + Vulture + flake8 + detect-secrets + pip-audit + Pyright
- **Tests:** External audit + DNA golden (no silent skip)
- **Custom:** Protected-paths verification (blocks PR that modifies permission source)

### Layer 3: Nightly 2AM (~30 min)
- **File:** `nightly-audit.sh`
- **Tools:** Full Ruff + Bandit + Vulture + TruffleHog + OSV-Scanner + Pyre
- **Tests:** ALL tests (no skip, no || true)
- **Custom:** Regression detection (compare with baseline 121 findings)
- **Alert:** Slack webhook if ruff errors > 200

## Monthly: 5-subagent independent audit
- **Framework:** `/home/z/my-project/scp-trust-root/independent-audit/5-subagent-framework.md`
- **Schedule:** 1st of month, 2AM Asia/Saigon
- **Output:** `SCP_INTEGRITY_REPORT.md` (auto-generated)
- **Subagents:** file-dup, logic-dup, doc-vs-code, test-integrity, permission

## Quarterly: Human review ("audit the auditor")
- Review 5-subagent results
- Test planted finding → subagent must catch
- Verify subagent not compromised

## Setup
```bash
# Install pre-commit
pip install pre-commit
pre-commit install

# Verify CI
gh workflow list  # should show "SCP Audit (PR)"

# Setup nightly cron
crontab -e
# Add: 0 2 * * * /path/to/scp-vietnam/continuous_usage/nightly-audit.sh
```
