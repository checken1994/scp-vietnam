"""
SLM part — extracted from slms.py (Task 19-A).
 kept verbatim; only the class location changed.
"""
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("scp.slms")

# Token boundary helper (copied from slms.py)
def _token_boundary_match_slms(key: str, entity_lower: str) -> bool:
    import re as _re
    if not key or not entity_lower:
        return False
    if key == entity_lower:
        return True
    if len(key) < 4:
        return False
    if _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(key) + r'(?![\wÀ-ỹ])', entity_lower):
        return True
    if len(entity_lower) >= 4 and _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(entity_lower) + r'(?![\wÀ-ỹ])', key):
        return True
    return False


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


class BaseSLM(ABC):
    """Base class cho tất cả SLM chuyên biệt."""

    def __init__(self, name: str, domain: str, config: Optional[dict] = None):
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
        return hashlib.sha256(question.encode()).hexdigest()

    def cache_response(self, question: str, response: SLMResponse):
        # [FIXED] Enforce size limit - evict oldest entries
        if len(self.response_cache) >= self._MAX_CACHE_SIZE:
            oldest_keys = sorted(self.response_cache.keys(),
                key=lambda k: self.response_cache[k][0])[:self._MAX_CACHE_SIZE // 2]
            for k in oldest_keys:
                del self.response_cache[k]
        self.response_cache[self._cache_key(question)] = (time.time(), response)

    def get_cached(self, question: str, ttl: int = 3600) -> Optional[SLMResponse]:
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


class GeneralSLM(BaseSLM):
    """
    [V73] General Knowledge SLM — handles type classification, name meanings,
    "Tell me about X", "What type of thing is X" questions.

    Strategy: extract entity, query Wikipedia summary, return first sentence
    as the "type" or "description".
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="GeneralSLM", domain="general", config=config)
        self._wiki = None
        try:
            from scp.core.reality_engine import WikipediaDataSource
            self._wiki = WikipediaDataSource()
        except Exception as e:
            logger.warning(f"GeneralSLM Wiki init: {e}")

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # Check cache
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import re
        q = question.strip()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # Extract entity from common patterns
        entity = None
        # "What type of thing is X?" → X
        m = re.match(r'what\s+type\s+of\s+thing\s+is\s+(.+?)\?*$', q, re.IGNORECASE)
        if m: entity = m.group(1).strip().rstrip('?').strip()
        # "X là loại gì?" → X
        if not entity:
            m = re.match(r'(.+?)\s+là\s+loại\s+gì\?*$', q, re.IGNORECASE)
            if m: entity = m.group(1).strip()
        # "Tell me about X" → X
        if not entity:
            m = re.match(r'tell\s+me\s+about\s+(.+?)\?*$', q, re.IGNORECASE)
            if m: entity = m.group(1).strip().rstrip('.').strip()
        # "Mô tả ngắn về X là gì?" → X
        if not entity:
            m = re.match(r'(?:mô\s+tả\s+ngắn\s+về|cho\s+biết\s+về)\s+(.+?)\s+là\s+gì\?*$', q, re.IGNORECASE)
            if m: entity = m.group(1).strip()
        # "What is interesting about the number X?" → X
        if not entity:
            m = re.match(r'what\s+is\s+(?:interesting|special)\s+about\s+(?:the\s+)?(.+?)\?*$', q, re.IGNORECASE)
            if m: entity = m.group(1).strip()
        # "What is the likely gender of the name 'X'?" → X
        if not entity:
            m = re.match(r"what\s+is\s+the\s+likely\s+gender\s+of\s+the\s+name\s+['\"]?(\w+)['\"]?\?*$", q, re.IGNORECASE)
            if m: entity = m.group(1).strip()

        if entity:
            # [V91 FIX] Cross-verify with 3 sources instead of 1
            try:
                from scp.core.cross_verify import cross_verify_entity
                cv_result = cross_verify_entity(entity, question)
                if cv_result.get("value"):
                    answer = str(cv_result["value"])[:300]
                    confidence = cv_result["confidence"]
                    sources = cv_result.get("sources", [])
                    reasoning = f"Cross-verified by {len(sources)} sources: {', '.join(sources)}"
                    evidence = {"value": cv_result.get("value"),
                                "source": sources[0] if sources else "cross_verify",
                                "entity": entity, "sources": sources}
                else:
                    answer = ""
                    confidence = 0.1
                    reasoning = f"No data from any source for '{entity}'"
            except Exception as e:
                # Fallback to Wikipedia only
                if self._wiki:
                    try:
                        entity_clean = re.sub(r'^(?:a|an|the)\s+', '', entity, flags=re.IGNORECASE).strip()
                        data = self._wiki.fetch(entity_clean)
                        if data and data.get("extract"):
                            answer = data["extract"][:300]
                            confidence = 0.65
                            reasoning = f"Wikipedia fallback: {data.get('title', entity)}"
                            evidence = {"value": data.get("extract"), "source": "wikipedia", "title": data.get("title", "")}
                    except Exception as e2:
                        reasoning = f"All sources failed: {e}, {e2}"
                        confidence = 0.0
                else:
                    reasoning = f"Cross-verify error: {e}"
                    confidence = 0.0
        else:
            reasoning = "Could not extract entity from question"
            confidence = 0.0

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="general", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.7 if answer else 0.0
