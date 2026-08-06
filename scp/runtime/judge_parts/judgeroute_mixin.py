""" JudgeVerdict
SCP V90 — Reality Judge Module
Cross-check SLMs, verify with reality, produce final verdicts.
Extracted from engine.py for modularity.
"""


import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass

# [G3-CONSOLIDATE P1-03] Inline routing rules now delegate to canonical domain_registry.
# Previously this file had ~470 LOC of inline `if any(kw in q_lower for kw in [...])`
# keyword lists that DISAGREED with smart_classifier.DOMAIN_PROFILES (44 domains) and
# domain_registry.DOMAINS (45 domains) — same question routed differently depending on
# which DB won (Task 2-B finding P1-03). The canonical registry now holds the superset
# (54 domains) with merged keywords. We import DOMAINS here and use _kw(domain_id) to
# fetch the canonical keyword list wherever an inline list previously appeared.
# The conditional logic (e.g. "if thuốc → don't add biology", "if reality → remove
# chemistry/astronomy/geography") is preserved verbatim — only the keyword LISTS are
# delegated.
from scp.data_sources.domain_registry import DOMAINS as _CANONICAL_DOMAINS


def _kw(domain_id: str) -> list[str]:
    """[G3-CONSOLIDATE P1-03] Fetch canonical keyword list for a domain.

    Replaces the ~30 inline `["kw1", "kw2", ...]` lists that were previously
    scattered across `_route_question`. Returns an empty list for unknown
    domains (preserves the original `any(kw in q_lower for kw in [...])`
    semantics — empty list → no match).
    """
    return _CANONICAL_DOMAINS.get(domain_id, {}).get("keywords", [])


logger = logging.getLogger("scp.judge")


# [V5.3-WIRE] multi_llm_check integration — opt-in via SCP_MULTI_LLM_CHECK=1 env var.
# TẠI SAO: DNA SCP #20 "ảo giác đồng thuận" — 100 AI cùng kết luận chưa chắc 100
# nguồn độc lập. Cross-check giữa 2-3 LLM providers (OpenRouter + Groq) để phát
# hiện disagreement. Default OFF để không tăng latency khi không cần.
# Lazy singleton — chỉ instantiate khi env var ON.
_MULTI_LLM_CHECKER_SINGLETON = None
_MULTI_LLM_CHECKER_LOCK = __import__("threading").Lock()


def _get_multi_llm_checker():
    """Lazy singleton for MultiLLMChecker. Returns None if env var OFF."""
    global _MULTI_LLM_CHECKER_SINGLETON
    if os.environ.get("SCP_MULTI_LLM_CHECK", "0") != "1":
        return None
    if _MULTI_LLM_CHECKER_SINGLETON is None:
        with _MULTI_LLM_CHECKER_LOCK:
            if _MULTI_LLM_CHECKER_SINGLETON is None:
                try:
                    from scp.meta.multi_llm_check import MultiLLMChecker
                    _MULTI_LLM_CHECKER_SINGLETON = MultiLLMChecker()
                    logger.info("[V5.3-WIRE] MultiLLMChecker initialized — adversary answers will be cross-checked")
                except Exception as e:
                    logger.warning(f"[V5.3-WIRE] MultiLLMChecker init failed: {e} — multi-LLM check disabled")
                    _MULTI_LLM_CHECKER_SINGLETON = False
    return _MULTI_LLM_CHECKER_SINGLETON if _MULTI_LLM_CHECKER_SINGLETON is not False else None


# [V104.42 #AH] Internal signal — source passed watchlist check, proceed to INSERT

# ============================================================
# JUDGE VERDICT
# ============================================================
@dataclass

# ============================================================
# REALITY JUDGE — Cross-check SLMs + Verify với Reality
# ============================================================


