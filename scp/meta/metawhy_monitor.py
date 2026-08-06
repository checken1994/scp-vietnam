"""
SCP MetaWhy Monitor — detect emergent patterns in WHY engine.

TẠI SAO module này tồn tại?
  WHY engine đặt câu hỏi "Tại sao?" liên tục. Nhưng nếu SCP cứ hỏi cùng 1
  loại câu hỏi lặp đi lặp lại → có thể đang bị bias hoặc stuck in loop.

  MetaWhyMonitor theo dõi các câu hỏi WHY, phát hiện:
  - Pattern lặp (cùng type câu hỏi xuất hiện >N lần/ngày)
  - Theme mới xuất hiện (emergent)
  - Loop detection (cùng câu hỏi trong 1h)

  → Alert human nếu phát hiện loop = missing piece SCP không tự giải được.

TẠI SAO không block WHY engine?
  - WHY phải tự do hỏi bất cứ gì (nguyên tắc #9: luon con missing piece)
  - MetaWhy chỉ MONITOR + ALERT, không block
  - Human quyết định có action hay không

Safety:
  - Thread-safe (Lock)
  - Persist to data/metawhy_patterns.jsonl (append-only)
  - No PII logging (only question patterns, not full text)
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path

logger = logging.getLogger("scp.meta.metawhy_monitor")


class MetaWhyMonitor:
    """Monitor WHY engine for emergent patterns and loops."""

    # Pattern categories to track
    WHY_CATEGORIES = {
        "necessity": r"tại sao cần|why necessary|why need",
        "falsification": r"tại sao đúng|bác bỏ|falsif|why true",
        "root_cause": r"tại sao xảy ra|root cause|why happen",
        "missing_piece": r"còn thiếu|missing|what.*lack",
        "boundary": r"giới hạn|boundary|limit|scope",
        "assumption": r"giả định|assumption|presuppos",
        "alternative": r"cách khác|alternative|other way",
        "consequence": r"hậu quả|consequence|if.*then",
    }

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_file = self.data_dir / "metawhy_patterns.jsonl"
        self._lock = threading.Lock()
        self._recent_questions: list[tuple[str, float]] = []  # (categorized, timestamp)

    def record_why(self, question: str, why_plan: dict) -> None:
        """Record a WHY question + its plan. Categorize for pattern detection."""
        categorized = self._categorize(question)
        entry = {
            "timestamp": time.time(),
            "question_hash": self._hash_question(question),
            "category": categorized,
            "why_plan_target": (why_plan.get("target") or "")[:100] if why_plan else "",
            "why_plan_strategy": (why_plan.get("verification_strategy") or "")[:100] if why_plan else "",
        }
        with self._lock:
            self._recent_questions.append((categorized, time.time()))
            # Keep only last 1000
            if len(self._recent_questions) > 1000:
                self._recent_questions = self._recent_questions[-1000:]
            try:
                with open(self.patterns_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"[metawhy] Failed to write pattern: {e}")

    def _categorize(self, question: str) -> str:
        """Categorize a WHY question."""
        q_lower = question.lower()
        for category, pattern in self.WHY_CATEGORIES.items():
            if re.search(pattern, q_lower):
                return category
        return "other"

    def _hash_question(self, question: str) -> str:
        """Hash question for dedup (no PII)."""
        import hashlib
        normalized = re.sub(r'\s+', ' ', question.lower().strip())
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]

    def detect_patterns(self) -> list[dict]:
        """Detect patterns in WHY questions."""
        with self._lock:
            if not self._recent_questions:
                return []
            # Count by category
            categories = [cat for cat, _ in self._recent_questions]
            counter = Counter(categories)
            total = len(categories)
            patterns = []
            for cat, count in counter.most_common():
                pct = (count / total) * 100
                if pct > 30:  # >30% of questions in 1 category = pattern
                    patterns.append({
                        "category": cat,
                        "count": count,
                        "percentage": round(pct, 1),
                        "severity": "high" if pct > 50 else "medium",
                    })
            return patterns

    def get_emergent_themes(self, window_days: int = 7) -> list[str]:
        """Get themes that emerged in last N days."""
        cutoff = time.time() - (window_days * 86400)
        with self._lock:
            recent = [(cat, ts) for cat, ts in self._recent_questions if ts > cutoff]
        if not recent:
            return []
        categories = [cat for cat, _ in recent]
        counter = Counter(categories)
        # Themes = categories with >5 occurrences
        return [cat for cat, count in counter.most_common() if count >= 5]

    def alert_if_loop(self) -> str | None:
        """Check if SCP is in a loop (same category >5 times in 1h)."""
        cutoff = time.time() - 3600  # last 1h
        with self._lock:
            recent = [cat for cat, ts in self._recent_questions if ts > cutoff]
        if len(recent) < 5:
            return None
        counter = Counter(recent)
        most_common_cat, count = counter.most_common(1)[0]
        if count >= 5:
            return (
                f"[MetaWhy ALERT] SCP asked '{most_common_cat}' questions {count} times "
                f"in last hour — possible loop or stuck on missing piece. "
                f"Human review recommended."
            )
        return None
