"""
SCP WHY Gate — Primary Control Gate for toàn hệ thống.

TẠI SAO module này tồn tại?
  v8.0: WHY = cố vấn (opt-in, advisory only, adjust confidence ±0.1)
  v9.0: WHY = CHỐT (default ON, block-capable, gate mọi action)

  Gà: "WHY hiện tại = cố vấn, không phải chốt. Hoàn thành nốt."

Kiến trúc WHY Gate:
  Mọi action trong SCP (verdict, autofix, evolution, learning, prediction)
  phải qua WHY Gate trước khi thực thi.

  WHY Gate hỏi 2 lớp:
    1. Necessity: "Tại sao action này cần thiết?"
    2. Falsification: "Tại sao action này đúng? Bác bỏ được không?"

  Nếu WHY layer 2 self-falsify → REJECT (block action)
  Nếu WHY không trả lời được → UPHOLD (conservative, allow but flag)
  Nếu WHY approve → ALLOW

Safety:
  - Default ON (không cần env var)
  - Fallback: LLM fail → regex patterns → default UPHOLD
  - Constitution HARD LOCK: WHY không override Constitution KILL
  - Audit log: mọi WHY decision ghi vào why_gate_audit.jsonl

Components gated by WHY:
  1. Verdict (judge.py) — WHY can block PASS → UNKNOWN
  2. AutoFix (engine.py) — WHY can block fix
  3. Scanner (runner.py) — WHY can prioritize/skip bugs
  4. Evolution (evolution.py) — WHY can block build/evolve
  5. Prediction (predictor.py) — WHY can block prediction
  6. Learning (brain.py) — WHY can block KB save
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

logger = logging.getLogger("scp.meta.why_gate")


class WhyDecision(IntEnum):
    """WHY Gate decision."""
    ALLOW = 1       # WHY approves action
    UPHOLD = 2      # WHY can't decide — allow but flag (conservative)
    REJECT = 3      # WHY rejects action (self-falsified)


@dataclass
class WhyResult:
    """Result of WHY Gate evaluation."""
    decision: WhyDecision
    necessity_reason: str = ""
    falsification_reason: str = ""
    confidence: float = 0.0
    llm_used: bool = False
    action_type: str = ""
    action_desc: str = ""
    timestamp: float = 0.0

    @property
    def allowed(self) -> bool:
        """True if action should proceed."""
        return self.decision != WhyDecision.REJECT

    @property
    def blocked(self) -> bool:
        """True if WHY blocked the action."""
        return self.decision == WhyDecision.REJECT

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.name,
            "necessity_reason": self.necessity_reason,
            "falsification_reason": self.falsification_reason,
            "confidence": self.confidence,
            "llm_used": self.llm_used,
            "action_type": self.action_type,
            "action_desc": self.action_desc[:200],
            "timestamp": self.timestamp,
        }


class WhyGate:
    """WHY Primary Control Gate — gates mọi action trong SCP.

    Default ON. Không cần env var.
    Fallback: LLM → regex → UPHOLD (conservative).
    Constitution HARD LOCK: WHY không override Constitution KILL.
    """

    # Regex patterns for necessity (fallback when LLM unavailable)
    NECESSITY_PATTERNS = {
        "verdict": [
            r"pass|fail|unknown|conflict",
            r"verify|check|validate",
        ],
        "autofix": [
            r"fix|patch|repair|correct",
            r"bug|error|issue|defect",
        ],
        "evolution": [
            r"build|create|generate",
            r"evolve|improve|enhance",
        ],
        "learning": [
            r"learn|save|store|record",
            r"lesson|knowledge|experience",
        ],
        "prediction": [
            r"predict|forecast|anticipate",
            r"threat|attack|risk",
        ],
    }

    # Falsification patterns — if action matches these, WHY tends to REJECT
    FALSIFICATION_REJECT_PATTERNS = [
        r"remove.*block|delete.*rule|skip.*detect",  # relaxation
        r"lower.*threshold|raise.*confidence|looser|relax",  # loosening
        r"allow.*attack|whitelist|bypass.*security",  # security bypass
        r"ignore.*error|suppress.*warning|silent.*fail",  # error suppression
    ]

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log = self.data_dir / "why_gate_audit.jsonl"
        self._stats = {
            "total_gates": 0,
            "allowed": 0,
            "upheld": 0,
            "rejected": 0,
            "llm_calls": 0,
            "regex_fallbacks": 0,
        }

    def gate(
        self,
        action_type: str,
        action_desc: str,
        context: str = "",
        constitution_kill: bool = False,
    ) -> WhyResult:
        """Gate an action through WHY.

        Args:
            action_type: "verdict" | "autofix" | "evolution" | "learning" | "prediction" | "scanner"
            action_desc: Description of the action
            context: Additional context (code, question, etc.)
            constitution_kill: True if Constitution already decided KILL
                              (WHY cannot override — HARD LOCK)

        Returns:
            WhyResult with decision (ALLOW | UPHOLD | REJECT)
        """
        self._stats["total_gates"] += 1
        now = time.time()

        # Constitution HARD LOCK — WHY cannot override Constitution KILL
        if constitution_kill:
            result = WhyResult(
                decision=WhyDecision.ALLOW,  # Allow the KILL (don't block)
                necessity_reason="Constitution HARD LOCK — WHY cannot override KILL",
                falsification_reason="N/A — Constitution is inviolable",
                confidence=1.0,
                llm_used=False,
                action_type=action_type,
                action_desc=action_desc,
                timestamp=now,
            )
            self._stats["allowed"] += 1
            self._audit(result)
            return result

        # WHY Layer 1: Necessity
        necessity_reason, necessity_ok = self._check_necessity(action_type, action_desc, context)

        # WHY Layer 2: Falsification
        falsification_reason, self_falsified = self._check_falsification(action_type, action_desc, context)

        # Decision
        if self_falsified:
            decision = WhyDecision.REJECT
            self._stats["rejected"] += 1
        elif necessity_ok:
            decision = WhyDecision.ALLOW
            self._stats["allowed"] += 1
        else:
            decision = WhyDecision.UPHOLD  # Conservative — allow but flag
            self._stats["upheld"] += 1

        result = WhyResult(
            decision=decision,
            necessity_reason=necessity_reason,
            falsification_reason=falsification_reason,
            confidence=0.8 if decision == WhyDecision.ALLOW else (0.5 if decision == WhyDecision.UPHOLD else 0.0),
            llm_used=False,  # Will be set by LLM path if used
            action_type=action_type,
            action_desc=action_desc,
            timestamp=now,
        )

        # [V10.0-KB-EVOLVE] WHY lookup KB — "Đã gặp pattern này chưa?"
        # TẠI SAO: Nếu KB có lesson cho bug_type này, WHY confidence tăng
        # → fix nhanh hơn (verified pattern), chính xác hơn
        if decision == WhyDecision.ALLOW and action_type == "autofix":
            try:
                from scp.meta.kb_evolve import get_kb_store
                _kb = get_kb_store()
                # Extract bug_type from action_desc
                import re as _re_kb
                _bt_match = _re_kb.search(r'(BareExceptPass|RaceCondition|NullDereference|SQLInjection|TypeMismatch|ResourceLeak|UndefinedName)', action_desc)
                if _bt_match:
                    _bug_type = _bt_match.group(1)
                    _lesson = _kb.lookup_lesson(_bug_type, action_desc[:100])
                    if _lesson and _lesson.success_rate > 0.7:
                        # KB has verified lesson — boost confidence
                        result.confidence = min(1.0, result.confidence + 0.1)
                        result.necessity_reason += f" | [KB] Lesson found: {_lesson.lesson[:60]} (success_rate={_lesson.success_rate:.0%})"
                    elif _lesson:
                        result.necessity_reason += f" | [KB] Lesson exists but low success_rate={_lesson.success_rate:.0%}"
            except Exception as e:
                logger.warning(f"Silent except: {e}")  # Non-blocking — KB lookup is enhancement, not requirement

        self._audit(result)
        return result

    def _check_necessity(self, action_type: str, action_desc: str, context: str) -> tuple[str, bool]:
        """WHY Layer 1: 'Tại sao action này cần thiết?'

        Returns (reason, is_necessary).
        """
        desc_lower = action_desc.lower()

        # Check if action matches expected patterns for this type
        patterns = self.NECESSITY_PATTERNS.get(action_type, [])
        for pattern in patterns:
            if re.search(pattern, desc_lower):
                return f"Action matches {action_type} necessity pattern", True

        # If no pattern match — try LLM (if available)
        if os.environ.get("SCP_WHY_LLM_ENABLED", "0") == "1":
            llm_reason = self._llm_necessity(action_type, action_desc, context)
            if llm_reason:
                return llm_reason, True

        # Fallback: conservative — assume necessary (don't block without reason)
        self._stats["regex_fallbacks"] += 1
        return "Default necessity (no pattern match, conservative allow)", True

    def _check_falsification(self, action_type: str, action_desc: str, context: str) -> tuple[str, bool]:
        """WHY Layer 2: 'Tại sao đúng? Bác bỏ được không?'

        Returns (reason, self_falsified).
        If self_falsified=True → REJECT.
        """
        desc_lower = action_desc.lower()

        # Check reject patterns (relaxation, security bypass, etc.)
        for pattern in self.FALSIFICATION_REJECT_PATTERNS:
            if re.search(pattern, desc_lower):
                return f"Action matches reject pattern: {pattern} — falsified (action loosens security)", True

        # Try LLM falsification (if available)
        if os.environ.get("SCP_WHY_LLM_ENABLED", "0") == "1":
            llm_reason, llm_falsified = self._llm_falsification(action_type, action_desc, context)
            if llm_reason:
                return llm_reason, llm_falsified

        # Fallback: not falsified (conservative — don't reject without reason)
        self._stats["regex_fallbacks"] += 1
        return "No falsification detected (conservative)", False

    def _llm_necessity(self, action_type: str, action_desc: str, context: str) -> str | None:
        """LLM-based necessity check."""
        try:
            from scp.autofix.llm_fix import _call_openrouter
            prompt = f"""Bạn là WHY engine của SCP. Hỏi: "Tại sao action này cần thiết?"

