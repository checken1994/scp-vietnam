"""
SCP V90 — Self-Healing Engine Module
V14 healing strategies + error recovery.
Extracted from engine.py for modularity.
"""

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from scp.core.db_manager import (
    checkpoint_wal,
    db_exec,
)

logger = logging.getLogger("scp.healing_v14")

# HEALING STRATEGY — Chiến lược tự sửa lỗi
# ============================================================
@dataclass
class HealingStrategy:
    """Chiến lược tự sửa lỗi."""
    name: str
    description: str
    trigger_conditions: list[str]
    actions: list[str]
    priority: int
    success_rate: float = 0.0
    last_used: Optional[str] = None


# ============================================================
# V14 SELF-HEALING ENGINE — Enhanced với strategies
# ============================================================
class V14SelfHealingEngine:
    """
    Self-Healing Engine V14 — Enhanced với 5 healing strategies.

    Strategies:
      1. retry_slm         — Gọi lại SLM với tham số khác
      2. switch_domain     — Chuyển sang SLM khác
      3. reality_fallback  — Fallback sang V13 Reality Engine
      4. cache_refresh     — Làm mới cache
      5. scale_resource    — Tăng tài nguyên
    """

<<<<<<< HEAD
    def __init__(self, config: dict = None, judge=None):
=======
    def __init__(self, config: Optional[dict] = None, judge=None):
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
        self.config = config or {}
        self.health_metrics: dict[str, Any] = {}
        self.healing_strategies: list[HealingStrategy] = []
        self.healing_history: list[dict] = []
        self.error_patterns: dict[str, int] = defaultdict(int)
        # [FIX v5] Reference to RealityJudge (for reduce_error strategy)
        self.judge = judge
        self._init_strategies()

    def _init_strategies(self):
        # Create healing_history table (V14 specific)
        try:
            db_exec("""CREATE TABLE IF NOT EXISTS healing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, error_type TEXT, strategy TEXT,
                success BOOLEAN, details TEXT, duration REAL
            )""")
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        self.healing_strategies = [
            HealingStrategy(
                name="retry_slm",
                description="Gọi lại SLM với tham số khác",
                trigger_conditions=["slm_timeout", "slm_error"],
                actions=["increase_timeout", "use_alternative_slm"],
                priority=1,
            ),
            HealingStrategy(
                name="switch_domain",
                description="Chuyển sang SLM khác cùng domain",
                trigger_conditions=["domain_accuracy_low", "slm_confidence_low"],
                actions=["use_backup_slm", "reroute_to_judge"],
                priority=2,
            ),
            HealingStrategy(
                name="reality_fallback",
                description="Fallback sang V13 Reality Engine",
                trigger_conditions=["all_slm_fail", "high_uncertainty"],
                actions=["use_reality_engine", "mark_for_review"],
                priority=3,
            ),
            HealingStrategy(
                name="cache_refresh",
                description="Làm mới cache",
                trigger_conditions=["stale_data", "cache_corruption"],
                actions=["clear_cache", "reload_from_source"],
                priority=4,
            ),
            HealingStrategy(
                name="scale_resource",
                description="Tăng tài nguyên xử lý",
                trigger_conditions=["high_latency", "out_of_memory"],
                actions=["increase_threads", "use_fallback_model"],
                priority=5,
            ),
            # [P2] New strategy for high_error_rate
            HealingStrategy(
                name="reduce_error",
                description="Giảm error rate bằng cách điều chỉnh threshold",
                trigger_conditions=["high_error_rate"],
                actions=["adjust_confidence_threshold", "retry_with_fallback"],
                priority=1,
            ),
        ]

    def monitor(self, system_state: dict) -> dict:
        """[V89 FIX] Giám sát hệ thống — detect ALL issue types, not just 2."""
        issues = []
        max_latency = self.config.get("max_latency", 5.0)
        max_error_rate = self.config.get("max_error_rate", 0.1)
        ts = datetime.now().isoformat()

        # 1. High latency → scale_resource (was already working)
        if system_state.get("slm_latency", 0) > max_latency:
            issues.append({"type": "high_latency", "severity": "warning",
                          "details": f"SLM latency {system_state['slm_latency']}s > {max_latency}s",
                          "source": "slm", "timestamp": ts})

        # 2. High error rate → reduce_error (was already working)
        if system_state.get("error_rate", 0) > max_error_rate:
            issues.append({"type": "high_error_rate", "severity": "critical",
                          "details": f"Error rate {system_state['error_rate']*100:.1f}%",
                          "source": "system", "timestamp": ts})

        # [V89] 3. SLM errors → retry_slm (was NEVER triggered)
        slm_responses = system_state.get("slm_responses", [])
        has_errors = any(r.get("error") for r in slm_responses if isinstance(r, dict))
        if has_errors:
            issues.append({"type": "slm_error", "severity": "warning",
                          "details": "One or more SLMs returned errors",
                          "source": "slm", "timestamp": ts})

        # [V89] 4. Low confidence → switch_domain (was NEVER triggered)
        confidence = system_state.get("confidence", 0.5)
        if confidence < 0.3 and system_state.get("verdict") not in ("PASS",):
            issues.append({"type": "slm_confidence_low", "severity": "warning",
                          "details": f"SLM confidence {confidence:.2f} < 0.30",
                          "source": "slm", "timestamp": ts})

        # [V89] 5. All SLM fail → reality_fallback (was NEVER triggered)
        verdict = system_state.get("verdict", "")
        if verdict == "UNKNOWN" and confidence < 0.2:
            issues.append({"type": "all_slm_fail", "severity": "critical",
                          "details": f"All SLMs failed — verdict={verdict}, confidence={confidence:.2f}",
                          "source": "system", "timestamp": ts})

        # [V89] 6. Stale data → cache_refresh (was NEVER triggered)
        # Check if verdict is stale (same question answered before with different result)
        if verdict == "CONFLICT":
            issues.append({"type": "stale_data", "severity": "warning",
                          "details": "Verdict conflict — possible stale cached data",
                          "source": "cache", "timestamp": ts})

        for issue in issues:
            self.error_patterns[issue["type"]] = self.error_patterns.get(issue["type"], 0) + 1

        return {"has_issues": len(issues) > 0, "issues": issues, "patterns": dict(self.error_patterns)}

    def heal(self, issue: dict) -> dict:
        """Áp dụng chiến lược tự sửa cho issue."""
        start_time = time.time()
        strategy = self._find_strategy(issue)

        # [P2] Always log to healing_history, even if no strategy found
        if not strategy:
            strategy_name = "NO_STRATEGY"
            success = False
        else:
            success = self._execute_strategy(strategy, issue)
            strategy_name = strategy.name
            # Update strategy success rate (exponential moving average)
            strategy.success_rate = strategy.success_rate * 0.8 + (1 if success else 0) * 0.2
            strategy.last_used = datetime.now().isoformat()

        duration = time.time() - start_time

        # [P2] Log to SQLite (always, even if no strategy)
        try:
            db_exec("""INSERT INTO healing_history (timestamp, error_type, strategy, success, details, duration)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (datetime.now().isoformat(), issue.get("type", "unknown"), strategy_name,
                     1 if success else 0, json.dumps(issue), duration))
        except Exception as e:
            # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed INSERT failures
            # silently → healing history loss is invisible to operators.
            logger.warning(f"[healing_v14] _record_healing INSERT failed: {e}")

        # [FIXED] Periodic WAL checkpoint to prevent unbounded WAL growth
        try:
            if hasattr(self, '_heal_count'):
                self._heal_count += 1
                if self._heal_count >= 100:
                    checkpoint_wal()
                    self._heal_count = 0
            else:
                self._heal_count = 0
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        # [FIXED] Keep in-memory list bounded to prevent memory leak
        self.healing_history.append({
            "timestamp": datetime.now().isoformat(), "error_type": issue.get("type", "unknown"),
            "strategy": strategy_name, "success": success, "duration": duration,
        })
        if len(self.healing_history) > 1000:  # Cap at 1000 entries
            self.healing_history = self.healing_history[-500:]

        if strategy:
            return {"success": success, "strategy": strategy.name,
                    "message": f"Applied {strategy.name}: {'Success' if success else 'Failed'}",
                    "duration": duration}
        else:
            return {"success": False, "message": "No strategy found", "issue": issue}

    def _find_strategy(self, issue: dict) -> Optional[HealingStrategy]:
        eligible = [s for s in self.healing_strategies if issue["type"] in s.trigger_conditions]
        if not eligible:
            return None
        eligible.sort(key=lambda s: (s.priority, -s.success_rate))
        return eligible[0]

    def _execute_strategy(self, strategy: HealingStrategy, issue: dict) -> bool:
        try:
            # Actual implementations
            if strategy.name == "reduce_error":
                # [V89 FIX] DON'T lower threshold globally — that creates false positives!
                # Instead: lower confidence of knowledge that fails repeatedly
                if self.judge is None:
                    logger.warning("[HEALING] reduce_error: no judge reference, skipping")
                    return False
                # [V89] Only lower confidence of SPECIFIC failing knowledge, not global threshold
                try:
                    from scp.core.db_manager import db_exec as _db_exec
                    from scp.core.db_manager import db_query_all as _db_qa
                    # 1. Lower confidence of knowledge in high-error domains
                    _db_exec("UPDATE knowledge SET confidence = confidence * 0.8 WHERE confidence > 0.5 AND entity IN (SELECT entity FROM experiences WHERE verdict = 'FAIL' GROUP BY entity HAVING COUNT(*) > 2)")
                    # 2. Add failing questions to reverify_queue for retry with fresh data
                    failing = _db_qa("SELECT DISTINCT question, frame AS domain FROM error_history WHERE final_verdict = 'FAIL' ORDER BY id DESC LIMIT 10")
                    for fq in failing:
                        try:
                            _db_exec("INSERT OR IGNORE INTO reverify_queue (timestamp, question, domain, original_verdict, original_confidence, cooldown_minutes, scheduled_at, status, attempts) VALUES (?, ?, ?, 'FAIL', 0.5, 60, ?, 'pending', 0)",
                                     (datetime.now().isoformat(), fq['question'][:500], fq['domain'],
                                      datetime.now().isoformat()))
                        except Exception as e:
                            # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed INSERT
                            # failures silently → failing questions not queued for reverify.
                            logger.warning(f"[healing_v14] reverify_queue INSERT failed for fq={fq.get('question', '')[:60]!r}: {e}")
                    logger.info(f"[HEALING] Reduced confidence for failing knowledge + queued {len(failing)} questions for reverify")
                except Exception as e:
                    logger.warning(f"[HEALING] reduce_error failed: {e}")
                return True

            # [V88 FIX] Actually implement other strategies
            executors = {
                "retry_slm": lambda: self._healing_retry_slm(issue),
                "switch_domain": lambda: self._healing_switch_domain(issue),
                "reality_fallback": lambda: self._healing_reality_fallback(issue),
                "cache_refresh": lambda: self._healing_cache_refresh(issue),
                "scale_resource": lambda: True,  # No-op on free tier
            }
            return executors.get(strategy.name, lambda: False)()
        except Exception as e:
            logger.error(f"Error executing strategy {strategy.name}: {e}")
            return False

    def _healing_retry_slm(self, issue: dict) -> bool:
        """[V88 FIX] Clear smart cache for failed questions so they get re-processed."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM smart_cache_disk WHERE identifier IN (SELECT question FROM error_history WHERE error_type != 'UNKNOWN' ORDER BY timestamp DESC LIMIT 50)  -- [V104.34 #31] TẠI SAO: error_history has no cache_key column, use question→identifier mapping")
            logger.info("[HEALING] Cleared smart cache for 50 recent failed questions")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] retry_slm failed: {e}")
            return False

    def _healing_switch_domain(self, issue: dict) -> bool:
        """[V89 FIX] [P2-19 FIX] [ROOT-FIX 40-A] Don't blindly set domain='general'. Clear smart_cache so questions get re-classified.

        TẠI SAO: V104.34 #32 fix used `WHERE cache_key IN (SELECT question ...)` —
        but cache_key is a SHA256 HASH of the question, not the raw question text.
        error_history.question IS the raw text. So `cache_key IN (raw questions)`
        matched ZERO rows → verdict_cache was never cleared → failed questions
        kept returning stale cached verdicts. Fix (per FIX-C canonical schema):
        use `question_text` (the column that stores the raw question).

        [ROOT-FIX 40-A] Was: cleared cache for final_verdict='FAIL' questions.
        But FAIL = attack blocked by V98 — clearing cache means re-judging the
        SAME attack next cycle → wasted work + drives the 495-retry loop
        (every re-judge re-enqueues UNKNOWN subtypes). Fix: only clear cache
        for final_verdict='UNKNOWN' (genuine unknowns that genuinely need
        re-classification with fresh data). DNA SCP #7 (AutoFix safe) + #9 (No harm).
        """
        try:
            from scp.core.db_manager import db_exec as _db_exec
            # [ROOT-FIX 40-A] Clear smart_cache for UNKNOWN questions so they
            # get re-classified with updated keywords. FAIL = attack blocked —
            # do NOT clear (avoids re-judging attacks every cycle).
            _db_exec("DELETE FROM smart_cache_disk WHERE identifier IN (SELECT question FROM error_history WHERE final_verdict = 'UNKNOWN' ORDER BY id DESC LIMIT 30)")
            # [P2-19 FIX] verdict_cache canonical schema (FIX-C) stores raw question
            # in `question_text`, not in `cache_key` (which is a hash). Match on text.
            _db_exec("DELETE FROM verdict_cache WHERE question_text IN (SELECT question FROM error_history WHERE final_verdict = 'UNKNOWN' ORDER BY id DESC LIMIT 30)")
            logger.info("[HEALING] Cleared cache for 30 UNKNOWN questions — will re-classify on next cycle (FAIL=attack-blocked skipped)")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] switch_domain failed: {e}")
            return False

    def _healing_reality_fallback(self, issue: dict) -> bool:
        """[V88 FIX] Clear stale live_knowledge_cache entries."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM live_knowledge_cache WHERE fetched_at < datetime('now', '-1 day')  -- [V104.34 #33] TẠI SAO: column is fetched_at, not timestamp")
            logger.info("[HEALING] Cleared stale live knowledge cache (>1 day old)")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] reality_fallback failed: {e}")
            return False

    def _healing_cache_refresh(self, issue: dict) -> bool:
        """[V88 FIX] Clear old verdict cache to force re-evaluation."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM verdict_cache WHERE rowid NOT IN (SELECT rowid FROM verdict_cache ORDER BY rowid DESC LIMIT 100)")
            logger.info("[HEALING] Trimmed verdict cache to 100 most recent")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] cache_refresh failed: {e}")
            return False

    def get_stats(self) -> dict:
        total = len(self.healing_history)
        successes = sum(1 for h in self.healing_history if h["success"])
        avg_duration = sum(h["duration"] for h in self.healing_history) / max(1, total)
        return {
            "total_healing_attempts": total,
            "successful_healings": successes,
            "success_rate": round(successes / max(1, total) * 100, 1),
            "avg_duration": round(avg_duration, 4),
            "error_patterns": dict(self.error_patterns),
            "strategies": [
                {"name": s.name, "success_rate": round(s.success_rate * 100, 1),
                 "priority": s.priority, "last_used": s.last_used}
                for s in self.healing_strategies
            ],
        }


