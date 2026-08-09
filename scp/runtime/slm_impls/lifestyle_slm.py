"""
SLM implementations extracted from runtime/slms.py for modularity.

[Task 17-C] Split out from slms.py to keep individual modules under 1000 LOC
while preserving backward compatibility (slms.py re-exports everything).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from scp.runtime.slm_base import BaseSLM, SLMResponse
from scp.security.url_safety import safe_urlopen  # noqa: B310

logger = logging.getLogger("scp.slms")

# ============================================================
# Lifestyle / General / Religion / Food / City / Holiday / Facts / Advice SLMs
# ============================================================

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
                with safe_urlopen(req, timeout=10) as resp:
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


# ============================================================
# [V78] NEW SLMs — fill coverage gaps from deep check
# ============================================================

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
                    with safe_urlopen(req, timeout=5) as resp:
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
                with safe_urlopen(req, timeout=5) as resp:
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
                with safe_urlopen(req, timeout=5) as resp:
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
                with safe_urlopen(req, timeout=5) as resp:
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
                with safe_urlopen(req, timeout=5) as resp:
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
