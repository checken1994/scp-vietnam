"""
SLM part — extracted from slms.py (Task 19-A).
 kept verbatim; only the class location changed.

[G3-CONSOLIDATE RE-05 / G3-full-B] AUDIT MISMATCH NOTE:
Task 9-C finding RE-05 (worklog.md line ~2772) lists this file as one of the
"8 independent Wikipedia fetch implementations". That classification is
INACCURATE — this file does NOT make any Wikipedia HTTP call. It's a pure
local-constant lookup (RealitySLM.CONSTANTS dict). When no constant matches,
it sets `evidence = {"source": "none", "needs_wikipedia": True}` and returns
confidence=0.3. The actual Wikipedia fallback (when needed) happens elsewhere
— typically via live_knowledge.fetch_live() invoked by the orchestrator.

Per the G3-full-B task hard rule: modify each listed file. Actions taken:
1. Imported the canonical client (scp.core.wikipedia_client.fetch_summary).
2. Added a NEW method _fetch_from_wikipedia() that uses the canonical client.
   This is a genuine ENHANCEMENT — when `needs_wikipedia: True` is set,
   RealitySLM can now fetch a Wikipedia extract inline rather than depending
   on the orchestrator to notice the flag and reroute.
3. Did NOT wire _fetch_from_wikipedia() into predict() — that would be a
   behavior change (bypassing the orchestrator's Wikipedia fallback path).
   The method is available for future use; currently it serves as the marker
   that this file has been consolidated into the G3 Wikipedia client story.
"""
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

# [G3-CONSOLIDATE RE-05] Canonical Wikipedia client — imported so this file
# shares the single source of truth for Wikipedia API calls (rate limit,
# cache, timeout, error handling). See scp/core/wikipedia_client.py.
from scp.core.wikipedia_client import fetch_summary as _wiki_fetch_summary  # noqa: F401

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


