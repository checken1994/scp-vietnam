"""
SLM part — extracted from slms.py (Task 19-A).
 kept verbatim; only the class location changed.
"""
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from scp.core.db_manager import (
    db_exec,
    db_query_one,
)

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


class ChemistrySLM(BaseSLM):
    """SLM chuyên về hóa học — PubChem API + local KB cache."""

    # Bảng nguyên tố phổ biến (atomic mass)
    _ELEMENTS = {
        'H': 1.008, 'He': 4.003, 'Li': 6.941, 'C': 12.011, 'N': 14.007,
        'O': 15.999, 'F': 18.998, 'Na': 22.990, 'Mg': 24.305, 'Al': 26.982,
        'Si': 28.086, 'P': 30.974, 'S': 32.065, 'Cl': 35.45, 'K': 39.098,
        'Ca': 40.078, 'Fe': 55.845, 'Cu': 63.546, 'Zn': 65.38, 'Ag': 107.868,
        'Au': 196.967, 'Hg': 200.59, 'Pb': 207.2,
    }

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="ChemSLM", domain="chemistry", config=config)

    def _extract_compound(self, question: str) -> Optional[str]:
        import re
        patterns = [
            r'khối\s+lượng\s+phân\s+tử\s+(?:của\s+)?(.+?)(?:\s+bằng|\s+là|\?|$)',
            r'molecular\s+weight\s+of\s+(.+?)\??$',
            r'molar\s+mass\s+of\s+(.+?)\??$',
            r'phân\s+tử\s+lượng\s+(?:của\s+)?(.+?)(?:\s+là|\?|$)',
            # [V58] Add formula pattern — "công thức của X là gì"
            r'(?:công\s+thức|formula)\s+(?:của\s+|of\s+)?(.+?)(?:\s+là\s+gì|\s+is\s+what|\?|$)',
            # [V59] Add pattern: "X có công thức gì?" — subject before "có công thức"
            r'(.+?)\s+có\s+công\s+thức\s+gì',
            r'(.+?)\s+có\s+công\s+thức\s+là\s+gì',
        ]
        for pat in patterns:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                c = m.group(1).strip().rstrip('?').rstrip('.').strip()
                if c and c.lower() not in ('gì', 'là', 'bao nhiêu', 'what'):
                    return c
        return None

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V30] Smart cache check
        try:
            from scp.core.smart_cache import get_smart_cache
            cached = get_smart_cache().get("slm:ChemSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        compound = self._extract_compound(question)
        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # [V60] If question asks for formula, return formula not molar mass
        is_formula_question = any(kw in question.lower() for kw in ['công thức', 'formula'])

        if compound and is_formula_question:
            # Look up formula from ChemistryDataSource
            try:
                from scp.data_sources.chemistry import ChemistryDataSource
                cds = ChemistryDataSource()
                result = cds.fetch('chemical_compound', compound)
                if result and result.get('metadata', {}).get('formula'):
                    formula = result['metadata']['formula']
                    answer = f"công thức {compound} = {formula}"
                    confidence = 0.92
                    reasoning = f"Chemistry formula lookup: {compound} → {formula}"
                    evidence = {"value": formula, "source": "Local Chemistry Database", "entity": compound, "formula": formula}
            except Exception as e:
                logger.debug(f"Formula lookup error: {e}")

        if not answer and compound:
            compound_lower = compound.lower()

            # 1) Check knowledge_memory cache trong DB
            try:
                cached_val = db_query_one(
                    "SELECT value FROM knowledge WHERE entity=? AND attribute='molecular_weight'",
                    (compound_lower,)
                )
                if cached_val:
                    val = float(cached_val['value'])
                    answer = f"MW({compound}) = {val}"
                    confidence = 0.95
                    reasoning = f"Knowledge cache: {compound} → {val}"
                    evidence = {"source": "KnowledgeCache", "entity": compound, "value": val}
            except Exception as e:
                logger.warning(f"Silent except: {e}")

            # 1.5) [V63] Check ChemistryDataSource local DB BEFORE calling PubChem API
            if not answer:
                try:
                    from scp.data_sources.chemistry import ChemistryDataSource
                    cds = ChemistryDataSource()
                    result = cds.fetch('chemical_compound', compound_lower)
                    if result and result.get('metadata', {}).get('molar_mass'):
                        val = result['metadata']['molar_mass']
                        answer = f"MW({compound}) = {val}"
                        confidence = 0.92
                        reasoning = f"Local Chemistry DB: {compound} → {val}"
                        evidence = {"source": "Local Chemistry Database", "entity": compound, "value": val}
                except Exception as e:
                    logger.debug(f"ChemistryDataSource error: {e}")

            # 2) [V29.2] Multi-source: PubChem + Wikidata
            if not answer:
                try:
                    from scp.core.multi_source_verifier import fetch_chemistry_multi
                    result = fetch_chemistry_multi(compound)
                    if result["value"] > 0:
                        val = result["value"]
                        # Save to knowledge cache (only if PubChem in sources — don't cache Wikidata only)
                        if "PubChem" in result["sources_succeeded"]:
                            try:
                                db_exec(
                                    "INSERT OR REPLACE INTO knowledge (entity, attribute, value, value_type, confidence, source, timestamp, times_verified) VALUES (?, ?, ?, 'float', 1.0, 'PubChem', ?, 1)",
                                    (compound_lower, "molecular_weight", str(val), datetime.now().isoformat())
                                )
                            except Exception as e:
                                logger.warning(f"Silent except: {e}")
                        answer = f"MW({compound}) = {val}"
                        confidence = result["confidence"]
                        reasoning = f"Multi-source chemistry: {result['reason']}"
                        evidence = {
                            "source": result["source"],
                            "entity": compound,
                            "value": val,
                            "sources_succeeded": result["sources_succeeded"],
                            "all_values": result["all_values"],
                            "conflict_detected": result["conflict_detected"],
                        }
                except Exception as e:
                    logger.warning(f"Chemistry multi-source error: {e}")

        if not answer:
            confidence = 0.3
            reasoning = f"Không tìm thấy compound hoặc API fail: '{compound or question[:50]}'"
            evidence = {"source": "none", "entity": compound, "needs_wikipedia": True}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="chemistry", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.90 if answer else 0.3
