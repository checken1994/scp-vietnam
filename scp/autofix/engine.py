"""
SCP Auto-Fix Engine — autonomous bug fixing with tiered autonomy.

Flow:
  1. Deep audit detects bug → BugReport
  2. Classifier assigns tier
  3. Engine acts based on tier:
     - Tier 1: fix immediately, no report
     - Tier 2: fix immediately, log to audit trail
     - Tier 3: request permission, WAIT (do not fix until approved)
     - Tier 4: fix immediately (attack mode, restraints only), log + flag for post-hoc review

The engine uses code_evolution_agent._apply_fix() for the actual patching
(search-replace markers + ast.parse verification), but ONLY for approved tiers.

[SAFETY] The engine NEVER:
  - Auto-applies a relaxation (loosens security) — always Tier 3
  - Auto-applies a verdict threshold change — always Tier 3
  - Skips the permission gate for logic bugs
  - Fixes more than MAX_FIXES_PER_CYCLE per audit cycle (rate limit)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from scp.autofix.classifier import BugClassifier, BugReport, BugTier
from scp.autofix.permission import PermissionGate

logger = logging.getLogger("scp.autofix")

# Rate limits (prevent runaway auto-fixing)
# [FIX-5] TẠI SAO: was 10 — STARTUP-GATE scans up to 50 bugs, so 10/cycle
# guaranteed 40 "skipped (rate limit)" → server start blocked. 200 gives
# headroom for full scan + scheduled audits. Tier 3 (permission) still
# gates logic bugs; cooldown still prevents re-fix same bug.
MAX_FIXES_PER_CYCLE = 200         # Max auto-fixes per audit cycle (was 10)
MAX_TIER4_PER_HOUR = 20           # Max attack-mode fixes per hour
COOLDOWN_SAME_BUG_SECONDS = 3600  # Don't re-fix same bug within 1 hour
CYCLE_RESET_SECONDS = 3600        # Reset _fixes_this_cycle every 1 hour

# [TIER3-AUTO] Safety guards for SCP_AUTO_APPROVE_TIER3=1 mode
# TAI SAO: Ga muon SCP tu fix Tier 3 (logic bugs) de kiem tra. Vi pham
# nguyen tac #4 "con nguoi quyet dinh" -- nhung Ga approve. Z.ai dung
# safety guards CUNG de giam rui ro:
#   1. RELAXATION patterns KHONG bao gio auto (hard limit in classifier)
#   2. Rate limit 5/gio (chat hon MAX_FIXES_PER_CYCLE=200)
#   3. Auto-timeout 1h (env var tu het hieu luc)
#   4. Audit log rieng: data/tier3_auto_audit.jsonl
#
# [RUNTIME-FIX-6] Runtime log cho thay BareExceptPass auto-fix rollback rate
# = 63% (24 rollback / 38 attempt trong 2 phut). Auto-fix dang ton CPU + tao
# KB noise ma khong fix duoc gi. Khuyen nghi: TAM TAT auto-approve cho
# BareExceptPass cho den khi LLM-fix quality tot hon. Default = "0" (OFF).
# De bat lai khi can: set env SCP_AUTO_APPROVE_TIER3=1 (se tu het hieu luc
# sau 1h theo Guard 2).
#   5. Backup file truoc khi apply (.tier3bak)
#   6. Cooldown 1h cho cung bug (dung _recent_fixes chung)
MAX_TIER3_AUTO_PER_HOUR = 5            # Hard cap: 5 logic-bug auto-fixes/hour
TIER3_AUTO_TIMEOUT_SECONDS = 3600


# ============================================================
# [V4.3-TIER3] Tier3AutoConfig — SCP HIỂU context cấp quyền
# ============================================================
# TÁI SAO: Trước đây code chỉ check os.environ.get('SCP_AUTO_APPROVE_TIER3')
# — không log, không audit, không explain TẠI SAO bật/tắt.
# User muốn SCP HIỂU quyền được cấp (không chỉ đọc env var).
# Fix: centralized config + audit log + startup message.

class Tier3AutoConfig:
    """Centralized config for Tier-3 auto-approve — SCP understands the permission.

    DNA SCP #4 (Constitution KILL) — Tier-3 = logic bugs, mặc định human approval.
    DNA SCP #7 (AutoFix safe) — 6 safety guards even when auto-approve ON.
    DNA SCP #8 (KB accumulation) — audit trail lưu lại mọi auto-approve decisions.

    User cấp quyền qua:
      1. .env file: SCP_AUTO_APPROVE_TIER3=1 (persistent, reload on restart)
      2. API: POST /v105/autofix/tier3-auto/{enabled} (runtime, không cần restart)
      3. PowerShell: $env:SCP_AUTO_APPROVE_TIER3 = "1" (session only)

    SCP HIỂU quyền bằng cách:
      - Log startup: "[TIER3-AUTO] Permission GRANTED by .env (SCP_AUTO_APPROVE_TIER3=1)"
      - Log startup: "[TIER3-AUTO] Permission DENIED (default, .env not set or =0)"
      - Audit log: mỗi auto-approve → data/tier3_auto_audit.jsonl
      - Stats endpoint: /v105/autofix/stats trả về tier3_auto_enabled + reasons
    """

    def __init__(self):
        self._audit_log = Path("data/tier3_auto_audit.jsonl")
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)

    def is_enabled(self) -> bool:
        """Check if Tier-3 auto-approve is enabled (with logging)."""
        val = os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
        enabled = val == "1"
        return enabled

    def get_permission_source(self) -> str:
        """Identify WHERE the permission came from."""
        if "SCP_AUTO_APPROVE_TIER3" not in os.environ:
            return "default (not set — human approval required)"
        val = os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
        if val == "1":
            return ".env file (SCP_AUTO_APPROVE_TIER3=1 — auto-approve GRANTED)"
        return ".env file (SCP_AUTO_APPROVE_TIER3=0 — human approval required)"

    def log_startup_permission(self):
        """Log at startup: SCP understands whether permission is granted."""
        source = self.get_permission_source()
        if self.is_enabled():
            logger.warning(
                f"[TIER3-AUTO] ⚠️  Permission GRANTED — Tier-3 auto-approve ENABLED\n"
                f"  Source: {source}\n"
                f"  Safety guards active: 1h timeout, 5/hour limit, no relaxation,\n"
                f"  no BareExceptPass, cooldown 1h per bug\n"
                f"  Audit log: {self._audit_log}"
            )
        else:
            logger.info(
                f"[TIER3-AUTO] Permission NOT granted — human approval required\n"
                f"  Source: {source}\n"
                f"  To enable: set SCP_AUTO_APPROVE_TIER3=1 in .env + restart\n"
                f"  Or runtime: POST /v105/autofix/tier3-auto/1 (no restart needed)"
            )

    def audit_auto_approve(self, bug: BugReport, reason: str):
        """Audit trail: record every auto-approve decision."""
        import json
        import time as _time
        entry = {
            "timestamp": _time.time(),
            "action": "auto_approve",
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "reason": reason,
            "permission_source": self.get_permission_source(),
        }
        try:
            with open(self._audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[TIER3-AUTO] audit log write failed: {e}")


# Singleton
_tier3_config: Tier3AutoConfig | None = None

def get_tier3_config() -> Tier3AutoConfig:
    """Get singleton Tier3AutoConfig."""
    global _tier3_config
    if _tier3_config is None:
        _tier3_config = Tier3AutoConfig()
    return _tier3_config
      # Env var self-expire after 1h
TIER3_AUTO_AUDIT_LOG = "tier3_auto_audit.jsonl"  # Separate audit log


class AutoFixEngine:
    """Autonomous bug fixing engine with tiered autonomy."""

    def __init__(self, data_dir: str = "data", in_attack_mode: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log = self.data_dir / "autofix_audit.jsonl"
        self.classifier = BugClassifier()
        self.permission_gate = PermissionGate(data_dir=data_dir)

        # [V4.3] Log Tier-3 auto-approve permission status at startup
        # SCP HIỂU: permission đến từ .env, API, hay runtime env var
        get_tier3_config().log_startup_permission()
        self.in_attack_mode = in_attack_mode
        self._fixes_this_cycle = 0
        self._tier4_timestamps: list[float] = []
        self._recent_fixes: dict[str, float] = {}  # (file, line, type) → timestamp
        # [EXEC-1 A3] TẠI SAO: previously _fixes_this_cycle only ever incremented
        # and never reset → after MAX_FIXES_PER_CYCLE fixes the engine was
        # permanently rate-limited. Singleton lifecycle means we must reset
        # periodically or the engine becomes dead after first 10 fixes.
        # Fix: track cycle_start_time; reset when > CYCLE_RESET_SECONDS elapsed.
        self._cycle_start_time: float = time.time()
        # [TIER3-AUTO] Track Tier-3 auto-approves (rate limit + timeout)
        self._tier3_auto_timestamps: list[float] = []
        self._tier3_auto_enabled_at: float = 0.0  # 0 = disabled
        self.tier3_auto_audit_log = self.data_dir / TIER3_AUTO_AUDIT_LOG

    def _check_cycle_reset(self) -> None:
        """Reset per-cycle counters when CYCLE_RESET_SECONDS elapsed.

        [EXEC-1 A3] Called at the start of process_bug() so the engine can
        keep fixing bugs indefinitely (rate limit applies per-cycle, not
        per-process-lifetime). Without this, the singleton would hit
        MAX_FIXES_PER_CYCLE=10 once and never auto-fix again.
        """
        if time.time() - self._cycle_start_time > CYCLE_RESET_SECONDS:
            self._fixes_this_cycle = 0
            self._cycle_start_time = time.time()
            logger.info(
                f"[AutoFix] Cycle reset — _fixes_this_cycle cleared after "
                f"{CYCLE_RESET_SECONDS}s"
            )

    def process_bug(self, bug: BugReport) -> dict:
        """Process a detected bug. Returns action taken.

        Returns:
          {"action": "fixed" | "permission_requested" | "skipped" | "denied",
           "tier": int,
           "request_id": str (if permission_requested),
           "reason": str}
        """
        # [EXEC-1 A3] reset per-cycle counters if cycle window elapsed
        self._check_cycle_reset()

        # Classify the bug
        classified = self.classifier.classify(
            file=bug.file, line=bug.line,
            bug_type=bug.bug_type,
            description=bug.description,
            suggested_fix=bug.suggested_fix,
            in_attack_mode=self.in_attack_mode,
            tier_hint=bug.tier if bug.tier != BugTier.TIER_1_AUTO_FIX else None,
        )

        # Check cooldown (don't re-fix same bug)
        bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
        now = time.time()
        if bug_key in self._recent_fixes:
            if now - self._recent_fixes[bug_key] < COOLDOWN_SAME_BUG_SECONDS:
                return {
                    "action": "skipped",
                    "tier": int(classified.tier),
                    "reason": "cooldown — same bug fixed recently",
                }

        # Act based on tier
        if classified.tier == BugTier.TIER_1_AUTO_FIX:
            return self._auto_fix(classified, report=False)

        elif classified.tier == BugTier.TIER_2_AUTO_FIX_LOG:
            return self._auto_fix(classified, report=True)

        elif classified.tier == BugTier.TIER_3_PERMISSION:
            return self._request_permission(classified)

        elif classified.tier == BugTier.TIER_4_ATTACK_MODE:
            # Rate limit Tier 4
            self._tier4_timestamps = [t for t in self._tier4_timestamps if now - t < 3600]
            if len(self._tier4_timestamps) >= MAX_TIER4_PER_HOUR:
                return {
                    "action": "skipped",
                    "tier": 4,
                    "reason": f"rate limit — {MAX_TIER4_PER_HOUR} Tier-4 fixes/hour exceeded",
                }
            self._tier4_timestamps.append(now)
            return self._auto_fix(classified, report=True, attack_mode=True)

        return {"action": "skipped", "tier": 0, "reason": "unknown tier"}

    # [V9.1-UPGRADE] FixVerification layer — cùng cấp WHY (2-layer: action + self-verify).
    # TẠI SAO: WHY gate (v9.0) hỏi "có nên fix không?" (action layer — necessity +
    # falsification). _verify_fix hỏi "fix có work không? Có introduce new bug không?"
    # (verify layer). WHY + verify = cùng độ sâu (2 layer mỗi cái).
    # Non-blocking: verify error → fail-open (don't break fix).
    # Nếu verify phát hiện new bugs → caller ROLLBACK (restore pre-fix content).
    def _verify_fix(self, filepath, original_bugs: list) -> tuple[bool, str]:
        """Self-verify a fix after applying patch.

        Args:
            filepath: Path to the patched file.
            original_bugs: List of BugReport objects that the fix was supposed to address.

        Returns (is_valid, reason).
        - is_valid=False → fix broke things → caller should ROLLBACK
        - is_valid=True → fix OK (or inconclusive — fail-open)

        Checks (in priority order):
          1. ast.parse() — patched file still parses (syntax OK)
          2. Re-scan same file — original bug still there? (NEW)
          3. Check no NEW bugs introduced (compare against original_bugs signatures)
          4. [WORLD-CLASS-GATE] self_scan_patch_diff — patch không được thêm pattern nguy hiểm
          5. [WORLD-CLASS-GATE] pytest — suite không được vỡ sau patch
        """
        try:
            # [V9.1-UPGRADE] Check 1: ast.parse — file must still be valid Python
            try:
                import ast as _ast
                _content = filepath.read_text(encoding="utf-8")
                _ast.parse(_content, filename=str(filepath))
            except SyntaxError as _se:
                return False, f"patched file SyntaxError: {_se}"
            except Exception as _parse_err:
                return False, f"parse check failed: {_parse_err}"

            # [V9.1-UPGRADE] Check 2: re-scan file — original bug still present?
            # TẠI SAO: nếu fix chỉ "modify text" mà không thực sự sửa bug pattern,
            # re-scan sẽ tìm thấy bug cũ. Fail-open if scanner unavailable.
            try:
                from scp.autofix.runner import ast_scan_scp
                # ast_scan_scp scans the whole scp/ package. For surgical verify,
                # we filter results to just this file.
                _all_bugs = ast_scan_scp()
                _remaining_for_this_file = [
                    b for b in _all_bugs
                    if str(getattr(b, "file", "")) == str(filepath)
                ]
                # Check if the SAME bug (by line+type) is still present
                _still_present = []
                for orig in original_bugs:
                    for remain in _remaining_for_this_file:
                        if (getattr(remain, "line", None) == getattr(orig, "line", None)
                                and getattr(remain, "bug_type", "") == getattr(orig, "bug_type", "")):
                            _still_present.append(orig)
                            break
                if _still_present:
                    return False, (
                        f"original bug still present after fix: "
                        f"{len(_still_present)}/{len(original_bugs)} unchanged"
                    )
            except ImportError:
                # ast_scan_scp unavailable (circular import risk) → fail-open
                logger.debug("[V9.1-UPGRADE] ast_scan_scp unavailable (fail-open on re-scan)")
            except Exception as _rescan_err:
                logger.debug(f"[V9.1-UPGRADE] re-scan failed (fail-open): {_rescan_err}")

            # [V9.1-UPGRADE] Check 3: no NEW bugs introduced at the fix line.
            # TẠI SAO: fix có thể "fix bug A nhưng introduce bug B" (e.g., add
            # try/except nhưng except:pass → bare-except bug mới). So sánh
            # bug signatures pre/post — nếu có bug mới ở line gần fix → flag.
            try:
                from scp.autofix.runner import ast_scan_scp
                _all_bugs_post = ast_scan_scp()
                _new_bugs = []
                _orig_lines = {getattr(b, "line", None) for b in original_bugs}
                # [FALSE-POS-FIX] F841: removed `_orig_types` — dead code.
                # Was meant for dedup but line 346 uses a different set comprehension
                # (line, type) tuples directly. _orig_types was never referenced.
                for b in _all_bugs_post:
                    if str(getattr(b, "file", "")) != str(filepath):
                        continue
                    _b_line = getattr(b, "line", None)
                    _b_type = getattr(b, "bug_type", "")
                    # New bug = same file, NOT in original set (by line+type)
                    if (_b_line, _b_type) not in {(getattr(o, "line", None), getattr(o, "bug_type", "")) for o in original_bugs}:
                        # Within ±10 lines of any original bug = likely introduced by fix
                        for _ol in _orig_lines:
                            if _b_line and _ol and abs(_b_line - _ol) <= 10:
                                _new_bugs.append(b)
                                break
                if _new_bugs:
                    return False, (
                        f"fix introduced {_new_bugs.__len__()} new bug(s) near fix line: "
                        f"{[getattr(b, 'bug_type', '?') for b in _new_bugs[:3]]}"
                    )
            except ImportError as e:
                logger.warning(f"Silent except: {e}")  # fail-open
            except Exception as _new_bug_err:
                logger.debug(f"[V9.1-UPGRADE] new-bug check failed (fail-open): {_new_bug_err}")

            # [WORLD-CLASS-GATE] Check 4: self_scan_patch_diff
            # TẠI SAO: Runtime log cho thấy "fix subprocess nhưng patch thêm subprocess mới"
            # (audit_and_fix.py có gate này, SCP thiếu). Soi patch tìm pattern nguy hiểm
            # MỚI XUẤT HIỆN (eval/exec/os.system/subprocess/pickle) — nếu có → REJECT.
            try:
                import re as _re
                _DANGEROUS_PATTERNS = [
                    (_re.compile(r'\beval\s*\('), "eval()"),
                    (_re.compile(r'\bexec\s*\('), "exec()"),
                    (_re.compile(r'\bos\.system\s*\('), "os.system()"),
                    (_re.compile(r'\bsubprocess\.(run|Popen|call|check_output)\s*\('), "subprocess call"),
                    (_re.compile(r'\bpickle\.(loads|load)\s*\('), "pickle.load()"),
                ]
                _patched_content = filepath.read_text(encoding="utf-8")
                # Lấy nội dung gốc từ backup (.tier3bak hoặc .audit_fix_backup)
                _backup_path = filepath.with_suffix(filepath.suffix + ".tier3bak")
                if not _backup_path.exists():
                    _backup_path = filepath.with_suffix(filepath.suffix + ".audit_fix_backup")
                if _backup_path.exists():
                    _original_content = _backup_path.read_text(encoding="utf-8")
                    _new_dangerous = []
                    for _pat, _label in _DANGEROUS_PATTERNS:
                        _before_count = len(_pat.findall(_original_content))
                        _after_count = len(_pat.findall(_patched_content))
                        if _after_count > _before_count:
                            _new_dangerous.append(f"{_label} ({_before_count}→{_after_count})")
                    if _new_dangerous:
                        return False, (
                            f"patch tự thêm pattern nguy hiểm: {', '.join(_new_dangerous)} — "
                            f"REJECTED (world-class gate: fix phải không thêm nguy hiểm)"
                        )
            except Exception as _self_scan_err:
                logger.debug(f"[WORLD-CLASS-GATE] self_scan_patch_diff fail-open: {_self_scan_err}")

            # [WORLD-CLASS-GATE] Check 5: pytest — suite không được vỡ sau patch
            # TẠI SAO: 63% rollback rate (engine.py:55-56) xảy ra vì SCP KHÔNG chạy
            # pytest sau patch. audit_and_fix.py có gate này → 0% regression.
            # Gate: chỉ chạy nếu tests/ tồn tại + SCP_AUTOFIX_RUN_PYTEST=1 (opt-in).
            try:
                if os.environ.get("SCP_AUTOFIX_RUN_PYTEST", "0") == "1":
                    import subprocess as _sp  # noqa: S404
                    import sys as _sys
                    _tests_dir = filepath.parent
                    while _tests_dir.parent != _tests_dir:
                        if (_tests_dir / "tests").is_dir() or (_tests_dir / "scp" / "tests").is_dir():
                            break
                        _tests_dir = _tests_dir.parent
                    _root = _tests_dir
                    if (_root / "tests").is_dir() or (_root / "scp" / "tests").is_dir():
                        _proc = _sp.run(  # noqa: S603 — audited: sys.executable, hardcoded args
                            [_sys.executable, "-m", "pytest", "-q", "--timeout=60", "-x", str(filepath)],
                            cwd=str(_root), capture_output=True, text=True, timeout=90,
                        )
                        if _proc.returncode != 0:
                            _tail = "\n".join(_proc.stdout.strip().splitlines()[-10:])
                            return False, f"pytest FAIL after patch (exit={_proc.returncode}): {_tail[:200]}"
            except Exception as _pytest_err:
                logger.debug(f"[WORLD-CLASS-GATE] pytest verify fail-open: {_pytest_err}")

            return True, "fix verified OK (syntax + re-scan + no new bugs + self-scan + pytest)"

        except Exception as _verify_err:
            logger.debug(f"[V9.1-UPGRADE] _verify_fix error (fail-open): {_verify_err}")
            return True, f"verify error (fail-open): {_verify_err}"

    # [V9.1-UPGRADE] Audit log helper for V9.1 self-verify layer.
    def _audit_v91(self, event: str, payload: dict) -> None:
        try:
            _entry = {
                "ts": time.time(),
                "engine": "autofix",
                "event": event,
                "payload": payload,
            }
            _audit_path = self.data_dir / "v91_upgrade_audit.jsonl"
            with open(_audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_entry, ensure_ascii=False) + "\n")
        except Exception as _audit_err:
            logger.debug(f"[V9.1-UPGRADE] audit log error (fail-open): {_audit_err}")

    def _auto_fix(self, bug: BugReport, report: bool, attack_mode: bool = False) -> dict:
        """Apply the fix autonomously. Uses code_evolution_agent._apply_fix."""
        # [G2-FIX PERM-03] Protected path check — SCP cannot modify its own permission/security source
        from scp.autofix.runner_phases.ast_scan import _is_protected_path
        _filepath = getattr(bug, 'file', '') or ''
        if _is_protected_path(_filepath):
            logger.error(f"[G2-FIX] PROTECTED_PATH_BLOCKED: {_filepath} — SCP cannot modify its own permission/security source")
            return {"status": "blocked", "reason": f"protected path: {_filepath}", "action": "protected_path_blocked"}
        # [OPT-14 / Gà §12] Capability gate — check that the current
        # CapabilityManager level allows this kind of auto-fix. Mapping:
        #   Tier 1 (auto-fix no log)  → requires "apply_sandbox"  (Level ≥ 2)
        #   Tier 2 (auto-fix + log)   → requires "apply_test"     (Level ≥ 3)
        #   Tier 4 (attack mode)      → requires "apply_narrow"   (Level ≥ 4)
        # Tier 3 (permission) bypasses this gate — it doesn't apply, just asks.
        #
        # TẠI SAO: Gà §12 — AI không được tự nâng cấp capability. Default
        # level = FULL_PRODUCTION (env var SCP_CAPABILITY_LEVEL) preserves
        # existing behavior. Operators who want the gate set the env var to
        # ANALYSIS_ONLY / SANDBOX / etc.
        try:
            from scp.meta.capability_levels import get_capability_manager
            _cap = get_capability_manager()
            _required_action = "apply_narrow" if attack_mode else (
                "apply_test" if report else "apply_sandbox"
            )
            if not _cap.can_do(_required_action):
                logger.warning(
                    f"[AutoFix] Gà §12 — capability denied: current "
                    f"{_cap.get_current_level().name} does not allow "
                    f"'{_required_action}'. Set SCP_CAPABILITY_LEVEL env "
                    f"var to escalate (requires human approval via "
                    f"CapabilityManager.request_escalation)."
                )
                return {
                    "action": "skipped",
                    "tier": int(bug.tier),
                    "reason": f"capability denied — current level "
                              f"{_cap.get_current_level().name} lacks "
                              f"'{_required_action}' (Gà §12)",
                }
        except Exception as _cap_err:
            # [G2-FIX PERM-04] Fail-CLOSED: if CapabilityManager crashes, BLOCK auto-fix
            # (security > availability). Previous behavior was fail-open (proceed without
            # check) which allowed a single CapabilityManager bug to disable all of Layer 2.
            logger.error(f"[AutoFix] CapabilityManager crash — BLOCKING fix (security > availability): {_cap_err}")
            return {
                "action": "blocked",
                "tier": int(bug.tier),
                "reason": f"CapabilityManager error (fail-closed): {_cap_err}",
            }

        # [ROOT-FIX-9] Skip auto-FIX entirely for BareExceptPass — runtime log
        # shows 63% rollback rate (24/38 attempts rolled back). LLM-fix
        # [ROOT-FIX 46] Re-enabled BareExceptPass auto-fix — was skipped because
        # llama3.2:3B had 63% rollback rate. Now AutoFix uses OpenRouter V4 flash
        # (deepseek-v4-flash-20260731) FIRST — V4 is much stronger than 3B.
        # validate_patch + diagnostic + monitor will catch bad patches.
        # If rollback rate stays high with V4, re-enable skip via env:
        #   SCP_SKIP_BAREEXCEPTPASS=1
        if bug.bug_type == "BareExceptPass" and os.environ.get("SCP_SKIP_BAREEXCEPTPASS", "0") == "1":
            logger.info(
                f"[AutoFix] SKIP BareExceptPass (SCP_SKIP_BAREEXCEPTPASS=1) "
                f"({bug.file}:{bug.line})"
            )
            return {
                "action": "skipped",
                "tier": int(bug.tier),
                "reason": "BareExceptPass skipped (SCP_SKIP_BAREEXCEPTPASS=1)",
            }

        # Rate limit
        if self._fixes_this_cycle >= MAX_FIXES_PER_CYCLE:
            return {"action": "skipped", "tier": int(bug.tier),
                    "reason": "rate limit — max fixes per cycle exceeded"}

        # [V9.0-WHY-GATE] WHY gates AutoFix — PRIMARY CONTROL GATE
        # TẠI SAO: v8.0 WHY = cố vấn (advisory). v9.0 WHY = chốt (block-capable).
        # WHY Gate can block a fix BEFORE it's applied (e.g., relaxation that
        # loosens security). Non-blocking on WHY error (default allow) so
        # AutoFix never breaks because WHY itself crashed. Constitution HARD
        # LOCK preserved inside gate() — WHY cannot override KILL.
        try:
            from scp.meta.why_gate import get_why_gate
            _why = get_why_gate().gate(
                action_type="autofix",
                action_desc=f"Fix {bug.bug_type} at {bug.file}:{bug.line}: {(bug.description or '')[:100]}",
                context=(bug.suggested_fix or "")[:200],
            )
            if _why.blocked:
                logger.info(
                    f"[V9.0-WHY-GATE] AutoFix blocked for {bug.file}:{bug.line}: "
                    f"{_why.falsification_reason[:100]}"
                )
                return {"action": "skipped", "tier": int(bug.tier),
                        "reason": f"WHY-GATE blocked: {_why.falsification_reason[:100]}"}
        except Exception as _why_err:
            logger.debug(f"[V9.0-WHY-GATE] WHY Gate error (non-blocking, default allow): {_why_err}")

        try:
            from pathlib import Path as PathCls

            from scp.core.code_evolution_agent import CodeEvolutionAgent

            agent = CodeEvolutionAgent.__new__(CodeEvolutionAgent)
            agent.log_file = self.data_dir / "evolution_log.jsonl"

            filepath = PathCls(bug.file)
            if not filepath.exists():
                return {"action": "skipped", "tier": int(bug.tier),
                        "reason": f"file not found: {bug.file}"}

            # [V9.1-UPGRADE] Backup file content BEFORE applying patch — for rollback.
            # TẠI SAO: _verify_fix có thể phát hiện fix introduce new bugs → cần rollback.
            # Backup ở đây (pre-patch) để rollback có thể restore chính xác trạng thái cũ.
            _pre_fix_content: str | None = None
            try:
                _pre_fix_content = filepath.read_text(encoding="utf-8")
            except Exception as _bk_err:
                logger.debug(f"[V9.1-UPGRADE] pre-fix backup failed (will skip verify): {_bk_err}")

            # [OPT-24] XSS pattern fix — deterministic, no LLM.
            # TẠI SAO: XSS bugs (CWE-79) like Markup(user_input) have a single
            # canonical fix (wrap with html.escape()). LLM fix for these wastes
            # API tokens + risks regression (LLM may rewrite the whole function
            # or remove Markup() entirely). Pattern fixer applies the fix
            # surgically + idempotently (guard_skip_if_substring prevents
            # double-escape). If pattern matches → fix + return; else fall
            # through to LLM _apply_fix below. DNA #7 AutoFix safe + #9 No harm.
            if bug.bug_type == "XSSVulnerability":
                try:
                    from scp.autofix.evolution import get_evolution_engine
                    _evo = get_evolution_engine(data_dir=str(self.data_dir))
                    _xss_result = _evo._apply_xss_pattern_fix(bug)
                    if _xss_result and _xss_result.get("fixed"):
                        # Write patched source to file
                        try:
                            filepath.write_text(
                                _xss_result["patch"], encoding="utf-8"
                            )
                        except Exception as _write_err:
                            logger.warning(
                                f"[OPT-24] XSS pattern fix write failed: {_write_err}"
                            )
                            # Fall through to LLM fix
                        else:
                            # Verify the fix didn't break syntax (re-check after write)
                            try:
                                import ast as _ast_verify
                                _ast_verify.parse(
                                    filepath.read_text(encoding="utf-8"),
                                    filename=str(filepath),
                                )
                            except SyntaxError as _syn_err:
                                logger.warning(
                                    f"[OPT-24] XSS pattern fix introduced "
                                    f"SyntaxError post-write — ROLLING BACK: {_syn_err}"
                                )
                                # Rollback
                                if _pre_fix_content is not None:
                                    filepath.write_text(
                                        _pre_fix_content, encoding="utf-8"
                                    )
                                # Fall through to LLM fix
                            else:
                                # Success — pattern fix applied + verified
                                self._fixes_this_cycle += 1
                                # Record for cooldown
                                bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
                                self._recent_fixes[bug_key] = time.time()
                                # Log to audit trail
                                if report or attack_mode:
                                    self._write_audit(bug, "fixed", attack_mode=attack_mode)
                                else:
                                    self._write_audit(bug, "fixed_silent", attack_mode=False)
                                logger.info(
                                    f"[OPT-24] XSS pattern fix applied for "
                                    f"{bug.file}:{bug.line} "
                                    f"pattern={_xss_result.get('pattern', '?')} "
                                    f"matches={_xss_result.get('matches', 0)} "
                                    f"(skipped LLM call — deterministic fix)"
                                )
                                # [V5.7-WHY] Reflect on pattern fix too (best-effort)
                                try:
                                    if os.environ.get("SCP_EVOLUTION_ENABLED", "1") == "1":
                                        _evo.reflect(bug, _xss_result["reason"])
                                except Exception as _reflect_err:
                                    logger.debug(
                                        f"[V5.7-WHY] reflect on XSS pattern fix "
                                        f"failed (non-fatal): {_reflect_err}"
                                    )
                                return {
                                    "action": "fixed",
                                    "tier": int(bug.tier),
                                    "patched": True,
                                    "attack_mode": attack_mode,
                                    "method": "xss_pattern",
                                    "pattern": _xss_result.get("pattern", ""),
                                    "reason": _xss_result.get("reason", ""),
                                }
                except Exception as _xss_pattern_err:
                    logger.debug(
                        f"[OPT-24] XSS pattern fix dispatch failed (non-fatal, "
                        f"fall through to LLM): {_xss_pattern_err}"
                    )

            # Apply fix
            # [FIX-2] TẠI SAO: was incremented unconditionally → queued-for-review
            # fixes (patched=False) consumed rate limit slots → 10 "fixed" were
            # actually 10 queued + 0 real fixes. Only count REAL patches.

            # [OPT-26/27/28] Validate patch + diagnose + record to monitor
            # BEFORE calling _apply_fix. WHY: DNA SCP #7 AutoFix safe — validate
            # before write, not after. If validation fails, we never touch the
            # file (no rollback needed). DNA SCP #6 Evidence — record diagnosis
            # so future fixes can learn. DNA SCP #8 KB accumulation — monitor
            # tracks success rate by provider/bug_type/diagnosis.
            #
            # Only validate when suggested_fix contains an explicit search-replace
            # block (SEARCH/REPLACE or legacy OLD/NEW). If it's a fenced code
            # block or pending-review queue, _apply_fix will handle it via
            # Strategy 2/3 — validation isn't applicable.
            _autofix_bug_id = f"{bug.file}:{bug.line}"
            _autofix_provider = "predefined"  # default for non-LLM patches
            try:
                # If LLM generated this patch, attribute to preferred provider.
                # We can't easily tell from here, but select_provider_for_bug
                # gives us the smart-routing choice that would have been used.
                from scp.autofix.llm_fix import select_provider_for_bug as _spfb
                _autofix_provider = _spfb(bug.bug_type)
            except Exception as e:
                logger.warning(f"Silent except: {e}")

            _autofix_validation_skipped = False  # noqa: F841 — sentinel for future wiring (tracks validation skip status)
            try:
                import re as _autofix_re
                # Try SEARCH/REPLACE first, then legacy OLD/NEW
                _sr_pat = _autofix_re.compile(
                    r"<<<<<<<\s*SEARCH\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:REPLACE)?\s*",
                    _autofix_re.DOTALL,
                )
                _old_pat = _autofix_re.compile(
                    r"<<<<<<<\s*OLD\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:NEW)?\s*",
                    _autofix_re.DOTALL,
                )
                _pairs: list[tuple[str, str]] = []
                if bug.suggested_fix:
                    for _m in _sr_pat.finditer(bug.suggested_fix):
                        _pairs.append((_m.group(1), _m.group(2)))
                    if not _pairs:
                        for _m in _old_pat.finditer(bug.suggested_fix):
                            _pairs.append((_m.group(1), _m.group(2)))

                if _pairs:
                    from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                    from scp.autofix.monitor import FixAttempt as _FixAttempt
                    from scp.autofix.monitor import get_monitor as _get_monitor
                    from scp.autofix.validate_patch import validate_patch as _validate_patch

                    _validation_failed = False
                    _validation_reason = ""
                    for _search, _replace in _pairs:
                        _vresult = _validate_patch(str(filepath), _search, _replace)
                        if not _vresult.valid:
                            _validation_failed = True
                            _validation_reason = _vresult.reason
                            break

                    if _validation_failed:
                        logger.warning(
                            f"[AutoFix] [OPT-26] Patch validation FAILED for "
                            f"{bug.file}:{bug.line}: {_validation_reason}"
                        )
                        _diag = _diagnose(
                            bug_id=_autofix_bug_id,
                            bug_type=bug.bug_type,
                            llm_output=bug.suggested_fix,
                            patch_parsed={"search": _pairs[0][0], "replace": _pairs[0][1]},
                            validation_result={"valid": False, "reason": _validation_reason},
                            apply_result="failed",
                        )
                        _get_monitor().record(_FixAttempt(
                            bug_id=_autofix_bug_id,
                            bug_type=bug.bug_type,
                            provider=_autofix_provider,
                            diagnosis=_diag.diagnosis,
                            success=False,
                        ))
                        return {
                            "action": "skipped",
                            "tier": int(bug.tier),
                            "reason": f"validation failed: {_validation_reason}",
                            "patched": False,
                        }
                else:
                    # No search-replace markers → _apply_fix will try fenced-code
                    # or queue for review. Skip validation (not applicable).
                    _autofix_validation_skipped = True  # noqa: F841 — sentinel
            except Exception as _val_err:
                # Fail-open: validation infrastructure crashed → don't block fix.
                logger.debug(f"[AutoFix] [OPT-26] validation harness error (non-blocking): {_val_err}")
                _autofix_validation_skipped = True  # noqa: F841 — sentinel

            patched = agent._apply_fix(filepath, bug.suggested_fix)
            if patched:
                self._fixes_this_cycle += 1
            else:
                # [OPT-27/28] Record LLM_OUTPUT_FORMAT_ERROR (or queued-for-review)
                # to diagnostic + monitor. WHY: even "queued for review" is a
                # failure mode worth tracking — if many bugs queue for the same
                # reason, prompt needs improving.
                try:
                    from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                    from scp.autofix.monitor import FixAttempt as _FixAttempt
                    from scp.autofix.monitor import get_monitor as _get_monitor
                    _diag = _diagnose(
                        bug_id=_autofix_bug_id,
                        bug_type=bug.bug_type,
                        llm_output=bug.suggested_fix,
                        patch_parsed=None,
                        validation_result=None,
                        apply_result="failed",
                    )
                    _get_monitor().record(_FixAttempt(
                        bug_id=_autofix_bug_id,
                        bug_type=bug.bug_type,
                        provider=_autofix_provider,
                        diagnosis=_diag.diagnosis,
                        success=False,
                    ))
                except Exception as e:
                    logger.warning(f"Silent except: {e}")
                return {
                    "action": "skipped",
                    "tier": int(bug.tier),
                    "reason": "fix queued for human review (not auto-patchable)",
                    "patched": False,
                }

            # [V9.1-UPGRADE] FixVerification layer — self-verify SAU khi apply patch.
            # TẠI SAO: WHY gate (v9.0) hỏi "có nên fix không?" (action layer).
            # _verify_fix hỏi "fix có thực sự work không? có introduce new bug không?" (verify layer).
            # WHY + verify = cùng độ sâu (2 layer) như WHY (necessity + falsification).
            # Non-blocking: verify error → fail-open (don't break fix). Nếu verify
            # phát hiện new bugs → ROLLBACK (restore pre-fix content) + return skipped.
            try:
                _verify_ok, _verify_reason = self._verify_fix(filepath, [bug])
                if not _verify_ok:
                    logger.warning(
                        f"[V9.1-UPGRADE] Fix verification FAILED for {bug.file}:{bug.line}: "
                        f"{_verify_reason} — ROLLING BACK"
                    )
                    self._audit_v91("autofix_verify_fail_rollback", {
                        "file": bug.file, "line": bug.line, "bug_type": bug.bug_type,
                        "reason": _verify_reason,
                    })
                    # Rollback: restore pre-fix content
                    if _pre_fix_content is not None:
                        try:
                            filepath.write_text(_pre_fix_content, encoding="utf-8")
                            logger.info(f"[V9.1-UPGRADE] Rollback OK for {bug.file}")
                        except Exception as _rb_err:
                            logger.error(f"[V9.1-UPGRADE] Rollback FAILED for {bug.file}: {_rb_err}")
                    # Decrement counter (fix was undone)
                    self._fixes_this_cycle = max(0, self._fixes_this_cycle - 1)
                    # [OPT-27/28] Record rollback — patch applied but introduced new bugs.
                    try:
                        from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                        from scp.autofix.monitor import FixAttempt as _FixAttempt
                        from scp.autofix.monitor import get_monitor as _get_monitor
                        _diag = _diagnose(
                            bug_id=_autofix_bug_id,
                            bug_type=bug.bug_type,
                            llm_output=bug.suggested_fix,
                            patch_parsed={"search": "(applied)", "replace": "(applied)"},
                            validation_result={"valid": True, "reason": "pre-apply OK"},
                            apply_result="rollback",
                        )
                        _get_monitor().record(_FixAttempt(
                            bug_id=_autofix_bug_id,
                            bug_type=bug.bug_type,
                            provider=_autofix_provider,
                            diagnosis=_diag.diagnosis,
                            success=False,
                        ))
                    except Exception as e:
                        logger.warning(f"Silent except: {e}")
                    return {
                        "action": "skipped",
                        "tier": int(bug.tier),
                        "reason": f"fix verification failed (rolled back): {_verify_reason}",
                        "patched": False,
                    }
                self._audit_v91("autofix_verify_ok", {
                    "file": bug.file, "line": bug.line, "bug_type": bug.bug_type,
                    "reason": _verify_reason,
                })
            except Exception as _verify_call_err:
                logger.debug(f"[V9.1-UPGRADE] _verify_fix call error (fail-open): {_verify_call_err}")

            # Record for cooldown
            bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
            self._recent_fixes[bug_key] = time.time()

            # Log to audit trail (Tier 2+ always; Tier 1 only if report=True)
            if report or attack_mode:
                self._write_audit(bug, "fixed", attack_mode=attack_mode)
            else:
                # Tier 1: minimal log (for forensics, not reported to human)
                self._write_audit(bug, "fixed_silent", attack_mode=False)

            # [V5.7-WHY] Change 1: WHY → Evolution Engine reflect feedback.
            # TẠI SAO: closes the loop "fix → reflect → KB update → next fix smarter".
            # Without this, each fix is a one-shot — SCP never learns WHY bug occurred.
            # Now: after a successful fix, ask EvolutionEngine.reflect(bug, fix_diff)
            # to extract root cause + lesson → write to evolution_reflect.jsonl →
            # next time same pattern detected, classifier/scanner can use the lesson.
            # SAFETY: opt-in via SCP_EVOLUTION_ENABLED=1 (default OFF). Wrap in
            # try/except — reflect failure MUST NOT break fix (fix already applied).
            # Reflect is best-effort: if LLM call fails, KB stays as-is, no harm.
            try:
                if os.environ.get("SCP_EVOLUTION_ENABLED", "1") == "1":
                    from scp.autofix.evolution import get_evolution_engine
                    _evo = get_evolution_engine(data_dir=str(self.data_dir))
                    # fix_diff: pass the suggested_fix that was applied (best proxy
                    # we have without diffing the file post-patch).
                    _fix_diff = str(bug.suggested_fix) if bug.suggested_fix else ""
                    _reflect_result = _evo.reflect(bug, _fix_diff)
                    logger.info(
                        f"[V5.7-WHY] reflect: {bug.file}:{bug.line} "
                        f"self_falsified={getattr(_reflect_result, 'self_falsified', '?')} "
                        f"lesson={getattr(_reflect_result, 'lesson_learned', '')[:80]!r}"
                    )
            except Exception as _reflect_err:
                # Reflect failure is non-fatal — fix already applied successfully.
                logger.debug(f"[V5.7-WHY] reflect failed (non-fatal): {_reflect_err}")

            # [OPT-27/28] Record success — patch applied + verified.
            try:
                from scp.autofix.diagnostic import diagnose_fix_failure as _diagnose
                from scp.autofix.monitor import FixAttempt as _FixAttempt
                from scp.autofix.monitor import get_monitor as _get_monitor
                _diag = _diagnose(
                    bug_id=_autofix_bug_id,
                    bug_type=bug.bug_type,
                    llm_output=bug.suggested_fix,
                    patch_parsed={"search": "(applied)", "replace": "(applied)"},
                    validation_result={"valid": True, "reason": "pre-apply OK"},
                    apply_result="success",
                )
                _get_monitor().record(_FixAttempt(
                    bug_id=_autofix_bug_id,
                    bug_type=bug.bug_type,
                    provider=_autofix_provider,
                    diagnosis=_diag.diagnosis,
                    success=True,
                ))
            except Exception as e:
                logger.warning(f"Silent except: {e}")

            return {
                "action": "fixed",
                "tier": int(bug.tier),
                "patched": patched,
                "attack_mode": attack_mode,
            }

        except Exception as e:
            logger.warning(f"Auto-fix failed for {bug.file}:{bug.line}: {e}")
            return {"action": "skipped", "tier": int(bug.tier),
                    "reason": f"fix failed: {e}"}

    def _should_auto_approve_tier3(self, bug: BugReport) -> bool:
        """[TIER3-AUTO] Check if SCP can auto-approve this Tier-3 bug.

        [V4.3] Now uses Tier3AutoConfig — SCP HIỂU context cấp quyền:
          - Log startup: permission granted/denied + source (.env/API/runtime)
          - Audit trail: every auto-approve → data/tier3_auto_audit.jsonl
          - Stats: /v105/autofix/stats returns tier3_auto_enabled + reasons

        Safety guards (ALL must pass):
          1. SCP_AUTO_APPROVE_TIER3=1 env var set (via Tier3AutoConfig)
          2. Not expired (within TIER3_AUTO_TIMEOUT_SECONDS since first enable)
          3. Rate limit not exceeded (MAX_TIER3_AUTO_PER_HOUR)
          4. bug.is_relaxation == False (HARD LIMIT -- never auto-relax security)
          5. Not in cooldown (same bug fixed recently)
          6. [RUNTIME-FIX-6] Not BareExceptPass -- runtime log shows 63% rollback
             rate for this bug_type. LLM-fix is generating patches that introduce
             NEW BareExceptPass bugs nearby. Skip auto-approve for this bug_type
             until LLM-fix quality improves. Human review required.

        Returns True only if ALL guards pass.
        """
        # [V4.7] Use Tier3AutoConfig — SCP understands permission context
        _config = get_tier3_config()

        # [V4.7-CORRECT] PHÂN BIỆT 2 LOẠI QUYỀN (không trùng lặp):
        #
        # LAYER 1: QUYỀN PHÁN QUYẾT (Decision Authority)
        #   - SCP_AUTO_APPROVE_TIER3=1
        #   = AI được quyền TỰ QUYẾT ĐỊNH "Tier-3 bug này OK, tự approve"
        #   = Bỏ qua human review cho logic bugs
        #   = Đây là quyền PHÁN QUYẾT, không phải thực thi
        #
        # LAYER 2: QUYỀN THỰC THI (Execution Authority) — checked ở _auto_fix()
        #   - SCP_CAPABILITY_LEVEL=FULL_PRODUCTION
        #   = AI được quyền MODIFY CODE (apply fix)
        #   = Checked ở layer khác (không trùng lặp)
        #
        # → _should_auto_approve_tier3 CHỈ check LAYER 1 (quyền phán quyết)
        # → _auto_fix check LAYER 2 (quyền thực thi)
        # → 2 layer tách biệt, không trùng lặp

        # Guard 1: QUYỀN PHÁN QUYẾT — check SCP_AUTO_APPROVE_TIER3 only
        if not _config.is_enabled():
            return False  # Không có quyền phán quyết → không auto-approve

        # [V4.7] Log permission (chỉ lần đầu + khi thay đổi)
        _permission_source = _config.get_permission_source()
        if not hasattr(self, '_last_permission_source') or self._last_permission_source != _permission_source:
            logger.info(
                f"[TIER3-AUTO] Decision authority granted — "
                f"SCP có quyền phán quyết auto-approve Tier-3"
                f" (source: {_permission_source[:60]})"
            )
            self._last_permission_source = _permission_source

        # Guard 6 (NEW): BareExceptPass exempt from auto-approve.
        # Runtime evidence (log lines 11-66): 24 rollbacks / 38 attempts = 63% fail.
        # This bug_type is too risky for auto-fix — LLM-fix generates bad patches.
        if bug.bug_type == "BareExceptPass":
            logger.info(
                f"[TIER3-AUTO] SKIP auto-approve for BareExceptPass "
                f"({bug.file}:{bug.line}) -- requires human review "
                f"(runtime log: 63% rollback rate)"
            )
            return False

        # Guard 2: timeout (auto-expire 1h after first enable)
        now = time.time()
        if self._tier3_auto_enabled_at == 0.0:
            self._tier3_auto_enabled_at = now  # first call starts the clock
        elif now - self._tier3_auto_enabled_at > TIER3_AUTO_TIMEOUT_SECONDS:
            logger.warning(
                f"[TIER3-AUTO] Timed out after {TIER3_AUTO_TIMEOUT_SECONDS}s -- "
                f"re-enable by setting SCP_AUTO_APPROVE_TIER3=1 again"
            )
            self._tier3_auto_enabled_at = 0.0
            return False

        # Guard 3: rate limit
        self._tier3_auto_timestamps = [t for t in self._tier3_auto_timestamps if now - t < 3600]
        if len(self._tier3_auto_timestamps) >= MAX_TIER3_AUTO_PER_HOUR:
            logger.warning(
                f"[TIER3-AUTO] Rate limit hit -- {MAX_TIER3_AUTO_PER_HOUR}/hour exceeded"
            )
            return False

        # Guard 4: HARD LIMIT -- relaxation never auto-approved
        if getattr(bug, "is_relaxation", False):
            logger.warning(
                f"[TIER3-AUTO] HARD LIMIT -- bug {bug.file}:{bug.line} is relaxation "
                f"(loosens security) -> still requires human approval"
            )
            return False

        # Guard 5: cooldown (reuse _recent_fixes)
        bug_key = f"{bug.file}:{bug.line}:{bug.bug_type}"
        if bug_key in self._recent_fixes:
            if now - self._recent_fixes[bug_key] < COOLDOWN_SAME_BUG_SECONDS:
                return False

        return True

    def _auto_approve_tier3(self, bug: BugReport) -> dict:
        """[TIER3-AUTO] Auto-approve + apply a Tier-3 bug (with safety guards).

        [V4.3] Now logs audit trail — SCP records every auto-approve decision.

        Called ONLY when _should_auto_approve_tier3() returns True.
        Backs up file to .tier3bak, applies fix, logs to tier3_auto_audit.jsonl.
        """

        # [V4.3] Audit: record this auto-approve decision
        get_tier3_config().audit_auto_approve(
            bug,
            reason=f"All 6 safety guards passed (bug_type={bug.bug_type})"
        )

        # Backup file to .tier3bak (so Ga can audit/rollback later)
        try:
            from pathlib import Path as PathCls
            filepath = PathCls(bug.file)
            if filepath.exists():
                bak_path = filepath.with_suffix(filepath.suffix + ".tier3bak")
                bak_path.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[TIER3-AUTO] Backup failed for {bug.file}: {e}")

        # Apply the fix (use _auto_fix machinery, but mark as tier3_auto)
        result = self._auto_fix(bug, report=True, attack_mode=False)
        # Override tier in result to show it was Tier-3 auto
        if result.get("action") == "fixed":
            result["tier"] = 3
            result["tier3_auto_approved"] = True
            self._tier3_auto_timestamps.append(time.time())
            # Write to dedicated Tier-3 audit log
            self._write_tier3_auto_audit(bug, "auto_approved_and_fixed")
        return result

    def _write_tier3_auto_audit(self, bug: BugReport, action: str):
        """Write to data/tier3_auto_audit.jsonl (separate from normal audit)."""
        entry = {
            "timestamp": time.time(),
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "description": bug.description[:300],
            "suggested_fix": (bug.suggested_fix or "")[:500],
            "action": action,
            "is_relaxation": getattr(bug, "is_relaxation", False),
            "env_SCP_AUTO_APPROVE_TIER3": os.environ.get("SCP_AUTO_APPROVE_TIER3", "0"),
        }
        try:
            with open(self.tier3_auto_audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[TIER3-AUTO] Failed to write audit log: {e}")

    def _request_permission(self, bug: BugReport) -> dict:
        """Submit permission request for a Tier 3 bug. Does NOT fix.

        [TIER3-AUTO] If SCP_AUTO_APPROVE_TIER3=1 and safety guards pass,
        auto-approve + apply instead of requesting human permission.
        """
        # [TIER3-AUTO] Check if we can auto-approve
        if self._should_auto_approve_tier3(bug):
            logger.info(
                f"[TIER3-AUTO] Auto-approving Tier-3 bug {bug.file}:{bug.line} "
                f"({bug.bug_type}) -- SCP_AUTO_APPROVE_TIER3=1"
            )
            return self._auto_approve_tier3(bug)

        # Normal flow: request human permission
        request_id = self.permission_gate.request_permission(bug)
        self._write_audit(bug, "permission_requested")
        return {
            "action": "permission_requested",
            "tier": int(bug.tier),
            "request_id": request_id,
            "reason": "logic bug — human approval required",
        }

    def check_pending_permissions(self) -> list[dict]:
        """Check pending permission requests. Returns list of approved ones
        that are ready to fix."""
        ready = []
        for req in self.permission_gate.list_pending():
            status = self.permission_gate.check_permission(req.request_id)
            if status == "approved":
                ready.append({
                    "request_id": req.request_id,
                    "file": req.file,
                    "line": req.line,
                    "fix": req.suggested_fix,
                    "approved_by": req.decided_by,
                })
        return ready

    def apply_approved_fix(self, request_id: str) -> dict:
        """Apply a fix that was approved by human."""
        # Find the request
        req = self.permission_gate._pending.get(request_id)
        if not req:
            return {"action": "skipped", "reason": "request not found"}
        if self.permission_gate.check_permission(request_id) != "approved":
            return {"action": "skipped", "reason": "not approved"}

        # Apply the fix
        bug = BugReport(
            file=req.file, line=req.line,
            bug_type=req.bug_type,
            description=req.description,
            suggested_fix=req.suggested_fix,
            tier=BugTier.TIER_3_PERMISSION,
            affects_logic=True,
        )
        result = self._auto_fix(bug, report=True)
        if result.get("action") == "fixed":
            self._write_audit(bug, "fixed_after_permission",
                              extra={"request_id": request_id, "approved_by": req.decided_by})
        return result

    def _write_audit(self, bug: BugReport, action: str, attack_mode: bool = False,
                     extra: dict = None):
        """Write to audit log (append-only JSONL)."""
        entry = {
            "timestamp": time.time(),
            "file": bug.file,
            "line": bug.line,
            "bug_type": bug.bug_type,
            "tier": int(bug.tier),
            "action": action,
            "attack_mode": attack_mode,
            "description": bug.description[:200],
        }
        if extra:
            entry.update(extra)
        try:
            with open(self.audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Silent except: {e}")

    def set_attack_mode(self, enabled: bool):
        """Toggle attack mode. When enabled, Tier 4 restraints auto-apply."""
        self.in_attack_mode = enabled
        logger.info(f"[AutoFix] Attack mode {'ENABLED' if enabled else 'DISABLED'}")

    def stats(self) -> dict:
        """Return stats for monitoring."""
        now = time.time()
        tier3_auto_remaining = max(
            0, MAX_TIER3_AUTO_PER_HOUR - len(self._tier3_auto_timestamps)
        )
        tier3_auto_expires_in = 0
        if self._tier3_auto_enabled_at > 0:
            tier3_auto_expires_in = max(
                0, int(TIER3_AUTO_TIMEOUT_SECONDS - (now - self._tier3_auto_enabled_at))
            )
        return {
            "in_attack_mode": self.in_attack_mode,
            "fixes_this_cycle": self._fixes_this_cycle,
            "tier4_last_hour": len(self._tier4_timestamps),
            "pending_permissions": len(self.permission_gate.list_pending()),
            "recent_fixes": len(self._recent_fixes),
            "cycle_started_at": self._cycle_start_time,
            "cycle_resets_in": max(
                0, int(CYCLE_RESET_SECONDS - (time.time() - self._cycle_start_time))
            ),
            # [TIER3-AUTO] Tier-3 auto-approve monitoring
            "tier3_auto_enabled": os.environ.get("SCP_AUTO_APPROVE_TIER3", "0") == "1",
            "tier3_auto_used_this_hour": len(self._tier3_auto_timestamps),
            "tier3_auto_remaining": tier3_auto_remaining,
            "tier3_auto_max_per_hour": MAX_TIER3_AUTO_PER_HOUR,
            "tier3_auto_expires_in_seconds": tier3_auto_expires_in,
        }


# ============================================================
# [EXEC-1 A1] SINGLETON ACCESSOR
# ============================================================
# TẠI SAO: previously api_server.py created AutoFixEngine() per-request
# (5 call sites) — throwaway instances. This meant:
#   - `in_attack_mode` set via /attack-mode/{enabled} was lost on next request
#     (each new engine defaulted to False) → Tier 4 autonomy NEVER ran
#   - `_fixes_this_cycle` reset to 0 every request → rate limit was a no-op
#     (engine could "fix" 1000 bugs in 1000 requests, never hitting the cap)
#   - `_recent_fixes` cooldown reset every request → same bug re-fixed on
#     every API call (cooldown violated)
#   - `permission_gate._pending` reloaded from disk every request →
#     race condition between concurrent request handlers
# Fix: singleton via get_autofix_engine() — single instance shared across
# all API handlers + the deep audit runner. Mirrors get_gateway() pattern.
_autofix_engine: AutoFixEngine | None = None
_autofix_lock = threading.Lock()


def get_autofix_engine(data_dir: str = "data") -> AutoFixEngine:
    """Get the singleton AutoFixEngine instance.

    [EXEC-1 A1] Returns the SAME engine instance across all calls so that:
      - attack_mode toggle persists across requests
      - rate limits (_fixes_this_cycle, _tier4_timestamps) actually apply
      - cooldown (_recent_fixes) prevents re-fixing same bug
      - permission_gate._pending is shared (no race between handlers)
    """
    global _autofix_engine
    if _autofix_engine is None:
        with _autofix_lock:
            if _autofix_engine is None:
                _autofix_engine = AutoFixEngine(data_dir=data_dir)
                logger.info("[AutoFix] Singleton engine initialized")
    return _autofix_engine


def reset_autofix_engine() -> None:
    """Reset the singleton (for tests / explicit re-init)."""
    global _autofix_engine
    with _autofix_lock:
        _autofix_engine = None
