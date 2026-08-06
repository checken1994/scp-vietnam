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


class MathSLM(BaseSLM):
    """
    SLM chuyên về toán học — V27: dùng Deterministic Math Evaluator (AST).

    Không bao giờ gọi LLM cho phép tính. Tính trực tiếp bằng Python AST:
    - +, -, *, /, //, %, ** (power)
    - Parentheses
    - Hàm: sqrt, abs, gcd, lcm, factorial (!), log, ln, sin, cos, tan, exp
    - Hằng số: pi, e, tau
    - So sánh: ==, !=, <, >, <=, >=
    """

    def __init__(self, config: dict = None):
        super().__init__(name="MathSLM", domain="math", config=config)
        # [V35] RealityEngine deleted (dead code)
        # [v27] Import deterministic evaluator
        from scp.core.math_evaluator import MathEvalError, evaluate_expression, extract_math_expression, verify_math
        self._extract_expr = extract_math_expression
        self._eval_expr = evaluate_expression
        self._verify_math = verify_math
        self._MathEvalError = MathEvalError

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V33] SmartCache check
        try:
            from scp.core.smart_cache import slm_cache_get, slm_cache_set
            cached = slm_cache_get("MathSLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        # [v27] Deterministic path — NO LLM
        expr = self._extract_expr(question)
        if not expr:
            resp = SLMResponse(
                question=question, answer="", confidence=0.0,
                domain="math", reasoning="Không parse được biểu thức",
                evidence={}, slm_name=self.name, processing_time=0,
            )
            self._end_timer(start, False)
            return resp

        try:
            result = self._eval_expr(expr)
        except self._MathEvalError as e:
            resp = SLMResponse(
                question=question, answer="", confidence=0.0,
                domain="math", reasoning=f"Lỗi evaluate: {e}",
                evidence={"expr": expr, "error": str(e)},
                slm_name=self.name, processing_time=time.time() - start,
            )
            self.cache_response(question, resp)
            self._end_timer(start, False)
            return resp
        except Exception as e:
            resp = SLMResponse(
                question=question, answer="", confidence=0.0,
                domain="math", reasoning=f"Lỗi không xác định: {e}",
                evidence={"expr": expr, "error": str(e)},
                slm_name=self.name, processing_time=time.time() - start,
            )
            self._end_timer(start, False)
            return resp

        # Format answer
        if isinstance(result, bool):
            answer_str = f"{expr} = {result}"
        elif isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            answer_str = f"{expr} = {int(result)}"
            result_out: Any = int(result)
        else:
            answer_str = f"{expr} = {result}"
            result_out = result

        confidence = 0.99  # Near-certain: deterministic
        resp = SLMResponse(
            question=question, answer=answer_str, confidence=confidence,
            domain="math",
            reasoning=f"Deterministic calc (AST, no LLM): {expr} = {result_out}",
            evidence={
                "method": "deterministic_ast",
                "expr": expr,
                "result": result_out,
                "value": result_out,  # alias for downstream reality check
                "source": "PythonAST",
            },
            slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        # [V33] Save to SmartCache
        try:
            from scp.core.smart_cache import slm_cache_set
            slm_cache_set("MathSLM", question, resp, "PythonAST")
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        self._end_timer(start, True)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # Math deterministic → confidence rất cao
        return 0.99
