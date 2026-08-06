"""
SCP V100 — H8 RedTeamBridge (Bypass Analysis Chiều 2)
======================================================
Tự học từ bypass: "TẠI SAO không chặn được?" → phân tích → sinh rule → test → tối ưu.

2 chiều học:
  Chiều 1 (đã có): Chặn thành công → ghi rule → chặn nhanh hơn
  Chiều 2 (MỚI):    Bypass → tự hỏi "Tại sao?" → gap analysis → sinh rule → test → activate

Bypass Analysis Pipeline:
  1. Detect bypass (verdict=PASS + attack signatures in question)
  2. Extract what was MISSED (which patterns didn't match)
  3. Generate new rule from the gap
  4. Test rule on history (false positive check)
  5. If pass → activate rule in AttackPatternMemory
  6. If fail → escalate to human

Parallel execution: bypass analysis runs in background thread, không block pipeline.
"""
from __future__ import annotations

import hashlib
import logging
import re
import re as _re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.security.h8_redteam")


# ============================================================
# ATTACK SIGNATURES — patterns to detect in questions
# ============================================================

ATTACK_SIGNATURES: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "injection_ignore_prev"),
    (r"forget\s+(your|all|previous)\s+(instructions?|training|rules?)", "injection_forget"),
    (r"disregard\s+(all\s+)?(prior|previous|above)\s+(context|instructions?|rules?)", "injection_disregard"),
    (r"skip\s+(above|all|previous)\s+(rules?|instructions?)", "injection_skip"),
    (r"you\s+are\s+(now|dan|do\s+anything)\s*[,.]?", "jailbreak_role"),
    (r"(reveal|show|print|display)\s+(your|the)\s+(system\s+)?prompt", "exfil_prompt"),
    (r"override\s+(safety|rules|restrictions?)", "jailbreak_override"),
    (r"developer\s+mode|no\s+rules?|unrestricted", "jailbreak_mode"),
    (r"pretend\s+(you\s+are|to\s+be)\s+(an?\s+)?(evil|unrestricted|dan)", "jailbreak_pretend"),
    (r"act\s+as\s+(if\s+)?(you\s+have\s+)?no\s+(restrictions?|guidelines?)", "jailbreak_act"),
    (r"new\s+instructions?\s*:", "injection_new_instr"),
    (r"system\s*:\s*", "injection_system_role"),
    (r"<\|im_start\|>|<\|system\|>|<\|user\|>", "injection_role_token"),
    (r"bỏ\s+qua\s+(tất\s+cả\s+)?(lệnh|hướng\s+dẫn)", "vn_injection_ignore"),
    (r"quên\s+(tất\s+cả\s+)?(lệnh|hướng\s+dẫn)", "vn_injection_forget"),
    (r"bây\s+giờ\s+bạn\s+là", "vn_jailbreak_role"),
    (r"vượt\s+quá\s+(giới\s+hạn|hạn\s+chế)", "vn_jailbreak_override"),
    (r"eval\s*\(|exec\s*\(|subprocess|os\.system|__import__", "rce_attempt"),
    (r"\{\{.*\}\}|\{%.*%\}", "template_injection"),
    (r"(continue|now|then|next)\s+.*\s+(ignore|forget|reveal)", "multi_turn_setup"),
    (r"i\s+am\s+(?:your|the)\s+(?:developer|admin|creator|owner)", "authority_claim"),
    (r"reveal\s+(?:the\s+)?(?:contents?\s+of\s+)?(?:your|the)\s+(?:system\s+)?(?:message|prompt|config)", "exfil_prompt"),
    (r"show\s+me\s+(?:your|the)\s+(?:system\s+)?(?:prompt|config)", "exfil_prompt"),
    (r"print\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)", "exfil_prompt"),
    (r"what\s+was\s+(?:your|the)\s+(?:initial|system)\s+instruction", "exfil_prompt"),
    (r"dan\s+(?:has\s+no|no)\s+restrictions", "jailbreak_dan"),
    (r"aim\s+mode\s+activated", "jailbreak_aim"),
    (r"stan\s+(?:strive|avoid\s+norms)", "jailbreak_stan"),
]

_COMPILED = [(re.compile(p, re.I | re.S), sig) for p, sig in ATTACK_SIGNATURES]


@dataclass
class BypassRecord:
    """1 bypass event — SCP bị tấn công thành công."""
    bypass_id: str
    timestamp: float
    source: str
    question: str
    answer: str
    verdict: str
    attack_signatures: list[str]
    classification: dict[str, Any]
    response_hash: str
    canary_token: str = ""