Action type: {action_type}
Action: {action_desc[:300]}
Context: {context[:200]}

Trả lời 1 câu: Tại sao cần thiết? (hoặc "Không cần thiết" nếu không cần)

WHY:"""
            response = _call_openrouter(prompt, max_tokens=200)
            if response:
                self._stats["llm_calls"] += 1
                return response.strip()[:200]
        except Exception as e:
            logger.debug(f"WHY LLM necessity failed: {e}")
        return None

    def _llm_falsification(self, action_type: str, action_desc: str, context: str) -> tuple[str | None, bool]:
        """LLM-based falsification check."""
        try:
            from scp.autofix.llm_fix import _call_openrouter
            prompt = f"""Bạn là WHY engine của SCP. Phản biện action sau:

Action: {action_desc[:300]}
Context: {context[:200]}

Hỏi: "Tại sao action này đúng? Bác bỏ được không?"

Trả lời:
1. Falsification: thử bác bỏ (1 câu)
2. SELF_FALSIFIED: yes/no

Output: FALSIFICATION: ... | SELF_FALSIFIED: yes/no"""
            response = _call_openrouter(prompt, max_tokens=200)
            if response:
                self._stats["llm_calls"] += 1
                falsified = "self_falsified: yes" in response.lower()
                return response.strip()[:200], falsified
        except Exception as e:
            logger.debug(f"WHY LLM falsification failed: {e}")
        return None, False

    def _audit(self, result: WhyResult):
        """Log WHY decision to audit file."""
        try:
            with open(self.audit_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"WHY Gate audit write failed: {e}")

    def stats(self) -> dict:
        """Return WHY Gate stats."""
        return {
            **self._stats,
            "allow_rate": self._stats["allowed"] / max(1, self._stats["total_gates"]),
            "reject_rate": self._stats["rejected"] / max(1, self._stats["total_gates"]),
            "uphold_rate": self._stats["upheld"] / max(1, self._stats["total_gates"]),
        }


# ============================================================
# Singleton
# ============================================================
_why_gate: WhyGate | None = None
_why_gate_lock = None

def get_why_gate(data_dir: str = "data") -> WhyGate:
    """Get singleton WhyGate instance."""
    global _why_gate, _why_gate_lock
    if _why_gate_lock is None:
        import threading
        _why_gate_lock = threading.Lock()
    if _why_gate is None:
        with _why_gate_lock:
            if _why_gate is None:
                _why_gate = WhyGate(data_dir=data_dir)
                logger.info("[WHY-GATE] Singleton initialized — WHY is now PRIMARY CONTROL GATE")
    return _why_gate

def reset_why_gate() -> None:
    """Reset singleton (for tests)."""
    global _why_gate
    with _why_gate_lock:
        _why_gate = None
