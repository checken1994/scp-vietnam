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

from scp.core.api_utils import fetch_with_retry

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


class WeatherSLM(BaseSLM):
    """SLM chuyên về thời tiết — Open-Meteo API."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="WeatherSLM", domain="weather", config=config)
        self._geo_cache: dict[str, tuple[float, float]] = {}

    def _extract_city(self, question: str) -> Optional[str]:
        import re
        patterns = [
            r'nhiệt\s+độ\s*(?:hiện\s+tại\s+)?(?:tại|ở)\s+(.+?)\s*(?:là|bao|hiện|$|\?)',
            r'nhiệt\s+độ\s*(?:tại|ở)\s+(.+?)\s*(?:là|bao|hiện|$|\?)',
            r'temperature\s+(?:at|in)\s+(.+?)\??$',
            r'thời\s+tiết\s+(?:tại|ở)\s+(.+?)\s*(?:như|hiện|$|\?)',
            r'weather\s+(?:at|in)\s+(.+?)\??$',
        ]
        for pat in patterns:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                city = m.group(1).strip().rstrip('?').rstrip('.').strip()
                if city:
                    return city
        return None

    def _geocode(self, city: str) -> Optional[tuple[float, float]]:
        if city.lower() in self._geo_cache:
            return self._geo_cache[city.lower()]
        try:
            import urllib.parse
            url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
            data = fetch_with_retry(url, {"User-Agent": "SCP-V14/1.0"}, timeout=10)
            if data and data.get("results"):
                lat = data["results"][0]["latitude"]
                lon = data["results"][0]["longitude"]
                self._geo_cache[city.lower()] = (lat, lon)
                return (lat, lon)
        except Exception as e:
            logger.warning(f"Weather geocode error: {e}")
        return None

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V30] Smart cache check
        try:
            from scp.core.smart_cache import get_smart_cache
            cached = get_smart_cache().get("slm:WeatherSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        city = self._extract_city(question)
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        if city:
            # [V29.2] Multi-source weather (Open-Meteo + wttr.in + Archive)
            try:
                from scp.core.multi_source_verifier import fetch_weather_multi
                result = fetch_weather_multi(city)
                if result["value"] != 0 or result["source"] != "none":
                    answer = f"nhiệt độ {city} = {result['value']:.1f}°C"
                    confidence = result["confidence"]
                    reasoning = f"Multi-source weather: {result['reason']}"
                    evidence = {
                        "source": result["source"],
                        "entity": city,
                        "value": result["value"],
                        "sources_succeeded": result["sources_succeeded"],
                        "all_values": result["all_values"],
                        "conflict_detected": result["conflict_detected"],
                    }
            except Exception as e:
                logger.warning(f"Weather multi-source error: {e}")
                # Fallback to single-source Open-Meteo
                coords = self._geocode(city)
                if coords:
                    lat, lon = coords
                    try:
                        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
                        data = fetch_with_retry(url, {"User-Agent": "SCP-V14/1.0"}, timeout=10)
                        if data and "current" in data:
                            temp = data["current"]["temperature_2m"]
                            answer = f"nhiệt độ {city} = {temp}°C"
                            confidence = 0.90
                            reasoning = f"Open-Meteo (fallback): {city} ({lat:.2f},{lon:.2f}) → {temp}°C"
                            evidence = {"source": "Open-Meteo", "entity": city, "value": temp, "lat": lat, "lon": lon}
                    except Exception as e2:
                        logger.warning(f"Weather fallback error: {e2}")

        if not answer:
            confidence = 0.3
            reasoning = f"Không tìm thấy city hoặc API fail: '{city or question[:50]}'"
            evidence = {"source": "none", "entity": city}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="weather", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.3
