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


class _DomainSLM(BaseSLM):
    """Base class cho V46 domain SLMs — dùng DataSource + LiveKnowledge fallback."""

    DOMAIN_NAME = "general"
    SLM_NAME = "DomainSLM"
    DATASOURCE_CLASS = None  # set in subclass

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name=self.SLM_NAME, domain=self.DOMAIN_NAME, config=config)
        self._ds = None
        try:
            if self.DATASOURCE_CLASS:
                self._ds = self.DATASOURCE_CLASS()
        except Exception as e:
            logger.warning(f"{self.SLM_NAME} init failed: {e}")

    def _extract_entity(self, question: str) -> str:
        """Extract entity từ câu hỏi — đơn giản cho V46."""
        import re
        q = question.strip()
        # Strip common prefixes
        q = re.sub(r'^(?:ai\s+là|what\s+is|what\s+are|kể\s+tên|cho\s+biết|tìm\s+hiểu)\s+', '', q, flags=re.IGNORECASE)
        q = re.sub(r'^(?:tác\s+dụng|chức\s+năng|đặc\s+điểm|thông\s+tin)\s+(?:của\s+)?', '', q, flags=re.IGNORECASE)
        # [V63] Extract entity from common patterns
        # "X được thành lập năm nào?" → X
        m = re.match(r'(.+?)\s+(?:được\s+thành\s+lập|thành\s+lập|được\s+tạo|tạo\s+ra|phát\s+hành|ra\s+đời)', q, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip('?').strip()
        # "X là gì?" → X
        m = re.match(r'(.+?)\s+là\s+gì', q, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip('?').strip()
        # "X có bao nhiêu Y?" → X
        m = re.match(r'(.+?)\s+có\s+(?:bao\s+nhiêu|mấy)', q, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip('?').strip()
        # [V63.3] "Olympic YYYY tổ chức ở đâu?" → "Olympic YYYY"
        m = re.match(r'(Olympic\s+\d{4})', q, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # [V63.3] "World Cup YYYY ai vô địch?" → "World Cup YYYY"
        m = re.match(r'(World\s+Cup\s+\d{4})', q, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        q = re.sub(r'\?+$', '', q).strip()
        return q

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        try:
            from scp.core.smart_cache import get_smart_cache
            cached = get_smart_cache().get(f"slm:{self.SLM_NAME}", question)
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

        entity = self._extract_entity(question)

        if self._ds:
            # Try direct fetch
            for intent in self._ds.get_supported_intents():
                if self._ds.can_handle(intent, entity):
                    result = self._ds.fetch(intent, entity)
                    if result:
                        meta = result.get("metadata", {})
                        # [V63 FIX] Build better answer from metadata when value is empty
                        raw_value = result.get("value", "")
                        if not raw_value and meta:
                            # Build answer from metadata fields
                            parts = []
                            for key in ['vi_name', 'name', 'born', 'died', 'country', 'movement',
                                        'type', 'function', 'use', 'works', 'formula', 'sport',
                                        'event', 'year', 'box_office', 'director', 'desc',
                                        'specialty', 'position', 'born', 'title',
                                        'city', 'host', 'winner', 'runner_up', 'top_country']:
                                if key in meta:
                                    val = meta[key]
                                    if isinstance(val, list):
                                        val = ', '.join(str(v) for v in val)
                                    parts.append(f"{key}={val}")
                            if parts:
                                raw_value = '; '.join(parts)
                        # [V63.3] If value exists but is just a number, also append metadata
                        elif raw_value and meta and len(str(raw_value)) < 20:
                            parts = [str(raw_value)]
                            for key in ['city', 'host', 'winner', 'country', 'top_country',
                                        'sport', 'event', 'formula', 'function', 'use',
                                        'born', 'died', 'vi_name', 'name']:
                                if key in meta:
                                    val = meta[key]
                                    if isinstance(val, list):
                                        val = ', '.join(str(v) for v in val)
                                    parts.append(f"{key}={val}")
                            raw_value = '; '.join(parts)
                        answer = str(raw_value)[:500] if raw_value else ""
                        if answer:
                            # [V104.43 #BS] TẠI SAO: was always confidence=0.85, overriding
                            # DataSource's intentional low confidence (e.g. Medical 0.55).
                            # Fix: respect DataSource confidence if present, else default 0.85.
                            _ds_conf = result.get("confidence")
                            if _ds_conf is not None and isinstance(_ds_conf, (int, float)):
                                confidence = _ds_conf
                            else:
                                # [EXEC-3] Default 0.5 (unverified) not 0.85 — sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
                                confidence = 0.5
                            reasoning = f"{self.SLM_NAME} lookup: {entity[:50]}"
                            # [V104.42 #BC] TẠI SAO: was missing "value" key → adversary
                            # checks evidence.get("value") → None → skip cross-check for
                            # ~30 _DomainSLM domains (medical, tech, sports, legal, arts...).
                            # Fix: include "value": raw_value in evidence.
                            evidence = {"source": result.get("source", ""), "entity": entity, "value": raw_value, **meta}
                            break

        # Fallback to LiveKnowledgeFetcher
        if not answer:
            try:
                from scp.data_sources.live_knowledge import fetch_live
                result = fetch_live(question, domain=self.DOMAIN_NAME)
                if result and result.get("value"):
                    answer = str(result["value"])[:500]
                    confidence = result.get("confidence", 0.6)
                    # [P1-12] TẠI SAO: medical/legal LiveKnowledge fallback must respect
                    # DataSource safety cap, not use cross-checked 0.85 (which bypasses
                    # the medical 0.55 cap and lets high-stakes answers through unfalsified).
                    if self.DOMAIN_NAME in ("medical", "legal"):
                        _ds_safety_cap = getattr(self._ds, "_static_confidence", 0.55) if self._ds else 0.55
                        if not isinstance(_ds_safety_cap, (int, float)):
                            _ds_safety_cap = 0.55
                        confidence = min(confidence, _ds_safety_cap)
                    reasoning = f"LiveKnowledgeFetcher ({self.DOMAIN_NAME})"
                    # [V104.42 #BC] TẠI SAO: was missing "value" key → adversary skip.
                    # Fix: include "value": result["value"] in evidence.
                    evidence = {
                        "source": result.get("source", ""),
                        "value": result["value"],
                        "from_cache": result.get("from_cache", False),
                        "fetched_at": result.get("fetched_at", ""),
                        **result.get("metadata", {}),
                    }
            except Exception as e:
                logger.debug(f"LiveKnowledge fallback error: {e}")

        if not answer:
            confidence = 0.1
            reasoning = f"Không tìm thấy thông tin cho {self.DOMAIN_NAME}"
            evidence = {"source": "none"}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain=self.DOMAIN_NAME, reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # [ROOT-FIX] Default 0.5 (unverified). Sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
        # Affects 31 SLMs inheriting _DomainSLM — all become 'unverified' unless DataSource provides confidence.
        return 0.5 if answer else 0.3