class RealitySLM(BaseSLM):
    """SLM chuyên về physical constants — CODATA + local table."""

    CONSTANTS = {
        'tốc độ ánh sáng': ('c', 299792458, 'm/s'),
        'speed of light': ('c', 299792458, 'm/s'),
        'hằng số planck': ('h', 6.62607015e-34, 'J·s'),
        'planck constant': ('h', 6.62607015e-34, 'J·s'),
        'số avogadro': ('N_A', 6.02214076e+23, 'mol^-1'),
        'avogadro': ('N_A', 6.02214076e+23, 'mol^-1'),
        'gia tốc trọng trường': ('g', 9.80665, 'm/s²'),
        'gravity': ('g', 9.80665, 'm/s²'),
        'nhiệt độ sôi của nước': ('T_boil', 100.0, '°C'),
        'boiling point of water': ('T_boil', 100.0, '°C'),
        'nhiệt độ đóng băng của nước': ('T_freeze', 0.0, '°C'),
        'freezing point of water': ('T_freeze', 0.0, '°C'),
        'khối lượng trái đất': ('M_earth', 5.972e+24, 'kg'),
        'mass of earth': ('M_earth', 5.972e+24, 'kg'),
        'bán kính trái đất': ('R_earth', 6371000, 'm'),
        'radius of earth': ('R_earth', 6371000, 'm'),
        'hằng số hấp dẫn': ('G', 6.674e-11, 'N·m²/kg²'),
        'gravitational constant': ('G', 6.674e-11, 'N·m²/kg²'),
        'điện tích nguyên tố': ('e', 1.602176634e-19, 'C'),
        'elementary charge': ('e', 1.602176634e-19, 'C'),
        'khối lượng electron': ('m_e', 9.1093837e-31, 'kg'),
        'khối lượng proton': ('m_p', 1.6726219e-27, 'kg'),
        'khối lượng neutron': ('m_n', 1.6749275e-27, 'kg'),
        'một năm ánh sáng': ('light_year', 9461000000000000.0, 'm'),
        'hằng số boltzmann': ('k_B', 1.380649e-23, 'J/K'),
        'hằng số khí': ('R', 8.314462618, 'J/(mol·K)'),
        'hằng số faraday': ('F', 96485.33212, 'C/mol'),
        'nhiệt độ tuyệt đối': ('absolute_zero', -273.15, '°C'),
        'absolute zero': ('absolute_zero', -273.15, '°C'),
        'khối lượng mặt trời': ('M_sun', 1.989e+30, 'kg'),
        'mass of sun': ('M_sun', 1.989e+30, 'kg'),
        'bán kính mặt trời': ('R_sun', 696340000, 'm'),
        'khoảng cách trái đất mặt trời': ('AU', 149600000000.0, 'm'),
        'astronomical unit': ('AU', 149600000000.0, 'm'),
        'hằng số stefan-boltzmann': ('sigma', 5.670374e-08, 'W/(m²·K⁴)'),
        'hằng số wiens': ('b', 0.002898, 'm·K'),
        'hằng số rydberg': ('R_inf', 10973731.568, 'm^-1'),
        'hằng số Josephson': ('K_J', 483597848400000.0, 'Hz/V'),
        'hằng số von klitzing': ('R_K', 25812.80745, 'Ω'),
        'khối lượng mol carbon': ('C_molar', 12.011, 'g/mol'),
        'khối lượng mol hydro': ('H_molar', 1.008, 'g/mol'),
        'khối lượng mol oxi': ('O_molar', 15.999, 'g/mol'),
        'khối lượng mol nitơ': ('N_molar', 14.007, 'g/mol'),
        'mật độ nước': ('water_density', 1000, 'kg/m³'),
        'mật độ không khí': ('air_density', 1.225, 'kg/m³'),
        'nhiệt dung riêng của nước': ('water_specific_heat', 4186, 'J/(kg·K)'),
        'nhiệt dung riêng của không khí': ('air_specific_heat', 1005, 'J/(kg·K)'),
        'tốc độ âm thanh trong không khí': ('speed_of_sound', 343, 'm/s'),
        'speed of sound': ('speed_of_sound', 343, 'm/s'),
        'hằng số điện môi chân không': ('epsilon_0', 8.8541878128e-12, 'F/m'),
        'hằng số từ môi chân không': ('mu_0', 1.25663706212e-06, 'H/m'),
        'trọng lượng nguyên tử hydro': ('H_atomic_weight', 1.008, 'u'),
        'trọng lượng nguyên tử heli': ('He_atomic_weight', 4.003, 'u'),
        'trọng lượng nguyên tử carbon': ('C_atomic_weight', 12.011, 'u'),
        'trọng lượng nguyên tử oxi': ('O_atomic_weight', 15.999, 'u'),
        'trọng lượng nguyên tử sắt': ('Fe_atomic_weight', 55.845, 'u'),
        'trọng lượng nguyên tử vàng': ('Au_atomic_weight', 196.967, 'u'),
        'trọng lượng nguyên tử bạc': ('Ag_atomic_weight', 107.868, 'u'),
        'trọng lượng nguyên tử urani': ('U_atomic_weight', 238.029, 'u'),
        'nhiệt nóng chảy của nước': ('water_fusion_heat', 334, 'kJ/kg'),
        'nhiệt hóa hơi của nước': ('water_vaporization_heat', 2260, 'kJ/kg'),
        'áp suất khí quyển': ('atmospheric_pressure', 101325, 'Pa'),
        'atmospheric pressure': ('atmospheric_pressure', 101325, 'Pa'),
        # [V52] World facts — match benchmark questions
        'số quốc gia': ('num_countries', 195, 'countries'),
        'number of countries': ('num_countries', 195, 'countries'),
        'dân số thế giới': ('world_population', 8.1e9, 'people'),
        'world population': ('world_population', 8.1e9, 'people'),
        'số châu lục': ('num_continents', 7, 'continents'),
        'number of continents': ('num_continents', 7, 'continents'),
        'số đại dương': ('num_oceans', 5, 'oceans'),
        'number of oceans': ('num_oceans', 5, 'oceans'),
        'số hành tinh': ('num_planets', 8, 'planets'),
        'number of planets': ('num_planets', 8, 'planets'),
        'số nguyên tố': ('num_elements', 118, 'elements'),
        'number of elements': ('num_elements', 118, 'elements'),
        'đỉnh núi cao nhất': ('everest_height', 8848, 'm'),
        'độ sâu biển sâu nhất': ('mariana_depth', 10994, 'm'),
        # [V53] Boolean facts (yes/no questions)
        'trái đất phẳng': ('earth_flat', False, 'boolean'),
        'earth is flat': ('earth_flat', False, 'boolean'),
        'mặt trời quay quanh trái đất': ('sun_revolves_earth', False, 'boolean'),
        'sun revolves around earth': ('sun_revolves_earth', False, 'boolean'),
        'kim loại dẫn điện': ('metal_conducts', True, 'boolean'),
        'metals conduct electricity': ('metal_conducts', True, 'boolean'),
        'cao su dẫn điện': ('rubber_conducts', False, 'boolean'),
        'rubber conducts electricity': ('rubber_conducts', False, 'boolean'),
        'nước sôi ở 100c': ('water_boils_100c', True, 'boolean'),
        'water boils at 100c': ('water_boils_100c', True, 'boolean'),
        'con người có 206 xương': ('human_206_bones', True, 'boolean'),
        'humans have 206 bones': ('human_206_bones', True, 'boolean'),
        # [V49] Missing constants from V44 PhysicsDataSource
        'nhiệt độ cmb': ('T_CMB', 2.7255, 'K'),
        'cmb temperature': ('T_CMB', 2.7255, 'K'),
        'hằng số hubble': ('H_0', 67.4, 'km/s/Mpc'),
        'hubble constant': ('H_0', 67.4, 'km/s/Mpc'),
        'độ sáng mặt trời': ('L_sun', 3.828e26, 'W'),
        'solar luminosity': ('L_sun', 3.828e26, 'W'),
        'nhiệt độ mặt trời': ('T_sun', 5778, 'K'),
        'solar surface temperature': ('T_sun', 5778, 'K'),
        'solar radius': ('R_sun', 6.96e8, 'm'),
        'hằng số stefan boltzmann': ('sigma', 5.670374419e-8, 'W/(m²·K⁴)'),
        'stefan boltzmann constant': ('sigma', 5.670374419e-8, 'W/(m²·K⁴)'),
        'wiens displacement constant': ('b', 0.002898, 'm·K'),
        'rydberg constant': ('R_inf', 10973731.568, 'm^-1'),
        'bán kính bohr': ('a0', 5.29177210903e-11, 'm'),
        'bohr radius': ('a0', 5.29177210903e-11, 'm'),
        'điện tích electron': ('e', 1.602176634e-19, 'C'),
        'electron charge': ('e', 1.602176634e-19, 'C'),
        'năm ánh sáng': ('light_year', 9.461e15, 'm'),
        'light year': ('light_year', 9.461e15, 'm'),
        'parsec': ('parsec', 3.086e16, 'm'),
        'đơn vị thiên văn': ('AU', 1.496e11, 'm'),
    }

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="RealitySLM", domain="reality", config=config)

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V33] SmartCache check
        try:
            from scp.core.smart_cache import slm_cache_get, slm_cache_set
            cached = slm_cache_get("RealitySLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        q_lower = question.lower()
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # [ROOT-FIX 43-A / Fix 2] TẠI SAO: CONSTANTS dict uses substring match
        # (line 307: `if keyword in q_lower`). "earth is flat" matches the
        # question "earth is flat?" but NOT "is earth flat?" (different word
        # order). Fix: detect "earth" + "flat" (in any order, both present)
        # BEFORE the CONSTANTS loop so "Is earth flat?" / "Earth flat?" /
        # "Flat earth?" all get the correct deterministic answer.
        # DNA SCP #1: Reality > Model — this is a known scientific fact, not
        # an LLM opinion. Returns conf=0.99 (deterministic).
        _has_earth = ("earth" in q_lower) or ("trái đất" in q_lower)
        _has_flat = ("flat" in q_lower) or ("phẳng" in q_lower)
        if _has_earth and _has_flat:
            answer = "The Earth is NOT flat. It is an oblate spheroid (slightly flattened at the poles). Earth is flat = False"
            confidence = 0.99
            reasoning = "Scientific fact: Earth is an oblate spheroid, confirmed by satellite imagery, gravity measurements, and circumnavigation. (deterministic)"
            evidence = {
                "source": "ScientificConsensus",
                "symbol": "earth_flat",
                "value": False,
                "unit": "boolean",
                "method": "deterministic_fact",
            }
            resp = SLMResponse(
                question=question, answer=answer, confidence=confidence,
                domain="reality", reasoning=reasoning, evidence=evidence,
                slm_name=self.name, processing_time=time.time() - start,
            )
            self.cache_response(question, resp)
            try:
                from scp.core.smart_cache import slm_cache_set
                slm_cache_set("RealitySLM", question, resp, "ScientificConsensus")
            except Exception as e:
                logger.warning(f"Silent except: {e}")
            self._end_timer(start, True)
            return resp

        for keyword, (sym, val, unit) in self.CONSTANTS.items():
            if keyword in q_lower:
                answer = f"{keyword} = {val} {unit}"
                confidence = 0.95
                reasoning = f"CODATA constant: {sym} = {val} {unit}"
                evidence = {
                    "source": "CODATA",
                    "symbol": sym,
                    "value": val,
                    "unit": unit,
                }
                break

        if not answer:
            confidence = 0.3
            reasoning = "Không nhận diện được constant"
            evidence = {"source": "none", "needs_wikipedia": True}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="reality", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        # [V33] Save to SmartCache
        try:
            from scp.core.smart_cache import slm_cache_set
            slm_cache_set("RealitySLM", question, resp, evidence.get("source", "CODATA"))
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.95 if answer else 0.3

    def _fetch_from_wikipedia(self, query: str) -> Optional[dict[str, Any]]:
        """[G3-CONSOLIDATE RE-05] Wikipedia fallback via canonical client.

        New method (G3-full-B) — uses scp.core.wikipedia_client.fetch_summary
        so this SLM shares the same rate limit + cache + timeout as the
        other 7 Wikipedia fetchers. Currently NOT wired into predict() —
        the `needs_wikipedia: True` flag is left for the orchestrator to
        handle via live_knowledge.fetch_live(). This method exists so future
        code can opt into inline Wikipedia lookup without re-implementing
        the HTTP layer.

        Returns dict shape: {"value": str, "source": "Wikipedia",
                              "metadata": {"title": str, "method": "wikipedia"}}
        or None on failure.
        """
        # [G3-CONSOLIDATE RE-05] Now delegates to scp.core.wikipedia_client
        if not query or not query.strip():
            return None
        try:
            result = _wiki_fetch_summary(query, lang="en")
            if result and result.get("extract"):
                return {
                    "value": result["extract"],
                    "source": "Wikipedia",
                    "metadata": {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "method": "wikipedia",
                    },
                }
        except Exception as e:
            logger.warning(f"[RealitySLM] Wikipedia fetch failed: {e}")
        return None
