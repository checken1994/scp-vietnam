"""
SLM implementations extracted from runtime/slms.py for modularity.

[Task 17-C] Split out from slms.py to keep individual modules under 1000 LOC
while preserving backward compatibility (slms.py re-exports everything).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

from scp.core.api_utils import fetch_with_retry
from scp.runtime.slm_base import BaseSLM, SLMResponse

logger = logging.getLogger("scp.slms")

# ============================================================
# Finance / Weather / Logic / Statistics SLMs
# ============================================================

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
        except Exception:
            logger.exception("[slms.py:604] silenced exception")
        # Also save to legacy cache for backward compat
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.1


class WeatherSLM(BaseSLM):
    """SLM chuyên về thời tiết — Open-Meteo API."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="WeatherSLM", domain="weather", config=config)
        self._geo_cache: dict[str, tuple[float, float]] = {}

    def _extract_city(self, question: str) -> str | None:
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

    def _geocode(self, city: str) -> tuple[float, float] | None:
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
        except Exception:
            logger.exception("[slms.py:1620] silenced exception")

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


# ============================================================
# LOGIC SLM — [v28] Boolean/comparison (deterministic)
# ============================================================
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
        except Exception:
            logger.exception("[slms.py:1708] silenced exception")

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
        except Exception:
            logger.exception("[slms.py:1784] silenced exception")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.95 if answer else 0.0


# ============================================================
# STATISTICS SLM — [v28] Mean/median/variance/std (deterministic)
# ============================================================
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
        except Exception:
            logger.exception("[slms.py:1816] silenced exception")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        nums = self._extract_numbers(question)
        question.lower()

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

            # [Z.ai-ROOT-FIX #10] Synonym-aware matching for stats operations
            if self._keyword_match(question, ['trung bình', 'mean', 'average']):
                val = mean
                answer = f"trung bình = {val}"
            elif self._keyword_match(question, ['trung vị', 'median']):
                val = median
                answer = f"trung vị = {val}"
            elif self._keyword_match(question, ['phương sai', 'variance']):
                val = variance
                answer = f"phương sai = {val}"
            elif self._keyword_match(question, ['độ lệch chuẩn', 'std', 'standard deviation']):
                val = std
                answer = f"độ lệch chuẩn = {val}"
            elif self._keyword_match(question, ['nhỏ nhất', 'min']):
                val = mn
                answer = f"min = {val}"
            elif self._keyword_match(question, ['lớn nhất', 'max']):
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
        except Exception:
            logger.exception("[slms.py:1890] silenced exception")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.95 if answer else 0.3
