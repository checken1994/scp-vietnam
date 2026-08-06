"""
[OPT-28] AutoFixMonitor — track success rate + diagnosis breakdown.

DNA SCP #8 KB accumulation: learn from past fixes.
Tracks:
  - Total bugs attempted
  - Success rate (by bug type)
  - Failure breakdown (LLM vs format vs validation vs rollback)
  - Provider performance (which LLM provider succeeds most)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("scp.autofix.monitor")


@dataclass
class FixAttempt:
    bug_id: str
    bug_type: str
    provider: str  # ollama:<model> (e.g. ollama:deepseek-r1:8b) or openrouter
    diagnosis: str  # from diagnostic.py
    success: bool
    elapsed_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class AutoFixMonitor:
    """Monitor AutoFix performance over time."""

    def __init__(self, max_history: int = 1000):
        self._attempts: list[FixAttempt] = []
        self._max_history = max_history

    def record(self, attempt: FixAttempt) -> None:
        """Record a fix attempt."""
        self._attempts.append(attempt)
        if len(self._attempts) > self._max_history:
            self._attempts = self._attempts[-self._max_history:]

    def get_stats(self) -> dict:
        """Get aggregated stats."""
        if not self._attempts:
            return {"total": 0, "message": "No attempts recorded"}

        total = len(self._attempts)
        success = sum(1 for a in self._attempts if a.success)

        # By bug type
        by_bug_type: dict[str, dict] = {}
        for a in self._attempts:
            if a.bug_type not in by_bug_type:
                by_bug_type[a.bug_type] = {"total": 0, "success": 0, "failure": 0}
            by_bug_type[a.bug_type]["total"] += 1
            if a.success:
                by_bug_type[a.bug_type]["success"] += 1
            else:
                by_bug_type[a.bug_type]["failure"] += 1

        # By diagnosis
        by_diagnosis: dict[str, int] = {}
        for a in self._attempts:
            by_diagnosis[a.diagnosis] = by_diagnosis.get(a.diagnosis, 0) + 1

        # By provider
        by_provider: dict[str, dict] = {}
        for a in self._attempts:
            if a.provider not in by_provider:
                by_provider[a.provider] = {"total": 0, "success": 0, "success_rate": 0.0}
            by_provider[a.provider]["total"] += 1
            if a.success:
                by_provider[a.provider]["success"] += 1
        for p in by_provider:
            t = by_provider[p]["total"]
            by_provider[p]["success_rate"] = by_provider[p]["success"] / t if t > 0 else 0.0

        return {
            "total_attempts": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate": success / total if total > 0 else 0.0,
            "by_bug_type": by_bug_type,
            "by_diagnosis": by_diagnosis,
            "by_provider": by_provider,
        }

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get recent attempts."""
        return [
            {
                "bug_id": a.bug_id,
                "bug_type": a.bug_type,
                "provider": a.provider,
                "diagnosis": a.diagnosis,
                "success": a.success,
                "elapsed_ms": a.elapsed_ms,
            }
            for a in self._attempts[-limit:]
        ]


# Singleton
_monitor: AutoFixMonitor | None = None

def get_monitor() -> AutoFixMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AutoFixMonitor()
    return _monitor


__all__ = ["AutoFixMonitor", "FixAttempt", "get_monitor"]
