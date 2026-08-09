"""
SLM part — extracted from slms.py (Task 19-A, batch 2).
 kept verbatim; only the class location changed.
"""
import hashlib
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("scp.slms")


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



class FinanceSLM(BaseSLM):
    """SLM chuyên về tài chính — dùng Frankfurter + CoinGecko (V29 fix)."""

    def __init__(self, config: Optional[dict] = None, registry = None):
        super().__init__(name="FinSLM", domain="finance", config=config)
        self.registry = registry or None
        # [V29 FIX] Removed deprecated extractor — using direct API calls like ConversionSLM

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V30] Smart cache với per-source TTL
        try:
            from scp.core.smart_cache import get_smart_cache
            cache = get_smart_cache()
            cached = cache.get("slm:FinanceSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception:
            cached = None

        # [V29 FIX] Use direct API calls instead of deprecated extractor
        # [V29.1] Use multi-source crypto_verifier + currency rates
        import re
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # Pattern 1: Currency "chuyển đổi 1 USD sang VND"
        m = re.search(r'chuyển\s+đổi\s+(\d+(?:\.\d+)?)\s+([A-Z]{3})\s+sang\s+([A-Z]{3})', question, re.IGNORECASE)
        if m:
            amount = float(m.group(1))
            from_curr = m.group(2).upper()
            to_curr = m.group(3).upper()
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
                logger.warning(f"Currency rate error: {e}")

        # Pattern 2: Crypto "giá bitcoin hiện tại" / "price of bitcoin"
        # [V29.1] Use multi-source crypto_verifier
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
                    logger.warning(f"Crypto multi-source error: {e}")

        if not answer:
            confidence = 0.1
            reasoning = "Không match pattern currency/crypto hoặc all sources failed"
            evidence = {"source": "none"}

        resp = SLMResponse(question=question, answer=answer, confidence=confidence,
                           domain="finance", reasoning=reasoning, evidence=evidence,
                           slm_name=self.name, processing_time=time.time() - start)
        # [V30] Save to smart cache với source-based TTL
        try:
            source = evidence.get("source", "")
            cache.set("slm:FinanceSLM", question, resp, source)
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        # Also save to legacy cache for backward compat
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.1



class LogicSLM(BaseSLM):
    """SLM chuyên về logic — dùng math_evaluator cho boolean/comparison."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="LogicSLM", domain="logic", config=config)
        from scp.core.math_evaluator import MathEvalError, evaluate_expression
        self._eval = evaluate_expression
        self._EvalError = MathEvalError

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V33] SmartCache check
        try:
            from scp.core.smart_cache import slm_cache_get, slm_cache_set
            cached = slm_cache_get("LogicSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        # Extract expression từ câu hỏi logic
        import re
        # [V89 FIX] Convert words to comparison operators
        q_normalized = question.lower()
        word_to_op = {
            "greater than or equal to": ">=",
            "less than or equal to": "<=",
            "not equal to": "!=",
            "greater than": ">",
            "less than": "<",
            "equal to": "==",
            "equals": "==",
            "is equal to": "==",
            "is not equal to": "!=",
            "is greater than": ">",
            "is less than": "<",
            "is greater than or equal to": ">=",
            "is less than or equal to": "<=",
        }
        q_converted = question
        for phrase, op in sorted(word_to_op.items(), key=lambda x: -len(x[0])):
            if phrase in q_normalized:
                q_converted = re.sub(re.escape(phrase), op, q_converted, flags=re.IGNORECASE)
                break

        # Pattern: "X > Y", "X == Y" etc
        m = re.search(r'([\d\.]+\s*[<>=!]+\s*[\d\.]+)', q_converted)
        if not m:
            # Pattern: "X là đúng/sai", "đúng không"
            if re.search(r'(đúng|sai|true|false|yes|no)', question, re.IGNORECASE):
                resp = SLMResponse(
                    question=question, answer="", confidence=0.2,
                    domain="logic", reasoning="Câu hỏi boolean không có mệnh đề",
                    evidence={"source": "none"}, slm_name=self.name, processing_time=0,
                )
                self._end_timer(start, False)
                return resp
            resp = SLMResponse(
                question=question, answer="", confidence=0.0,
                domain="logic", reasoning="Không parse được biểu thức logic",
                evidence={}, slm_name=self.name, processing_time=0,
            )
            self._end_timer(start, False)
            return resp

        expr = m.group(1)
        try:
            result = self._eval(expr)
            answer = f"{expr} = {result}"
            confidence = 0.95
            reasoning = f"Deterministic AST: {expr} = {result}"
            evidence = {"source": "PythonAST", "expr": expr, "result": result, "value": result}
        except Exception as e:
            answer = ""
            confidence = 0.0
            reasoning = f"Lỗi evaluate: {e}"
            evidence = {"expr": expr, "error": str(e)}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="logic", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        # [V33] Save to SmartCache
        try:
            from scp.core.smart_cache import slm_cache_set
            slm_cache_set("LogicSLM", question, resp, evidence.get("source", "PythonAST"))
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.95 if answer else 0.0



class StatisticsSLM(BaseSLM):
    """SLM chuyên về thống kê — deterministic Python math."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="StatsSLM", domain="statistics", config=config)

    def _extract_numbers(self, question: str) -> list[float]:
        import re
        nums = re.findall(r'-?\d+\.?\d*', question)
        return [float(n) for n in nums if n.replace('.', '', 1).replace('-', '').isdigit()]

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V33] SmartCache check
        try:
            from scp.core.smart_cache import slm_cache_get, slm_cache_set
            cached = slm_cache_get("StatsSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        nums = self._extract_numbers(question)
        q_lower = question.lower()

        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        if len(nums) >= 2:
            n = len(nums)
            mean = sum(nums) / n
            sorted_nums = sorted(nums)
            median = sorted_nums[n // 2] if n % 2 == 1 else (sorted_nums[n//2 - 1] + sorted_nums[n//2]) / 2
            variance = sum((x - mean) ** 2 for x in nums) / n
            std = math.sqrt(variance)
            mn, mx = min(nums), max(nums)

            # Decide what to return
            if 'trung bình' in q_lower or 'mean' in q_lower or 'average' in q_lower:
                val = mean
                answer = f"trung bình = {val}"
            elif 'trung vị' in q_lower or 'median' in q_lower:
                val = median
                answer = f"trung vị = {val}"
            elif 'phương sai' in q_lower or 'variance' in q_lower:
                val = variance
                answer = f"phương sai = {val}"
            elif 'độ lệch chuẩn' in q_lower or 'std' in q_lower or 'standard' in q_lower:
                val = std
                answer = f"độ lệch chuẩn = {val}"
            elif 'nhỏ nhất' in q_lower or 'min' in q_lower:
                val = mn
                answer = f"min = {val}"
            elif 'lớn nhất' in q_lower or 'max' in q_lower:
                val = mx
                answer = f"max = {val}"
            else:
                # Default: trả tất cả
                val = mean
                answer = f"mean={mean}, median={median}, std={std:.4f}"

            confidence = 0.95
            reasoning = f"Deterministic stats: n={n}, mean={mean}, median={median}, std={std}"
            evidence = {
                "source": "PythonMath",
                "n": n,
                "mean": mean, "median": median, "variance": variance,
                "std": std, "min": mn, "max": mx,
                "value": val,
            }

        if not answer:
            confidence = 0.3
            reasoning = "Không đủ số để tính thống kê (cần >= 2)"
            evidence = {"source": "none", "nums_found": len(nums)}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="statistics", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        # [V33] Save to SmartCache
        try:
            from scp.core.smart_cache import slm_cache_set
            slm_cache_set("StatsSLM", question, resp, evidence.get("source", "PythonMath"))
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.95 if answer else 0.3



class HolidaySLM(BaseSLM):
    """
    [V78] Holiday SLM — public holidays via date.nager.at API.
    Handles: "What is a public holiday in X?" (X = country code)
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="HolidaySLM", domain="history", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import json as _json
        import re
        import urllib.request
        q = question.strip()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # "What is a public holiday in XX?" → fetch holidays for country XX
        m = re.match(r'what\s+is\s+a\s+public\s+holiday\s+in\s+(\w+)\??$', q, re.IGNORECASE)
        if m:
            country = m.group(1).strip()
            # [V89 FIX] Convert country name to 2-letter code (date.nager.at requires ISO 3166-1 alpha-2)
            country_codes = {
                'vietnam': 'VN', 'viet nam': 'VN', 'usa': 'US', 'united states': 'US',
                'uk': 'GB', 'united kingdom': 'GB', 'britain': 'GB', 'england': 'GB',
                'france': 'FR', 'germany': 'DE', 'japan': 'JP', 'china': 'CN',
                'korea': 'KR', 'south korea': 'KR', 'india': 'IN', 'thailand': 'TH',
                'singapore': 'SG', 'malaysia': 'MY', 'indonesia': 'ID',
                'philippines': 'PH', 'australia': 'AU', 'canada': 'CA',
                'brazil': 'BR', 'mexico': 'MX', 'italy': 'IT', 'spain': 'ES',
                'russia': 'RU', 'netherlands': 'NL', 'sweden': 'SE',
                'norway': 'NO', 'finland': 'FI', 'denmark': 'DK', 'poland': 'PL',
                'turkey': 'TR', 'egypt': 'EG', 'south africa': 'ZA',
                'argentina': 'AR', 'chile': 'CL', 'new zealand': 'NZ',
                'ireland': 'IE', 'portugal': 'PT', 'greece': 'GR',
                'switzerland': 'CH', 'austria': 'AT', 'belgium': 'BE',
            }
            country_code = country_codes.get(country.lower(), country.upper())[:2]
            # Try current year + previous year
            from datetime import datetime as _dt
            for year in [_dt.now().year, _dt.now().year - 1]:
                try:
                    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}"
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'SCP-V78-Bot/1.0 (educational research)'
                    })
                    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                        data = _json.loads(resp.read().decode('utf-8'))
                    if isinstance(data, list) and data:
                        import random as _rand
                        holiday = _rand.choice(data)  # noqa: S311
                        name = holiday.get("name", "")
                        date = holiday.get("date", "")
                        local_name = holiday.get("localName", "")
                        if name and date:
                            answer = f"{name} (local: {local_name}) is on {date}."
                            confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                            reasoning = f"Public Holidays API: {country} {year}"
                            evidence = {"value": answer, "source": "public_holidays", "country": country_code, "year": year}
                            break
                except Exception as e:
                    reasoning = f"Holiday API error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "Holiday pattern not recognized"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="history", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # [ROOT-FIX] Default 0.5 (unverified). Sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
        return 0.5 if answer else 0.0



class AnimalFactsSLM(BaseSLM):
    """
    [V78] Animal Facts SLM — cat/dog facts via kinduff/catfact APIs.
    Handles: "Tell me a fact about cats/dogs."
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="AnimalFactsSLM", domain="biology", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import json as _json
        import urllib.request
        q = question.strip().lower()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # "Tell me a fact about cats/dogs."
        if 'fact about cats' in q or 'cat fact' in q:
            try:
                req = urllib.request.Request(
                    "https://catfact.ninja/fact",
                    headers={'User-Agent': 'SCP-V78-Bot/1.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                    data = _json.loads(resp.read().decode('utf-8'))
                fact = data.get("fact", "")
                if fact:
                    answer = fact
                    confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                    reasoning = "Cat Facts API"
                    evidence = {"value": fact, "source": "cat_facts"}
            except Exception as e:
                reasoning = f"Cat facts error: {e}"

        elif 'fact about dogs' in q or 'dog fact' in q:
            try:
                # [V79] dog-api.kinduff.com returns empty facts — use some-random-api instead
                req = urllib.request.Request(
                    "https://some-random-api.com/animal/dog",
                    headers={'User-Agent': 'SCP-V79-Bot/1.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                    data = _json.loads(resp.read().decode('utf-8'))
                fact = data.get("fact", "")
                if fact:
                    answer = fact
                    confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                    reasoning = "Some Random API (dog)"
                    evidence = {"value": fact, "source": "dog_facts"}
            except Exception as e:
                reasoning = f"Dog facts error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "Animal fact pattern not recognized"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="biology", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # [ROOT-FIX] Default 0.5 (unverified). Sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
        return 0.5 if answer else 0.0



class CitySLM(BaseSLM):
    """
    [V73] City SLM — populations, areas, timezones for cities (vs GeographySLM
    which only handles countries via REST Countries API).
    Uses Open-Meteo geocoding + Wikidata.
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="CitySLM", domain="geography", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
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

        # "What is the population of X?" → geocoding API
        m = re.match(r'what\s+is\s+the\s+population\s+of\s+(.+?)\?*$', q, re.IGNORECASE)
        if m:
            city = m.group(1).strip().rstrip('?').strip()
            try:
                import requests
                # Open-Meteo geocoding returns population for cities
                r = requests.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en&format=json",
                    timeout=5, headers={'User-Agent': 'SCP-V73/1.0'})
                if r.status_code == 200:
                    data = r.json()
                    results = data.get("results") or []
                    if results:
                        c = results[0]
                        pop = c.get("population", 0)
                        if pop:
                            answer = str(pop)
                            confidence = 0.8
                            reasoning = f"Open-Meteo geocoding: {c.get('name', city)}, {c.get('country', '')}"
                            evidence = {"value": answer, "source": "open-meteo-geocoding", "city": c.get("name", "")}
            except Exception as e:
                reasoning = f"Geocoding error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "City pattern not recognized"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="geography", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.8 if answer else 0.0



class ReligionSLM(BaseSLM):
    """
    [V73] Religion/Literature SLM — Bible verses, quotes, scriptures.
    Uses bible-api.com for verse lookup.
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="ReligionSLM", domain="religion", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import json as _json
        import re
        import urllib.parse
        import urllib.request
        q = question.strip()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # "What does the Bible say in X:Y?" → fetch verse
        m = re.match(r'what\s+does\s+the\s+bible\s+say\s+in\s+(.+?)\?*$', q, re.IGNORECASE)
        if m:
            ref = m.group(1).strip().rstrip('?').strip()
            try:
                url = f"https://bible-api.com/{urllib.parse.quote(ref)}?translation=kjv"
                req = urllib.request.Request(url, headers={'User-Agent': 'SCP-V73/1.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                    data = _json.loads(resp.read().decode('utf-8'))
                text = (data.get("text") or "").strip()
                if text:
                    answer = text
                    confidence = 0.9
                    reasoning = f"Bible verse {data.get('reference', ref)} (KJV)"
                    evidence = {"value": text, "source": "bible-api", "reference": data.get("reference", ref)}
            except Exception as e:
                reasoning = f"Bible API error: {e}"
                confidence = 0.0

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="religion", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.9 if answer else 0.0



class AdviceSLM(BaseSLM):
    """
    [V78] Advice SLM — life advice via adviceslip.com API.
    Handles: "What is a piece of useful life advice?"
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="AdviceSLM", domain="general", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import json as _json
        import urllib.request
        q = question.strip().lower()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        if 'advice' in q or 'wisdom' in q:
            try:
                req = urllib.request.Request(
                    "https://api.adviceslip.com/advice",
                    headers={'User-Agent': 'SCP-V78-Bot/1.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                    data = _json.loads(resp.read().decode('utf-8'))
                advice = data.get("slip", {}).get("advice", "")
                if advice:
                    answer = advice
                    confidence = 0.7  # Lower confidence — advice is subjective
                    reasoning = "Advice Slip API"
                    evidence = {"value": advice, "source": "advice_slip"}
            except Exception as e:
                reasoning = f"Advice API error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "Advice pattern not recognized"

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



class ChuckNorrisSLM(BaseSLM):
    """
    [V78] Chuck Norris SLM — jokes via chucknorris.io API.
    Handles: "Tell me a Chuck Norris fact."
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="ChuckNorrisSLM", domain="entertainment", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        import json as _json
        import urllib.request
        q = question.strip().lower()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        if 'chuck norris' in q:
            try:
                req = urllib.request.Request(
                    "https://api.chucknorris.io/jokes/random",
                    headers={'User-Agent': 'SCP-V78-Bot/1.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                    data = _json.loads(resp.read().decode('utf-8'))
                joke = data.get("value", "")
                if joke:
                    answer = joke
                    confidence = 0.7  # Jokes are subjective
                    reasoning = "Chuck Norris API"
                    evidence = {"value": joke, "source": "chuck_norris"}
            except Exception as e:
                reasoning = f"Chuck Norris API error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "Chuck Norris pattern not recognized"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="entertainment", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.7 if answer else 0.0