@dataclass
class BypassAnalysis:
    """Kết quả phân tích bypass — 'TẠI SAO không chặn được?'"""
    bypass_id: str
    timestamp: float
    root_cause: str           # "missing_pattern" | "encoding_bypass" | "classifier_miss" | ...
    missed_signatures: list[str]  # signatures in question but NOT in existing rules
    suggested_rule_pattern: str   # regex pattern for new rule
    suggested_rule_type: str      # keyword | pattern | strategy
    test_result: str         # "passed" | "failed_fp" | "failed_other"
    activated: bool          # True if rule was activated
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bypass_id": self.bypass_id,
            "timestamp": self.timestamp,
            "root_cause": self.root_cause,
            "missed_signatures": self.missed_signatures,
            "suggested_rule_pattern": self.suggested_rule_pattern,
            "suggested_rule_type": self.suggested_rule_type,
            "test_result": self.test_result,
            "activated": self.activated,
            "detail": self.detail,
        }


class H8RedTeamBridge:
    """H8 — Bypass detection + analysis chiều 2 + auto-rule generation.

    Naming convention: <Purpose>Bridge (world standard).

    Pipeline (chiều 2 — bypass analysis):
      1. Detect bypass: verdict=PASS + attack signatures in question
      2. Extract missed signatures: which patterns existed but weren't caught
      3. Generate new rule: create regex from missed pattern
      4. Test rule: check false positive on normal questions history
      5. Activate: if FP rate < 30% → add to AttackPatternMemory
      6. Escalate: if FP rate > 30% → log for human review

    Parallel: bypass analysis runs in ThreadPoolExecutor, không block judge().
    """

    def __init__(
        self,
        data_dir: str = "data",
        attack_memory=None,
        max_workers: int = 2,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bypass_log = self.data_dir / "bypass_log.jsonl"
        self.analysis_log = self.data_dir / "bypass_analysis.jsonl"
        self.attack_memory = attack_memory  # AttackPatternMemory instance
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="h8-analysis")

        # Normal questions history for FP testing (collected from PASS verdicts)
        # [EXEC-3] TẠI SAO: _normal_questions is appended from main thread
        # (record_normal_question / process_verdict) while worker threads in
        # the ThreadPoolExecutor iterate it during bypass analysis →
        # 'list changed size during iteration' RuntimeError. Lock all access.
        self._normal_questions: list[str] = []
        self._normal_questions_lock = threading.Lock()
        self._max_normal_history = 1000

        self._stats = {
            "total_requests": 0,
            "total_bypasses": 0,
            "total_blocked": 0,
            "total_rules_generated": 0,
            "total_rules_activated": 0,
            "total_rules_rejected_fp": 0,
            "last_bypass": 0,
        }
        self._load_stats()

    def detect_attack_signatures(self, text: str) -> list[str]:
        """Detect attack signatures in text."""
        if not text:
            return []
        signatures = []
        text_lower = text
        for pattern, sig in _COMPILED:
            if pattern.search(text_lower):
                signatures.append(sig)
        return signatures

    def is_bypass(self, verdict: str, question: str, classification: dict = None) -> tuple[bool, list[str]]:
        """Determine if this is a bypass.

        Bypass = verdict PASS + attack signatures present.
        [BUG-2 FIX] Also record UNKNOWN + ≥3 signatures for analysis ( SCP returns
        UNKNOWN 99.4% of the time → H8 never receives bypasses → 0 rules generated).
        UNKNOWN + ≥3 sigs = SCP couldn't verify but attack signatures present →
        potentially a bypass that SCP missed by returning UNKNOWN instead of FAIL.
        """
        signatures = self.detect_attack_signatures(question)
        if not signatures:
            return False, []

        # [V104.18 #6 FIX] Require ≥2 signatures for bypass (was: 1 → false bypass on benign)
        if verdict in ("PASS", "UNREFUTED_IN_CURRENT_SCOPE") and len(signatures) >= 2:
            return True, signatures

        # [BUG-2 FIX] UNKNOWN + ≥3 signatures = potential bypass (SCP couldn't verify)
        # TÁI SAO: SCP trả UNKNOWN 99.4% → H8 không bao giờ nhận bypass → 0 rules.
        # Nếu question có ≥3 attack signatures nhưng SCP trả UNKNOWN → có thể SCP
        # bị bypass (trả UNKNOWN thay vì FAIL). Record for analysis.
        if verdict == "UNKNOWN" and len(signatures) >= 3:
            return True, signatures

        # If FAIL but low confidence → still record for analysis
        if verdict == "FAIL" and classification:
            conf = classification.get("confidence", 0)
            if conf < 0.5:
                return False, signatures  # not bypass but interesting

        return False, signatures

    def record_bypass(
        self,
        question: str,
        answer: str,
        verdict: str,
        classification: dict[str, Any],
        source: str = "unknown",
        attacker_ip: str = "",
    ) -> BypassRecord | None:
        """Record bypass + trigger analysis (async, parallel).

        Returns BypassRecord if bypass detected, None otherwise.
        """
        self._stats["total_requests"] += 1
        is_bp, signatures = self.is_bypass(verdict, question, classification)

        if verdict in ("FAIL", "UNKNOWN"):
            self._stats["total_blocked"] += 1

        if not is_bp:
            # Still log if signatures present (for learning)
            if signatures:
                self._async_log({
                    "type": "caught_with_signatures",
                    "timestamp": time.time(),
                    "source": source,
                    "question": question[:500],
                    "verdict": verdict,
                    "signatures": signatures,
                })
            # Save normal question for FP testing
            if verdict == "PASS":
                with self._normal_questions_lock:
                    if len(self._normal_questions) < self._max_normal_history:
                        self._normal_questions.append(question[:200])
            return None

        # === BYPASS DETECTED ===
        self._stats["total_bypasses"] += 1
        self._stats["last_bypass"] = time.time()

        bypass_id = hashlib.sha256(f"{question}{time.time()}".encode()).hexdigest()[:16]
        canary_token = f"CANARY_{hashlib.sha256(f'{attacker_ip}{bypass_id}'.encode()).hexdigest()[:16]}"

        record = BypassRecord(
            bypass_id=bypass_id,
            timestamp=time.time(),
            source=source,
            question=question[:1000],
            answer=answer[:1000],
            verdict=verdict,
            attack_signatures=signatures,
            classification=classification,
            response_hash=hashlib.sha256(answer.encode()).hexdigest()[:16],
            canary_token=canary_token,
        )

        # Log bypass
        self._async_log({
            "type": "BYPASS",
            "bypass_id": bypass_id,
            "timestamp": record.timestamp,
            "source": source,
            "attacker_ip": attacker_ip,
            "question": record.question,
            "answer": record.answer,
            "verdict": verdict,
            "signatures": signatures,
            "classification": classification,
            "canary_token": canary_token,
        })

        # === TRIGGER ANALYSIS CHIỀU 2 (async, parallel) ===
        self._executor.submit(self._analyze_bypass, record)

        logger.warning(
            f"🚨 BYPASS DETECTED [{source}] id={bypass_id} sigs={signatures} "
            f"question='{question[:60]}...' verdict={verdict}"
        )
        return record

    def _analyze_bypass(self, record: BypassRecord):
        """Analyze bypass — 'TẠI SAO không chặn được?'

        This runs in background thread, không block pipeline.

        Steps:
          1. Identify missed signatures
          2. Generate new rule pattern
          3. Test rule on normal questions (FP check)
          4. Activate or reject
        """
        analysis = BypassAnalysis(
            bypass_id=record.bypass_id,
            timestamp=time.time(),
            root_cause="",
            missed_signatures=[],
            suggested_rule_pattern="",
            suggested_rule_type="pattern",
            test_result="",
            activated=False,
        )

        # Step 1: Identify missed signatures
        # Compare attack signatures in question with existing rules
        existing_keywords = set()
        if self.attack_memory:
            for rule in self.attack_memory.dynamic_rules.values():
                existing_keywords.add(rule.pattern.lower())

        missed = []
        for sig in record.attack_signatures:
            # Check if any existing rule covers this signature
            sig_covered = False
            for kw in existing_keywords:
                # [V104.38 #91] TẠI SAO: V104.32 #18 passed compiled pattern + flags → TypeError → H8 dead.
                # Fix: use keyword (loop var) with word boundary, not compiled pattern + flags.
                if _re.search(r'\b' + _re.escape(kw) + r'\b', record.question, _re.IGNORECASE):
                    sig_covered = True
                    break
            if not sig_covered:
                missed.append(sig)

        analysis.missed_signatures = missed

        if not missed:
            analysis.root_cause = "all_signatures_covered_but_classifier_missed"
            analysis.detail = "Existing rules should have caught this — classifier logic error"
            analysis.test_result = "no_rule_needed"
            self._log_analysis(analysis)
            return

        # Step 2: Generate new rule pattern
        # Extract key phrase from question around the missed signature
        rule_pattern = self._generate_rule_pattern(record.question, missed)
        analysis.suggested_rule_pattern = rule_pattern

        if not rule_pattern:
            analysis.root_cause = "could_not_extract_pattern"
            analysis.test_result = "failed_other"
            self._log_analysis(analysis)
            return

        analysis.root_cause = "missing_pattern"

        # Step 3: Test rule on normal questions (FP check)
        # [EXEC-3] Snapshot under lock so worker thread iterates a stable copy
        # while main thread may keep appending new PASS questions.
        with self._normal_questions_lock:
            normal_snapshot = list(self._normal_questions)
        fp_count = 0
        total_tested = len(normal_snapshot)
        for nq in normal_snapshot:
            try:
                if re.search(rule_pattern, nq, re.IGNORECASE):
                    fp_count += 1
            except Exception as e:
                logger.debug(f"[V104.37] security/h8_redteam_bridge.py: e={e}")

        fp_rate = fp_count / total_tested if total_tested > 0 else 0.0

        # Step 4: Activate or reject
        if fp_rate > 0.3:
            analysis.test_result = "failed_fp"
            analysis.detail = f"FP rate {fp_rate:.1%} > 30% threshold — rejected"
            analysis.activated = False
            self._stats["total_rules_rejected_fp"] += 1
            logger.info(f"[H8] Rule rejected (FP={fp_rate:.1%}): {rule_pattern[:60]}")
        elif total_tested < 10:
            analysis.test_result = "insufficient_test_data"
            analysis.detail = f"Only {total_tested} normal questions — need >=10 for reliable FP test"
            analysis.activated = False
        else:
            analysis.test_result = "passed"
            analysis.activated = True
            self._stats["total_rules_activated"] += 1

            # Activate in AttackPatternMemory
            if self.attack_memory:
                # [FIX #7] MD5 → SHA256 (CWE-327). See attack_memory.py:214.
                rule_id = f"h8_auto_{hashlib.sha256(rule_pattern.encode()).hexdigest()[:8]}"
                from scp.security.attack_memory import DynamicRule
                rule = DynamicRule(
                    rule_id=rule_id,
                    rule_type="pattern",
                    pattern=rule_pattern,
                    description=f"H8 auto-generated from bypass {record.bypass_id}",
                    created_at=time.time(),
                    hits=1,
                    promoted=True,
                    promoted_at=time.time(),
                    source_signatures=missed,
                    examples=[record.question[:100]],
                )
                # [FIX-CRIT-46 BUG 4+5] TẠI SAO: the H8 background thread was
                # mutating self.attack_memory.dynamic_rules / _compiled_rules /
                # _keyword_index WITHOUT holding self.attack_memory._lock. This
                # is concurrent with judge.py main-thread check_against_rules()
                # (which holds the lock and iterates _compiled_rules /
                # _keyword_index) → "dictionary changed size during iteration"
                # RuntimeError → swallowed by judge.py:1403 except → attack
                # silently MISSED. Also breaks the [V104.50 #P1-9] lock contract
                # that AttackPatternMemory was designed around.
                # ADDITIONALLY: no _save_rule() call after the direct mutation →
                # rules NEVER persisted to disk → H8 "auto-rule generation"
                # learning is wiped each SCP restart. When ThreatSimulator runs
                # AttackPatternMemory().record_bypass(...) on a fresh instance,
                # its _save_rule rewrites dynamic_rules.jsonl from disk-loaded
                # state (which lacks H8 rules) → H8 rules also overwritten on
                # disk between restarts.
                # Fix: (BUG 4) wrap the whole mutation block in the lock;
                # (BUG 5) call self.attack_memory._save_rule(rule) at the end so
                # the new rule survives restart + ThreatSimulator rewrite. The
                # _save_rule call is inside the lock — RLock allows re-entry.
                with self.attack_memory._lock:
                    self.attack_memory.dynamic_rules[rule_id] = rule
                    try:
                        self.attack_memory._compiled_rules[rule_id] = re.compile(rule_pattern, re.IGNORECASE | re.UNICODE)
                    except Exception as e:
                        logger.debug(f"[V104.37] security/h8_redteam_bridge.py: e={e}")
                    # [V108 FIX] Also add to keyword index for immediate matching.
                    # Extract keyword from pattern for fast lookup.
                    # [P2-24 FIX] TẠI SAO: the keyword-index update was OUTSIDE the
                    # `if self.attack_memory:` block AND referenced `rule` (only defined
                    # inside that block) + `keyword` (only defined inside the inner
                    # `if kw_match:`). On the fp_rejected / insufficient_data / no-memory
                    # paths, `rule` and `keyword` were undefined → NameError, swallowed
                    # by the ThreadPoolExecutor's per-task except → H8 auto-rule
                    # generation almost NEVER completed. Additionally, `rule.rule_type`
                    # was hardcoded "pattern" (line 371), so `== "keyword"` was always
                    # False → the keyword index was never populated even on success.
                    # Fix: (a) move the keyword-index update INSIDE `if self.attack_memory:`
                    # AND inside `if kw_match:`; (b) only add to keyword index when the
                    # rule is genuinely a keyword rule (H8 currently always makes
                    # "pattern" rules, so this branch is informational — but if a future
                    # change makes keyword rules, it'll work). For pattern rules, the
                    # compiled_rules entry above already enables matching.
                    kw_match = re.search(r'([a-zà-ỹ]+)', rule_pattern, re.IGNORECASE)
                    if kw_match:
                        keyword = kw_match.group(1).lower()
                        # [V104.32 #19] Only keyword-type rules go into keyword index
                        # (was: H8 pattern rules added "ignore" → blocked benign questions)
                        if rule.rule_type == "keyword":
                            self.attack_memory._keyword_index[keyword] = rule_id
                            logger.info(f"[H8] Keyword rule ACTIVATED: {rule_id} kw={keyword!r}")
                        else:
                            # Pattern rule — already in _compiled_rules above. Log for visibility.
                            logger.info(f"[H8] Pattern rule ACTIVATED: {rule_id} pattern={rule_pattern[:50]} — will block next time")
                    else:
                        logger.info(f"[H8] Pattern rule ACTIVATED: {rule_id} pattern={rule_pattern[:50]} — will block next time")
                    # [FIX-CRIT-46 BUG 5] Persist the new rule to disk so it
                    # survives restart + is not overwritten by ThreatSimulator's
                    # record_bypass (which rewrites dynamic_rules.jsonl from the
                    # disk-loaded state).
                    self.attack_memory._save_rule(rule)

        self._stats["total_rules_generated"] += 1
        self._log_analysis(analysis)

    def _generate_rule_pattern(self, question: str, missed_signatures: list[str]) -> str:
        """Generate regex pattern from missed signatures + question context."""
        # Map signatures to regex patterns
        sig_to_pattern = {
            "injection_ignore_prev": r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?)",
            "injection_forget": r"forget\s+(?:your\s+)?(?:all\s+)?(?:previous\s+)?(?:instructions?|training|rules?)",
            "injection_disregard": r"disregard\s+(?:all\s+)?(?:prior|previous|above)\s+(?:context|instructions?|rules?)",
            "injection_skip": r"skip\s+(?:above|all|previous)\s+(?:rules?|instructions?)",
            "jailbreak_role": r"you\s+are\s+(?:now\s+)?(?:dan|do\s+anything)",
            "jailbreak_dan": r"dan\s+(?:has\s+no|no)\s+restrictions",
            "jailbreak_aim": r"aim\s+mode\s+activated",
            "jailbreak_stan": r"stan\s+(?:strive|avoid\s+norms)",
            "jailbreak_override": r"override\s+(?:safety|rules?|restrictions?)",
            "jailbreak_mode": r"(?:developer\s+mode|no\s+rules?|unrestricted\s+mode)",
            "jailbreak_pretend": r"pretend\s+(?:you\s+are|to\s+be)\s+(?:an?\s+)?(?:evil|unrestricted|dan)",
            "jailbreak_act": r"act\s+as\s+(?:if\s+)?(?:you\s+have\s+)?no\s+(?:restrictions?|guidelines?)",
            "exfil_prompt": r"(?:reveal|show|print|display)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|message|config)",
            "injection_new_instr": r"new\s+instructions?\s*:",
            "injection_system_role": r"system\s*:\s*",
            "injection_role_token": r"<\|im_start\|>|<\|system\|>",  # nosec B105 — regex pattern detecting prompt-injection markers, not a credential
            "vn_injection_ignore": r"bỏ\s+qua\s+(?:tất\s+cả\s+)?(?:lệnh|hướng\s+dẫn)",
            "vn_injection_forget": r"quên\s+(?:tất\s+cả\s+)?(?:lệnh|hướng\s+dẫn)",
            "vn_jailbreak_role": r"bây\s+giờ\s+bạn\s+là",
            "vn_jailbreak_override": r"vượt\s+quá\s+(?:giới\s+hạn|hạn\s+chế)",
            "rce_attempt": r"(?:eval|exec|subprocess|os\.system|__import__)",
            "template_injection": r"\{\{.*\}\}|\{%.*%\}",
            "multi_turn_setup": r"(?:continue|now|then)\s+.*\s+(?:ignore|forget|reveal)",
            "authority_claim": r"i\s+am\s+(?:your|the)\s+(?:developer|admin|creator|owner)",
        }

        # Use first missed signature to generate pattern
        for sig in missed_signatures:
            if sig in sig_to_pattern:
                return sig_to_pattern[sig]

        # Fallback: extract keyword from question
        words = question.lower().split()
        attack_words = [w for w in words if len(w) > 3 and any(
            w in kws for kws in [
                ["ignore", "forget", "disregard", "skip", "override"],
                ["reveal", "show", "print", "system", "prompt"],
                ["dan", "jailbreak", "unrestricted", "developer"],
                ["bỏ", "qua", "quên", "lệnh", "hướng"],
            ]
        )]
        if attack_words:
            return re.escape(attack_words[0])

        return ""

    def record_normal_question(self, question: str):
        """Record normal question for FP testing."""
        with self._normal_questions_lock:
            if len(self._normal_questions) < self._max_normal_history:
                self._normal_questions.append(question[:200])

    def _async_log(self, entry: dict[str, Any]):
        """Async log to bypass file."""
        try:
            with open(self.bypass_log, "a", encoding="utf-8") as f:
                f.write(__import__("json").dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[H8] log error: {e}")

    def _log_analysis(self, analysis: BypassAnalysis):
        """Log analysis result."""
        try:
            import json
            with open(self.analysis_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(analysis.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[H8] analysis log error: {e}")

    def _load_stats(self):
        """Load stats from existing log."""
        if not self.bypass_log.is_file():
            return
        try:
            import json
            bypasses = 0
            blocked = 0
            requests = 0
            for line in self.bypass_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    requests += 1
                    if d.get("type") == "BYPASS":
                        bypasses += 1
                    elif d.get("type") == "caught_with_signatures":
                        blocked += 1
                except Exception:  # noqa: S112
                    continue
            self._stats["total_requests"] = requests
            self._stats["total_bypasses"] = bypasses
            self._stats["total_blocked"] = blocked
        except Exception as e:
            logger.warning(f"[H8] stats load error: {e}")

    def get_recent_bypasses(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent bypasses for dashboard."""
        if not self.bypass_log.is_file():
            return []
        try:
            import json
            lines = self.bypass_log.read_text(encoding="utf-8").splitlines()
            bypasses = []
            for line in reversed(lines):
                if line.strip():
                    try:
                        d = json.loads(line)
                        if d.get("type") == "BYPASS":
                            bypasses.append(d)
                            if len(bypasses) >= limit:
                                break
                    except Exception:  # noqa: S112
                        continue
            return bypasses
        except Exception:
            return []

    def get_recent_analyses(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent bypass analyses."""
        if not self.analysis_log.is_file():
            return []
        try:
            import json
            lines = self.analysis_log.read_text(encoding="utf-8").splitlines()
            analyses = []
            for line in reversed(lines):
                if line.strip():
                    try:
                        analyses.append(json.loads(line))
                        if len(analyses) >= limit:
                            break
                    except Exception:  # noqa: S112
                        continue
            return analyses
        except Exception:
            return []

    def stats(self) -> dict[str, Any]:
        with self._normal_questions_lock:
            nq_count = len(self._normal_questions)
        return {
            **self._stats,
            "normal_questions_for_fp_test": nq_count,
            "signatures_count": len(ATTACK_SIGNATURES),
        }

    def close(self):
        self._executor.shutdown(wait=True)


__all__ = ["BypassRecord", "BypassAnalysis", "H8RedTeamBridge", "ATTACK_SIGNATURES"]
