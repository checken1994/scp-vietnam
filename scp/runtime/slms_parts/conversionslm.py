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

    def __init__(self, name: str, domain: str, config: dict = None):
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


class ConversionSLM(BaseSLM):
    """SLM chuyên về currency conversion + crypto price."""

    def __init__(self, config: dict = None):
        super().__init__(name="ConvSLM", domain="conversion", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V30] Smart cache check
        try:
            from scp.core.smart_cache import get_smart_cache
            cached = get_smart_cache().get("slm:ConvSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        # [V29.1] Multi-source crypto + currency (cross-validation)
        # [V51] Unit conversion (km→m, mile→km, etc.) using ConversionDataSource
        import re
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # [V51-V52] Pattern 0: Unit conversion "1 km bằng bao nhiêu m?" / "1 km = ? m"
        # [V52 FIX] Handle Vietnamese diacritics by normalizing before regex
        # [V53 FIX] Sort units by length DESCENDING to prevent 'm' matching before 'mg', 'cm' before 'm', etc.
        # [V54 FIX] Add missing units: yard, pound, ounce, stone, ton, hectare, floz, etc.
        # [V58 FIX] Add missing units: atm, pascals, pa, bar, psi, floz, tablespoon, teaspoon
        units_list = ['nautical mile', 'horsepower', 'hectare', 'minute', 'second', 'knot', 'mach',
                      'mile', 'inch', 'foot', 'hour', 'gallon', 'acre', 'watt', 'joule', 'kwh', 'btu',
                      'yard', 'pound', 'ounce', 'stone', 'ton', 'quart', 'pint', 'cup',
                      'pascals', 'tablespoon', 'teaspoon',
                      'km', 'cm', 'mm', 'mg', 'kg', 'lb', 'ft', 'cal', 'day', 'year', 'liter',
                      'atm', 'bar', 'psi', 'floz',
                      'm', 'g', 'l', 'pa']  # short units LAST to avoid greedy matching
        units_alt = r'(?:' + '|'.join(units_list) + r')'
        # [V52.1] Normalize Vietnamese diacritics for matching
        import unicodedata
        q_norm = unicodedata.normalize('NFD', question)
        q_norm = ''.join(c for c in q_norm if unicodedata.category(c) != 'Mn')
        # q_norm now has "1 km bang bao nhieu m?" (no diacritics)
        unit_m = re.search(
            rf'(\d+(?:\.\d+)?)\s*({units_alt})\s*(?:bang\s+bao\s+nhieu|=|sang|to|is)\s*\??\s*({units_alt})?',
            q_norm, re.IGNORECASE
        )
        if not unit_m:
            # Try direct pattern on original question
            unit_m = re.search(
                rf'(\d+(?:\.\d+)?)\s*({units_alt})\s*(?:=|sang|to)\s*({units_alt})',
                question, re.IGNORECASE
            )
        if unit_m:
            try:
                from scp.data_sources.conversion import ConversionDataSource
                cds = ConversionDataSource()
                amount = float(unit_m.group(1))
                from_unit = unit_m.group(2).lower()
                to_unit = (unit_m.group(3) or '').lower().rstrip('?').strip()
                # If to_unit is empty, try to infer from question context
                if not to_unit:
                    # Look for unit at end of question
                    tail_match = re.search(rf'({units_alt})\s*\??$', question, re.IGNORECASE)
                    if tail_match:
                        to_unit = tail_match.group(1).lower()
                # Try to get factor for from_unit
                result = cds.fetch('length_conversion', from_unit) if from_unit in cds._length_to_m else \
                         cds.fetch('mass_conversion', from_unit) if from_unit in cds._mass_to_kg else \
                         cds.fetch('time_conversion', from_unit) if from_unit in cds._time_to_s else \
                         cds.fetch('speed_conversion', from_unit) if from_unit in cds._speed_to_ms else \
                         cds.fetch('area_conversion', from_unit) if from_unit in cds._area_to_m2 else \
                         cds.fetch('volume_conversion', from_unit) if from_unit in cds._volume_to_m3 else \
                         cds.fetch('energy_conversion', from_unit) if from_unit in cds._energy_to_j else \
                         cds.fetch('power_conversion', from_unit) if from_unit in cds._power_to_w else \
                         cds.fetch('pressure_conversion', from_unit) if hasattr(cds, '_pressure_to_pa') and from_unit in cds._pressure_to_pa else None
                if result and to_unit:
                    # Get factor for to_unit
                    to_result = None
                    for intent in ['length_conversion', 'mass_conversion', 'time_conversion',
                                   'speed_conversion', 'area_conversion', 'volume_conversion',
                                   'energy_conversion', 'power_conversion', 'pressure_conversion']:
                        r2 = cds.fetch(intent, to_unit)
                        if r2:
                            to_result = r2
                            break
                    if to_result:
                        from_factor = result['value']
                        to_factor = to_result['value']
                        converted = amount * from_factor / to_factor
                        answer = f"{amount} {from_unit} = {converted} {to_unit}"
                        confidence = 0.95
                        reasoning = f"Unit conversion: {from_unit}→{to_unit} (factor {from_factor}/{to_factor})"
                        evidence = {
                            "source": "Local Conversion Database",
                            "amount": amount, "from": from_unit, "to": to_unit,
                            "from_factor": from_factor, "to_factor": to_factor,
                            "value": converted,
                        }
            except Exception as e:
                logger.debug(f"Unit conversion error: {e}")

        # Pattern 1: Currency conversion
        # [V89 FIX] Add English patterns: "How much is 100 USD in VND?" / "convert 100 USD to VND"
        if not answer:
            m = re.search(r'(?:how\s+much\s+(?:is|are)\s+)?(\d+(?:\.\d+)?)\s+([A-Z]{3})\s+(?:in|to|sang)', question, re.IGNORECASE)
            if not m:
                m = re.search(r'convert\s+(\d+(?:\.\d+)?)\s+([A-Z]{3})\s+(?:to|in|sang)\s+([A-Z]{3})', question, re.IGNORECASE)
            if not m:
                m = re.search(r'chuyển\s+đổi\s+(\d+(?:\.\d+)?)\s+([A-Z]{3})\s+sang\s+([A-Z]{3})', question, re.IGNORECASE)
            if m:
                amount = float(m.group(1))
                from_curr = m.group(2).upper()
                # [V89 FIX] Extract to_curr — may be in group(3) or need to search after the match
                to_curr = ""
                try:
                    to_curr = m.group(3).upper()
                except (IndexError, AttributeError) as e:
                    logger.warning(f"Silent except: {e}")
                if not to_curr:
                    # Search for 3-letter currency code after the preposition
                    tail = question[m.end():]
                    curr_match = re.search(r'\b([A-Z]{3})\b', tail, re.IGNORECASE)
                    if curr_match:
                        to_curr = curr_match.group(1).upper()
                if not to_curr:
                    to_curr = "USD"  # fallback
                try:
                    from scp.core.crypto_verifier import fetch_currency_rate
                    rate_result = fetch_currency_rate(from_curr, to_curr)
                    if rate_result["value"] > 0:
                        val = amount * rate_result["value"]
                        answer = f"{amount} {from_curr} = {val} {to_curr}"
                        confidence = rate_result["confidence"]
                        reasoning = f"Multi-source currency ({rate_result['source']}): 1 {from_curr} = {rate_result['value']} {to_curr}"
                        evidence = {
                            "source": rate_result["source"],
                            "amount": amount, "from": from_curr, "to": to_curr,
                            "rate": rate_result["value"], "value": val,
                            "sources_succeeded": rate_result["sources_succeeded"],
                            "all_values": rate_result["all_values"],
                        }
                except Exception as e:
                    logger.warning(f"Conversion currency error: {e}")

        # Pattern 2: Crypto "giá bitcoin hiện tại" / "price of X"
        # [V29.1] Multi-source crypto
        if not answer:
            m = re.search(r'giá\s+(\w+)\s+hiện\s+tại', question, re.IGNORECASE)
            if not m:
                m = re.search(r'price\s+of\s+(\w+)', question, re.IGNORECASE)
            if m:
                coin = m.group(1).lower()
                try:
                    from scp.core.crypto_verifier import fetch_crypto_price
                    result = fetch_crypto_price(coin)
                    if result.value > 0:
                        answer = f"giá {coin} = {result.value} USD"
                        confidence = result.confidence
                        reasoning = f"Multi-source crypto: {result.reason[:120]}"
                        evidence = {
                            "source": result.source,
                            "coin": coin,
                            "value": result.value,
                            "sources_succeeded": result.sources_succeeded,
                            "sources_failed": result.sources_failed,
                            "all_values": result.all_values,
                            "conflict_detected": result.conflict_detected,
                        }
                except Exception as e:
                    logger.warning(f"Conversion crypto error: {e}")

        if not answer:
            confidence = 0.1
            reasoning = "Không match pattern currency/crypto hoặc all sources failed"
            evidence = {"source": "none"}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="conversion", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.3
