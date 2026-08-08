"""
[Task 9-B] SLM base classes — extracted from runtime/slms.py to break circular
import between slms.py and slm_impls/*.py.

TẠI SAO: slm_impls modules need BaseSLM + SLMResponse, but slms.py imports
those SLM classes. Circular import solved by moving base classes here.

Backward-compatible — slms.py re-exports BaseSLM, SLMResponse from here.
"""
from __future__ import annotations

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
<<<<<<< HEAD
from typing import Any
=======
from typing import Any, Optional
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)

logger = logging.getLogger("scp.slms")


# [V104.32 #22] Token boundary helper (same as data_sources/_token_boundary_match)
def _token_boundary_match_slms(key: str, entity_lower: str) -> bool:
    if not key or not entity_lower:
        return False



# ============================================================
# V14 SLM RESPONSE
# ============================================================
@dataclass
class SLMResponse:
    """Response từ 1 SLM."""
    question: str
    answer: str
    confidence: float
    domain: str
    reasoning: str
    evidence: dict[str, Any]
    slm_name: str
    processing_time: float


# ============================================================
# BASE SLM — Lớp nền cho các SLM chuyên biệt
# ============================================================
class BaseSLM(ABC):
    """Base class cho tất cả SLM chuyên biệt."""

<<<<<<< HEAD
    def __init__(self, name: str, domain: str, config: dict = None):
=======
    def __init__(self, name: str, domain: str, config: Optional[dict] = None):
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
        self.name = name
        self.domain = domain
        self.config = config or {}
        self.response_cache: dict[str, tuple[float, SLMResponse]] = {}
        self._MAX_CACHE_SIZE = 500  # [OPT] Reduced from 1000
        self.stats = {
            "total_queries": 0,
            "total_time": 0.0,
            "success_count": 0,
            "error_count": 0,
        }

    @abstractmethod
    def predict(self, question: str) -> SLMResponse:
        """Dự đoán câu trả lời cho câu hỏi."""
        pass

    @abstractmethod
    def get_confidence(self, question: str, answer: str) -> float:
        """Tính độ tin cậy của câu trả lời."""
        pass

    def _cache_key(self, question: str) -> str:
        # [Z.ai-ROOT-FIX #9] Normalize trước khi hash — TẠI SAO: biến thể câu hỏi
        # ("Số xương..." vs "Cơ thể người có bao nhiêu xương?") trùng nhau nhưng
        # hash khác nhau → cache miss. Normalize → cùng hash → cache hit.
        # [Task 7-B] TẠI SAO: SHA-256 thay MD5 (CWE-327, B324) — collision risk
        # thấp hơn, không phá cache logic (key dài hơn 32→64 chars, dict OK).
        try:
            from scp.core.question_normalizer import normalize_question
            normalized = normalize_question(question)
            return hashlib.sha256(normalized.encode()).hexdigest()
        except ImportError:
            return hashlib.sha256(question.encode()).hexdigest()

    def _keyword_match(self, question: str, keywords) -> bool:
        """[Z.ai-ROOT-FIX #9] Synonym-aware keyword matching.
        Replaces `if keyword in q_lower` pattern in all SLMs.
        Handles: 'bao nhiêu xương' → match keyword 'số xương' (synonym).
        """
        try:
            from scp.core.question_normalizer import keyword_match
            return keyword_match(question, keywords)
        except ImportError:
            # Fallback: original substring match
            q_lower = question.lower()
            return any(kw.lower() in q_lower for kw in keywords if kw)

    def cache_response(self, question: str, response: SLMResponse):
        # [FIXED] Enforce size limit - evict oldest entries
        if len(self.response_cache) >= self._MAX_CACHE_SIZE:
            oldest_keys = sorted(self.response_cache.keys(),
                key=lambda k: self.response_cache[k][0])[:self._MAX_CACHE_SIZE // 2]
            for k in oldest_keys:
                del self.response_cache[k]
        self.response_cache[self._cache_key(question)] = (time.time(), response)

    def get_cached(self, question: str, ttl: int = 3600) -> SLMResponse | None:
        key = self._cache_key(question)
        if key in self.response_cache:
            ts, resp = self.response_cache[key]
            if time.time() - ts < ttl:
                return resp
            del self.response_cache[key]
        return None

    def _start_timer(self):
        self.stats["total_queries"] += 1
        return time.time()

    def _end_timer(self, start_time: float, success: bool):
        elapsed = time.time() - start_time
        self.stats["total_time"] += elapsed
        if success:
            self.stats["success_count"] += 1
        else:
            self.stats["error_count"] += 1

    def _healing_retry_slm(self, issue: dict) -> bool:
        """[V88 FIX] Clear smart cache for failed questions so they get re-processed."""
        try:
            # _run_periodic_cleanup()  # [V89] deduplicated - function not available
            logger.info("[HEALING] Cleared smart cache for 50 recent failed questions")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] retry_slm failed: {e}")
            return False

    def _healing_switch_domain(self, issue: dict) -> bool:
        """[V89 FIX] Don't blindly set domain='general'. Clear smart_cache so questions get re-classified."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            # Clear smart_cache for failed questions so they get re-classified with updated keywords
            _db_exec("DELETE FROM smart_cache_disk WHERE question IN (SELECT question FROM error_history WHERE final_verdict = 'FAIL' ORDER BY id DESC LIMIT 30)")
            # Also clear verdict_cache so they get re-judged
            _db_exec("DELETE FROM verdict_cache WHERE question IN (SELECT question FROM error_history WHERE final_verdict = 'FAIL' ORDER BY id DESC LIMIT 30)")
            logger.info("[HEALING] Cleared cache for 30 failed questions — will re-classify on next cycle")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] switch_domain failed: {e}")
            return False

    def _healing_reality_fallback(self, issue: dict) -> bool:
        """[V88 FIX] Clear stale live_knowledge_cache entries."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM live_knowledge_cache WHERE timestamp < datetime('now', '-1 day')")
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
        total = max(1, self.stats["total_queries"])
        return {
            "name": self.name,
            "domain": self.domain,
            "total_queries": self.stats["total_queries"],
            "avg_time": round(self.stats["total_time"] / total, 4),
            "success_rate": round(self.stats["success_count"] / total * 100, 1),
            "cache_size": len(self.response_cache),
        }


# [OPT-7] Alias for clarity — SCP's "SLM" means "Specialized Logic Module"
# (deterministic dispatcher), NOT "Small Language Model" (neural net).
# New code should use `DomainExpert` to avoid confusion.
# See SLM_NAMING_NOTE.md for full explanation.
DomainExpert = BaseSLM
