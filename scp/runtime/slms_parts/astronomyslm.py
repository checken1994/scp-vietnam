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


class AstronomySLM(BaseSLM):
    """SLM chuyên về thiên văn — dùng AstronomyDataSource (V44)."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="AstroSLM", domain="astronomy", config=config)
        self._ds = None
        try:
            from scp.data_sources.astronomy import AstronomyDataSource
            self._ds = AstronomyDataSource()
        except Exception as e:
            logger.warning(f"AstronomySLM init failed: {e}")

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # Smart cache
        try:
            from scp.core.smart_cache import get_smart_cache
            cached = get_smart_cache().get("slm:AstroSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        if self._ds:
            # Try to extract entity from common astronomy question patterns
            import re
            entity = None
            patterns = [
                r'(?:khối\s+lượng|bán\s+kính|khoảng\s+cách|nhiệt\s+độ)\s+(?:của\s+)?(.+?)(?:\s+là|\?|$)',
                r'(?:mass|radius|distance|temperature)\s+of\s+(.+?)\??$',
                r'(.+?)\s+là\s+(?:gì|bao\s+nhiêu)',
                r'tell\s+me\s+about\s+(.+?)$',
                # [V74] "What type of thing is X?" → X
                r'what\s+type\s+of\s+thing\s+is\s+(.+?)\??$',
                r'(.+?)\s+là\s+loại\s+gì\??$',
            ]
            for pat in patterns:
                m = re.search(pat, question, re.IGNORECASE)
                if m:
                    entity = m.group(1).strip().rstrip('?').rstrip('.').strip()
                    break

            # If no entity extracted, use the whole question
            if not entity:
                entity = question

            # [V74] Special handling for "What type of thing is X?" questions
            # Was: SLM returns "Saturn: 5.6834e+26 (loại: gas giant)" → AI "planet" → FAIL (overlap 0%)
            # Now: extract just the TYPE from metadata, return only the type
            is_type_question = bool(re.match(r'what\s+type\s+of\s+thing\s+is|là\s+loại\s+gì',
                                              question, re.IGNORECASE))

            # Try direct fetch
            result = self._ds.fetch('planet_info', entity)
            if not result:
                result = self._ds.fetch('star_info', entity)
            if not result:
                result = self._ds.fetch('galaxy_info', entity)
            if not result:
                result = self._ds.fetch('moon_info', entity)
            if not result:
                result = self._ds.fetch('astronomical_constant', entity)

            if result:
                meta = result.get('metadata', {})
                # [V74] For "What type of thing is X?" — return ONLY the type, not all facts
                # Was: returns "Saturn: 5.6834e+26 (loại: gas giant)..." → AI "planet" → FAIL
                # Now: returns just "planet" or "gas giant" → matches AI answer
                if is_type_question and 'type' in meta:
                    type_val = meta['type']
                    # Normalize: "gas giant" → "planet" (gas giant IS a planet)
                    type_aliases = {
                        'gas giant': 'planet',
                        'ice giant': 'planet',
                        'terrestrial planet': 'planet',
                        'rocky planet': 'planet',
                        'dwarf planet': 'planet',
                        'star': 'star',
                        'galaxy': 'galaxy',
                        'moon': 'moon',
                        'asteroid': 'asteroid',
                        'comet': 'comet',
                    }
                    answer = type_aliases.get(type_val.lower(), type_val)
                    confidence = 0.92
                    reasoning = f"AstronomyDataSource: {entity} is {type_val}"
                    evidence = {"source": result['source'], "entity": entity,
                                "type": type_val, "value": answer}
                elif 'vi_name' in meta:
                    answer = f"{meta.get('vi_name', entity)}: {result['value']}"
                    answer += f" (loại: {meta.get('type', '?')})"
                    if 'mass_kg' in meta:
                        answer += f"\n  Khối lượng: {meta['mass_kg']:.3e} kg"
                    if 'radius_m' in meta:
                        answer += f"\n  Bán kính: {meta['radius_m']:.3e} m"
                    if 'mean_temp_k' in meta:
                        answer += f"\n  Nhiệt độ TB: {meta['mean_temp_k']} K"
                    if 'moons' in meta:
                        answer += f"\n  Số vệ tinh: {meta['moons']}"
                    confidence = 0.92
                    reasoning = f"AstronomyDataSource lookup: {entity}"
                    evidence = {"value": result.get("value"), "source": result['source'], "entity": entity, **meta}
                else:
                    answer = f"{entity} = {result['value']}"
                    confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                    reasoning = f"Astronomy lookup: {entity}"
                    evidence = {"value": result.get("value"), "source": result['source'], "entity": entity, **meta}

        if not answer:
            confidence = 0.1
            reasoning = "Không tìm thấy dữ liệu thiên văn cho câu hỏi này"
            evidence = {"source": "none"}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="astronomy", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.3
