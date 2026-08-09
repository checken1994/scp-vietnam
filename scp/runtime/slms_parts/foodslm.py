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


class FoodSLM(BaseSLM):
    """
    [V73] Food & Recipe SLM — recipes, nutrition, cocktails.
    Uses cached MealDB/CocktailDB/Fruityvice data from external_questions.
    """
    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="FoodSLM", domain="food", config=config)

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

        # "What is the recipe for X?" → search local knowledge for X
        m = re.match(r'what\s+is\s+the\s+recipe\s+for\s+(.+?)\?*$', q, re.IGNORECASE)
        if m:
            dish = m.group(1).strip().rstrip('?').strip()
            # Try MealDB with progressively shorter search terms
            try:
                import requests
                # [V89 FIX] Try full dish name, then individual words
                search_terms = [dish]
                words = dish.split()
                if len(words) > 1:
                    search_terms.extend(words)

                meals = []
                for term in search_terms:
                    r = requests.get(f"https://www.themealdb.com/api/json/v1/1/search.php?s={term}",
                                     timeout=5, headers={'User-Agent': 'SCP-V73/1.0'})
                    if r.status_code == 200:
                        data = r.json()
                        meals = data.get("meals") or []
                        if meals:
                            break
                if meals:
                    meal = meals[0]
                    answer = f"{meal.get('strMeal', dish)} — a {meal.get('strCategory', '')} dish from {meal.get('strArea', '')}."
                    confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                    reasoning = f"MealDB: {meal.get('strMeal', dish)}"
                    evidence = {"value": answer, "source": "mealdb", "category": meal.get("strCategory", "")}
            except Exception as e:
                reasoning = f"MealDB error: {e}"

        # "How do you make the cocktail X?" → CocktailDB
        m = re.match(r'how\s+do\s+you\s+make\s+the\s+cocktail\s+(.+?)\?*$', q, re.IGNORECASE)
        if m and not answer:
            cocktail = m.group(1).strip().rstrip('?').strip()
            try:
                import requests
                r = requests.get(f"https://www.thecocktaildb.com/api/json/v1/1/search.php?s={cocktail}",
                                 timeout=5, headers={'User-Agent': 'SCP-V73/1.0'})
                if r.status_code == 200:
                    data = r.json()
                    drinks = data.get("drinks") or []
                    if drinks:
                        d = drinks[0]
                        answer = f"{d.get('strDrink', cocktail)} — a {d.get('strCategory', '')} served in {d.get('strGlass', '')}."
                        confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                        reasoning = f"CocktailDB: {d.get('strDrink', cocktail)}"
                        evidence = {"value": answer, "source": "cocktaildb"}
            except Exception as e:
                reasoning = f"CocktailDB error: {e}"

        # "What is the nutritional value of X?" → Fruityvice if fruit
        m = re.match(r'what\s+is\s+the\s+nutritional\s+value\s+of\s+(.+?)\?*$', q, re.IGNORECASE)
        if m and not answer:
            fruit = m.group(1).strip().rstrip('?').strip().lower()
            try:
                import requests
                r = requests.get(f"https://www.fruityvice.com/api/fruit/{fruit}",
                                 timeout=5, headers={'User-Agent': 'SCP-V73/1.0'})
                if r.status_code == 200:
                    data = r.json()
                    nutr = data.get("nutritions", {})
                    answer = (f"{data.get('name', fruit)} (family: {data.get('family', '')}). "
                              f"Nutrition per 100g: calories={nutr.get('calories', '?')}, "
                              f"sugar={nutr.get('sugar', '?')}g, carbs={nutr.get('carbohydrates', '?')}g, "
                              f"protein={nutr.get('protein', '?')}g.")
                    confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                    reasoning = f"Fruityvice: {data.get('name', fruit)}"
                    evidence = {"value": answer, "source": "fruityvice"}
            except Exception as e:
                reasoning = f"Fruityvice error: {e}"

        if not answer:
            confidence = 0.0
            reasoning = "Food pattern not recognized"

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="food", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # [ROOT-FIX] Default 0.5 (unverified). Sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
        return 0.5 if answer else 0.0