class JudgeRouteMixin:
    """Mixin for RealityJudge — provides _route_question."""

    def _route_question(self, question: str) -> list[str]:
        """Route question to appropriate SLM(s).
        [V72] Cached — same question returns same routing for 1 hour.
        [V95] Uses SmartClassifier if available, falls back to keyword matching.
        """
        if not question:
            return ["math"]

        # [V72] Check cache first
        cache_key = hashlib.sha256(question.encode()).hexdigest()
        cached = self._ROUTE_CACHE.get(cache_key)
        if cached:
            ts, domains_cached = cached
            if time.time() - ts < self._ROUTE_CACHE_TTL:
                return domains_cached

        q_lower = question.lower()
        domains = []

        # [V95] Use SmartClassifier for better accuracy
        if self.classifier:
            try:
                domains = self.classifier.classify_multi(question, max_domains=3)
                # [OPT-33] BUG #2 FIX: Was short-circuiting whenever classifier
                # succeeded, but classify_multi() ALWAYS succeeds because classify()
                # falls back to "general" when score < 0.15 (_MIN_CONFIDENT_SCORE).
                # That low-confidence fallback bypassed the keyword-based routing
                # rules below, which correctly handle Vietnamese patterns
                # ("thủ đô" → geography, "châu lục" → geography, "earth is flat"
                # → reality, "P/E ratio" → finance via tech/finance keywords).
                # Fix: only short-circuit when classifier found a NON-general
                # domain (i.e., real confidence). Otherwise fall through to
                # keyword routing which knows about Vietnamese + edge cases.
                if domains and any(d != "general" for d in domains):
                    # Confidence is high enough — use classifier result
                    self._ROUTE_CACHE[cache_key] = (time.time(), domains)
                    return domains
                # else: classifier returned ["general"] (low-confidence fallback) —
                # reset and fall through to keyword-based routing
                domains = []
            except Exception as e:
                logger.debug(f"[V95] SmartClassifier failed: {e}, using fallback")

        # [V91 FIX] "Tell me about X" → general (cross-verify) UNLESS specific domain
        if q_lower.startswith("tell me about"):
            is_entertainment = any(kw in q_lower for kw in [
                "the book", "the tv show", "the movie", "star wars",
                "the tv series", "the film"])
            is_reality = any(kw in q_lower for kw in [
                "speed of light", "boiling point", "freezing point",
                "planck", "avogadro", "gravity"])
            if not is_entertainment and not is_reality:
                domains = ["general"]
                self._ROUTE_CACHE[hashlib.sha256(question.encode()).hexdigest()] = (time.time(), domains)
                return domains

        # Rule-based routing — check more specific keywords first
        # [v28] Statistics — must come BEFORE math (otherwise stats questions get routed to math)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["statistics"]
        if any(kw in q_lower for kw in _kw("statistics")):
            domains.append("statistics")
        # [V56] Conversion — must come BEFORE math (unit conversion questions have digits + operators)
        # [V56.1] But ONLY if there's a unit keyword — don't catch "sqrt(144) bằng bao nhiêu?" (that's math)
        _unit_keywords = ["km", "cm", "mm", "mile", "inch", "foot", "yard", "pound", "kg", "mg",
                          "liter", "gallon", "acre", "hectare", "watt", "horsepower", "joule",
                          "hour", "minute", "second", "day", "year", "knot", "mach", "btu",
                          "cal", "kwh", "ounce", "stone", "ton", "quart", "pint",
                          "atm", "pascals", "pa", "bar", "psi", "floz", "tablespoon", "teaspoon"]
        _has_unit = any(f' {uk} ' in f' {q_lower} ' or f' {uk}?' in f' {q_lower} ' or f' {uk}s' in f' {q_lower} ' for uk in _unit_keywords)
        if (_has_unit and any(kw in q_lower for kw in ["bằng bao nhiêu", "bằng bao nhieu"])) or \
           any(kw in q_lower for kw in ["chuyển đổi", "convert"]):
            domains.append("conversion")
        # Math (highest priority, very fast) — skip if statistics/conversion already matched
        # [V5.9-FIX] TẠI SAO: removed single-char "+","-","*","/" from keywords — they match
        # CVE numbers (CVE-2021-44228), dates, URLs → false positive math routing.
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["math"]
        if "statistics" not in domains and "conversion" not in domains and any(kw in q_lower for kw in _kw("math")):
            domains.append("math")
        # Finance — currency / crypto / exchange (must come before biology since "giá" can overlap)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["finance"]
        if any(kw in q_lower for kw in _kw("finance")):
            domains.append("finance")
            # [V29 FIX] Also route to conversion (ConversionSLM + FinanceSLM cross-check)
            domains.append("conversion")
        # [V63] Medical — must come BEFORE biology/statistics/math
        # [V63.2] If "thuốc" present, route to medical ONLY (not biology) to avoid CONFLICT
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["medical"]
        _medical_kws = _kw("medical")
        if any(kw in q_lower for kw in _medical_kws):
            domains.append("medical")
            # [V63.2] "thuốc X" is medical, not biology — remove biology
            if "thuốc" in q_lower:
                _biology_remove = True
            else:
                _biology_remove = False
        # [V89.6 FIX] Define _is_pokemon and _is_star_wars BEFORE first use
        _is_pokemon = "pokemon" in q_lower
        _is_star_wars = "star wars" in q_lower
        # Biology — actual biology keywords (not just "nhiệt" which is also weather)
        # [V49] Expanded biology keywords
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["biology"]
        if not _is_pokemon and any(kw in q_lower for kw in _kw("biology")):
            # [V63.2] Don't add biology if "thuốc" question routed to medical
            if "_biology_remove" in dir() and _biology_remove:
                pass  # Skip biology — "thuốc X" is medical
            else:
                domains.append("biology")
        # [V48] Astronomy — must come BEFORE chemistry vì "khối lượng của Sao Mộc" có "khối lượng"
        # nhưng là astronomy question, không phải chemistry
        # [V75] Don't route to astronomy if it's a Star Wars question
        # [V89] Pokemon = entertainment, NOT biology
        # (_is_star_wars and _is_pokemon already defined above — V89.6 FIX)
        # [V89 FIX] Word-boundary check for short English keywords to prevent false positives
        # "mars" in "marshall" was routing history questions to astronomy!
        _astro_kws_vi = ["sao thủy", "sao kim", "sao hỏa", "sao mộc", "sao thổ",
                         "sao thiên vương", "sao hải vương", "sao diêm vương",
                         "trái đất", "mặt trăng", "mặt trời", "hành tinh",
                         "vệ tinh", "thiên hà", "chòm sao"]
        _astro_kws_en = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus",
                         "neptune", "pluto", "moon", "sun", "planet", "galaxy", "astronomy"]
        _has_astro_vi = any(kw in q_lower for kw in _astro_kws_vi)
        _has_astro_en = any(f' {kw} ' in f' {q_lower} ' or f' {kw}?' in f' {q_lower} ' or
                           f' {kw}.' in f' {q_lower} ' or f' {kw}s ' in f' {q_lower} '
                           for kw in _astro_kws_en)
        if not _is_star_wars and not _is_pokemon and (_has_astro_vi or _has_astro_en):
            domains.append("astronomy")
        # [V49 FIX] Reality (physical constants) — must come BEFORE chemistry
        # vì "hằng số khối lượng electron" có "electron" nhưng là physical constant, không phải chemistry
        # Trước V49: chemistry routing khớp "electron" → route sai → ChemistrySLM không có data → PARTIAL
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["reality"]
        # (was a 60+ entry inline list — now unified with smart_classifier + domain_registry)
        if any(kw in q_lower for kw in _kw("reality")):
            domains.append("reality")
            # Remove chemistry/astronomy/geography/biology/logic if they were matched
            for d in ("chemistry", "astronomy", "geography", "biology", "logic"):
                if d in domains:
                    domains.remove(d)
        # Chemistry — molecules, atoms, reactions, elements
        # [V54] Add compound names (glucose, caffeine, etc.) + "công thức" (formula)
        # [V63.1] If "khối lượng phân tử" present, route ONLY to chemistry (not medical)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["chemistry"]
        if any(kw in q_lower for kw in _kw("chemistry")):
            domains.append("chemistry")
            # [V63.1] "khối lượng phân tử" is chemistry-specific — remove medical AND biology
            if "khối lượng phân tử" in q_lower or "phân tử lượng" in q_lower:
                for d in ("medical", "biology"):
                    if d in domains:
                        domains.remove(d)
        # [V29 FIX] Reality (physical constants) — must come BEFORE weather
        # [V49] Already handled above — this block is now a no-op (kept for safety)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["reality"]
        if "reality" not in domains and any(kw in q_lower for kw in _kw("reality")):
            domains.append("reality")
        # Remove weather if reality already matched (nhiệt độ sôi ≠ weather)
        if "reality" in domains and "weather" in domains:
            domains.remove("weather")
        # Weather — temperature, humidity, climate (not "nhiệt độ cơ thể" which is biology)
        # [V29 FIX] Skip weather if reality already matched
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["weather"]
        if "reality" not in domains and any(kw in q_lower for kw in _kw("weather")):
            domains.append("weather")
        # [V62] Sports — must come BEFORE history (athletes have "là ai?" pattern)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["sports"]
        # NOTE: original inline had a copy-paste bug ("weather in" etc. mixed into sports list
        # — preserved in canonical merge for traceability but harmless: those keywords are
        # also in weather domain, so a sports question wouldn't match them anyway.
        if any(kw in q_lower for kw in _kw("sports")):
            domains.append("sports")
        # [V63] Arts — must come BEFORE history (artists have "là ai?" pattern)
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["arts"]
        # NOTE: scientist-exclusion logic preserved (these scientists go to history, not arts).
        _scientist_exclusion = {"tesla", "einstein", "newton", "darwin",
                                 "edison", "galileo", "curie", "turing",
                                 "feynman", "hawking"}
        if any(kw in q_lower for kw in _kw("arts")):
            # These scientists go to history, not arts
            if any(sn in q_lower for sn in _scientist_exclusion):
                pass  # Let history handle it
            else:
                domains.append("arts")
        # [V63] Technology — must come BEFORE history (tech companies have "thành lập năm nào?")
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["technology"]
        if any(kw in q_lower for kw in _kw("technology")):
            # [V63.5] Don't route to technology if it's a "Ai là X?" question about a scientist
            _is_who_question = any(p in q_lower for p in ["ai là", "là ai", "who is", "who was"])
            _scientist_names = {"tesla", "einstein", "newton", "darwin", "edison",
                                "galileo", "curie", "turing", "feynman", "hawking"}
            if _is_who_question and any(sn in q_lower for sn in _scientist_names):
                pass  # Let history handle it
            # [V73] Don't route to technology if it's a food/nutrition question
            # ("apple" the fruit, not "Apple" the company)
            elif any(kw in q_lower for kw in ["nutritional value", "nutrition", "calories",
                                                "recipe", "fruit", "food"]):
                pass  # Let food SLM handle it
            else:
                domains.append("technology")
        # [V63] Legal — must come BEFORE history (laws have "năm nào?")
        # [V63.5] "quyền" alone is too generic (matches "Ngô Quyền" name) — use "quyền " with space
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["legal"]
        if any(kw in q_lower for kw in _kw("legal")):
            domains.append("legal")
        # [V63] Organizations — WHO, WTO, NASA, etc. → reality or legal
        # Check original case for acronyms
        if any(acronym in question for acronym in ["WHO", "WTO", "NASA", "UNESCO", "UN ", "NATO"]):
            if "thành lập" in q_lower or "năm nào" in q_lower:
                domains.append("legal")
        # Geography — capitals, countries, continents
        # [V73] Removed "population", "dân số" from geography keywords — now handled by city SLM
        # Was: "population of Tokyo" → geography (only knows countries) → UNKNOWN
        # Now: "population of Tokyo" → city SLM (geocoding API, knows cities) → PASS
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["geography"]
        if any(kw in q_lower for kw in _kw("geography")):
            domains.append("geography")
        # [v27/V89] History — events, figures, years, wars, battles
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["history"]
        if any(kw in q_lower for kw in _kw("history")):
            domains.append("history")
        # [v28] Logic — comparison operators
        if re.search(r'\d\s*[<>=!]+\s*\d', q_lower) or any(kw in q_lower for kw in [" so sánh ", "compare", "đúng không", "true or false", "boolean"]):
            domains.append("logic")
        # [v28] Statistics — mean/median/variance
        # [G3-CONSOLIDATE P1-03] keywords delegated to canonical domain_registry.DOMAINS["statistics"]
        if any(kw in q_lower for kw in _kw("statistics")):
            domains.append("statistics")
        # [v28] Reality — physical constants (V29: dedup — đã match ở V29 block trên)
        # if "reality" not in domains and any(kw in q_lower for kw in ["tốc độ ánh sáng", "hằng số planck", "số avogadro", "gia tốc trọng trường", "khối lượng trái đất", "hằng số hấp dẫn", "speed of light", "gravity", "boiling point", "freezing point"]):
        #     domains.append("reality")
        pass  # [V29] moved to V29 block above (with dedup logic)

        # If no rule matched, try V13 classifier
        if not domains:
            try:
                frame, conf = self.v13.classifier.classify(question)
            except Exception:
                frame, _ = "unknown", 0.0  # [FALSE-POS-FIX] F841: _ prefix marks intentionally-unused tuple element
            if frame != "unknown" and frame in self.slms:
                domains.append(frame)
            elif frame == "conversion":
                domains.append("finance")
            elif frame == "chemistry":
                # Don't route chemistry to any SLM (no ChemistrySLM exists)
                # Let V13 handle it directly
                pass

        # [V62] V46 domain routing — rule-based for common V46 domains
        # [V73] Reordered: food, religion, entertainment checked BEFORE technology/medical
        # because "nutrition", "scripture", "tv show" keywords are more specific
        # [G3-CONSOLIDATE P1-03] all keyword lists now delegate to canonical domain_registry
        if not domains:
            # [V73] Food & recipes — cooking, nutrition, cocktails (CHECK FIRST — more specific than technology)
            if any(kw in q_lower for kw in _kw("food")):
                domains.append("food")
            # [V73] Religion/literature — Bible, Quran, verses, quotes
            elif any(kw in q_lower for kw in _kw("religion")):
                domains.append("religion")
            # [V73] Entertainment — TV shows, movies, jokes, celebrities
            # [V75] Added "star wars" — must come BEFORE general/astronomy
            elif any(kw in q_lower for kw in _kw("entertainment")):
                domains.append("entertainment")
            # Arts — phim, họa sĩ, nhạc sĩ, thiết kế + EN: D&D, spell, novel, painting
            elif any(kw in q_lower for kw in _kw("arts")):
                domains.append("arts")
            # Medical — bệnh, thuốc, triệu chứng + EN
            elif any(kw in q_lower for kw in _kw("medical")):
                domains.append("medical")
            # Technology — programming, AI, software + EN
            elif any(kw in q_lower for kw in _kw("technology")):
                domains.append("technology")
            # Sports — Olympic, FIFA, athletes + EN
            elif any(kw in q_lower for kw in _kw("sports")):
                domains.append("sports")
            # Legal — luật, hiến pháp, hợp đồng + EN
            elif any(kw in q_lower for kw in _kw("legal")):
                domains.append("legal")
            # [V73] General — names, gender, life advice, opinions
            # [V78] Removed "advice" — now handled by AdviceSLM
            elif any(kw in q_lower for kw in _kw("general")):
                domains.append("general")
            # [V73] City — populations, areas for cities (NOT countries)
            elif any(kw in q_lower for kw in _kw("city")):
                # Could be country (handled by geography) or city (handled by city)
                # Route to BOTH — geography will handle countries, city will handle cities
                domains.append("city")
                domains.append("geography")
            # [V75] Pokemon — "What type is the Pokemon X?" → biology (PokeAPI)
            # NOTE: pokemon keywords kept inline — not a canonical domain (routes to biology)
            elif any(kw in q_lower for kw in ["pokemon", "what type is the pokemon",
                                              "pokeapi"]):
                domains.append("biology")
            # [V78] Public holidays — "What is a public holiday in X?"
            elif any(kw in q_lower for kw in _kw("holiday")):
                domains.append("holiday")
            # [V78] Animal facts — "Tell me a fact about cats/dogs"
            elif any(kw in q_lower for kw in _kw("animal_facts")):
                domains.append("animal_facts")
            # [V78] Life advice — "What is a piece of useful life advice?"
            elif any(kw in q_lower for kw in _kw("advice")):
                domains.append("advice")
            # [V78] Chuck Norris — "Tell me a Chuck Norris fact"
            elif any(kw in q_lower for kw in _kw("chuck_norris")):
                domains.append("chuck_norris")

        # [V46] If still no match, use DomainClassifier for 45+ domains
        if not domains:
            try:
                from scp.data_sources.domain_classifier import classify_question
                results = classify_question(question, top_k=3)
                for domain_id, score in results:
                    if domain_id in self.slms and score >= 0.5:
                        domains.append(domain_id)
                        break
            except Exception as e:
                logger.debug(f"V46 DomainClassifier error: {e}")

        # [V97] 26 NEW domain routing — cho 26 SLM mới được import
        # [G3-CONSOLIDATE P1-03] Inline 26-domain keyword dict replaced with
        # canonical registry lookup. Priority order preserved (matches first
        # domain in DOMAINS iteration order whose keywords hit).
        if not domains:
            _v97_domain_order = [
                "physics", "education", "psychology", "environment", "energy",
                "transport", "blockchain", "cybersecurity", "genai", "social",
                "aerospace", "tourism", "foodtech", "geology", "oceanography",
                "cartography", "architecture", "uxui", "digitalmarketing",
                "ecommerce", "audiovideo", "crafts", "diplomacy", "heritage",
                "military", "spacemedicine",
            ]
            for domain in _v97_domain_order:
                if any(kw in q_lower for kw in _kw(domain)):
                    domains.append(domain)
                    break  # match domain đầu tiên (priority order)

        # ============================================================
        # [V5.9-ROUTE] 26 SLMs routing — fix dead SLMs (init but never routed)
        # ROOT CAUSE: V97 block above is gated behind `if not domains:` → never
        # fires when earlier rules already matched. Math rule (line ~702) has "-"
        # as keyword → "CVE-2021-44228" matches "-" → routes to "math" (wrong).
        # FIX: Add UNCONDITIONAL routing blocks for 26 dead SLMs + cleanup that
        # removes false-positive math when a specific new domain matched.
        # Spec: V59-FIX-ROUTING-26-1 (Vietnamese + English keywords).
        # [G3-CONSOLIDATE P1-03] All 26 inline `_xxx_kws = [...]` lists replaced
        # with `_kw("xxx")` lookups to canonical domain_registry. Word-boundary
        # special cases (sea, war, ux, ui, AI) preserved verbatim.
        # ============================================================

        # Word-boundary checks for short keywords that would otherwise match
        # substrings of unrelated words.
        _has_sea_word = bool(re.search(r'\bsea\b', q_lower))
        _has_war_word = bool(re.search(r'\bwar\b', q_lower))
        _has_uxui_short = bool(re.search(r'\b(ux|ui)\b', q_lower))
        # genai: "AI" must be case-sensitive (Vietnamese "ai" = "who")
        _has_AI_uppercase = "AI" in question

        _v59_route_table = [
            ("physics",         _kw("physics")),
            ("cybersecurity",   _kw("cybersecurity")),
            ("education",       _kw("education")),
            ("psychology",      _kw("psychology")),
            ("environment",     _kw("environment")),
            ("energy",          _kw("energy")),
            ("transport",       _kw("transport")),
            ("blockchain",      _kw("blockchain")),
            ("genai",           _kw("genai")),  # + _has_AI_uppercase (added below)
            ("social",          _kw("social")),
            ("aerospace",       _kw("aerospace")),
            ("tourism",         _kw("tourism")),
            ("foodtech",        _kw("foodtech")),
            ("geology",         _kw("geology")),
            ("oceanography",    _kw("oceanography")),  # + _has_sea_word (added below)
            ("cartography",     _kw("cartography")),
            ("architecture",    _kw("architecture")),
            ("uxui",            _kw("uxui")),  # + _has_uxui_short (added below)
            ("digitalmarketing", _kw("digitalmarketing")),
            ("ecommerce",       _kw("ecommerce")),
            ("audiovideo",      _kw("audiovideo")),
            ("crafts",          _kw("crafts")),
            ("diplomacy",       _kw("diplomacy")),
            ("heritage",        _kw("heritage")),
            ("military",        _kw("military")),  # + _has_war_word (added below)
            ("spacemedicine",   _kw("spacemedicine")),
        ]
        for _dom, _kws in _v59_route_table:
            if _dom in domains:
                continue
            # Per-domain word-boundary special cases (preserved from V5.9-ROUTE)
            if _dom == "genai":
                if _has_AI_uppercase or any(kw in q_lower for kw in _kws):
                    domains.append(_dom)
            elif _dom == "oceanography":
                if _has_sea_word or any(kw in q_lower for kw in _kws):
                    domains.append(_dom)
            elif _dom == "uxui":
                if _has_uxui_short or any(kw in q_lower for kw in _kws):
                    domains.append(_dom)
            elif _dom == "military":
                if _has_war_word or any(kw in q_lower for kw in _kws):
                    domains.append(_dom)
            else:
                if any(kw in q_lower for kw in _kws):
                    domains.append(_dom)

        # [V5.9-ROUTE] Cleanup: remove false-positive math when a specific new
        # domain matched. Math rule (line ~702) has "-" "*" "/" as single-char
        # keywords → "CVE-2021-44228" (has "-") matches math → wrong route.
        # Only remove math if NO real math keyword (calculate, tính, sqrt, ...)
        # is in the question — i.e., math matched ONLY due to single-char ops.
        # [G3-CONSOLIDATE P1-03] _real_math_kws kept inline (subset of math
        # keywords — the canonical math list now includes the V5.9 chars
        # already; this cleanup survives delegation).
        _v59_specific_domains = {
            "physics", "cybersecurity", "education", "psychology",
            "environment", "energy", "transport", "blockchain", "genai",
            "social", "aerospace", "tourism", "foodtech", "geology",
            "oceanography", "cartography", "architecture", "uxui",
            "digitalmarketing", "ecommerce", "audiovideo", "crafts",
            "diplomacy", "heritage", "military", "spacemedicine",
        }
        if "math" in domains and any(d in domains for d in _v59_specific_domains):
            _real_math_kws = ["calculate", "tính", "collatz", "giai thừa",
                              "fibonacci", "sqrt", "square root", "căn bậc",
                              "gcd", "compute", "solve", "how much is",
                              "sum of", "product of", "difference of",
                              "factorial", "what is 2", "what is 3",
                              "what is 4", "what is 5", "what is 6",
                              "what is 7", "what is 8", "what is 9"]
            if not any(kw in q_lower for kw in _real_math_kws):
                domains.remove("math")

        # [V77→V81] Fallback: was "math" (V76) → "general" (V77) → "universal" (V81)
        # V81: UniversalSLM covers 45+ domains via Wikidata/Wikipedia
        if not domains:
            domains = ["universal"]

        # [V72] Cache the routing result
        if len(self._ROUTE_CACHE) >= self._ROUTE_CACHE_MAX:
            # Evict oldest 25% entries (simple LRU)
            sorted_keys = sorted(self._ROUTE_CACHE.keys(),
                                  key=lambda k: self._ROUTE_CACHE[k][0])
            for k in sorted_keys[:self._ROUTE_CACHE_MAX // 4]:
                del self._ROUTE_CACHE[k]
        self._ROUTE_CACHE[cache_key] = (time.time(), domains)

        return domains

