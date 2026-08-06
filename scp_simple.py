"""
SCP Simple API — Verifier class cho developer experience (DX)

TÁI SAO: SCP v104.71 có 59 endpoints, 62k lines, cần 1.7s init.
Developer muốn dùng SCP chỉ cần 1 dòng:
    from scp_simple import Verifier
    v = Verifier()
    result = v.verify("Thủ đô Pháp?", "Paris")

Thiết kế:
  - Lazy loading: chỉ load Judge khi verify() lần đầu
  - Cache-less mode: không lưu KB (cho ứng dụng nhẹ)
  - BYO-Key: hỗ trợ OpenAI-compatible base_url
  - Sync + Async API
  - 90% nhu cầu → 1 method: verify()

Usage:
    # Cơ bản
    from scp_simple import Verifier
    v = Verifier()
    r = v.verify("2+2=?", "4")
    print(r.verdict, r.confidence, r.answer)

    # Async
    import asyncio
    async def main():
        v = Verifier()
        r = await v.async_verify("What is BTC price?", "$62000")
        print(r.verdict)
    asyncio.run(main())

    # Cache-less (không lưu KB)
    v = Verifier(cache=False)

    # BYO-Key (OpenAI-compatible)
    v = Verifier(llm_base_url="https://api.openai.com/v1", llm_api_key="sk-...")
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("scp_simple")


@dataclass
class VerificationResult:
    """Result từ SCP verify — đơn giản, dễ dùng."""
    verdict: str                    # "PASS" | "FAIL" | "UNKNOWN" | "CONFLICT"
    confidence: float               # 0.0 - 1.0
    answer: str                     # SCP's verified answer
    reasoning: str                  # Why this verdict
    sources: list[str] = field(default_factory=list)  # Data sources used
    domain: str = ""                # Question domain (math, geography, ...)
    elapsed_ms: float = 0.0         # Total processing time
    evidence: dict = field(default_factory=dict)  # Full evidence (optional)

    @property
    def is_pass(self) -> bool:
        """True if verdict is PASS with high confidence."""
        return self.verdict == "PASS" and self.confidence >= 0.7

    @property
    def is_fail(self) -> bool:
        """True if verdict is FAIL (answer is wrong)."""
        return self.verdict == "FAIL"

    @property
    def is_uncertain(self) -> bool:
        """True if SCP cannot verify (UNKNOWN or low confidence)."""
        return self.verdict in ("UNKNOWN", "CONFLICT") or self.confidence < 0.5

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "answer": self.answer,
            "reasoning": self.reasoning,
            "sources": self.sources,
            "domain": self.domain,
            "elapsed_ms": self.elapsed_ms,
        }


class Verifier:
    """SCP Verifier — simple API cho 90% use cases.

    Args:
        cache: If True (default), use SQLite KB. If False, cache-less mode.
        llm_base_url: OpenAI-compatible base URL (for BYO-Key). None = use default Ollama.
        llm_api_key: API key for LLM provider. None = use env var.
        mode: "fast" (skip V98 security) | "balanced" (default, all 11 phases) | "strict" (extra checks)
        dev: If True, no auth required (local development).

    Example:
        >>> from scp_simple import Verifier
        >>> v = Verifier()
        >>> r = v.verify("2+3=?", "5")
        >>> print(r.verdict, r.confidence)
        PASS 0.99
    """

    def __init__(
        self,
        cache: bool = True,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        mode: str = "balanced",
        dev: bool = True,
    ):
        self.cache = cache
        self.llm_base_url = llm_base_url
        self.llm_api_key = llm_api_key
        self.mode = mode
        self.dev = dev
        self._judge = None
        self._init_lock = threading.Lock()

        # Set env vars for SCP config
        if dev:
            os.environ.setdefault("SCP_DEV_MODE", "1")
        if llm_base_url:
            os.environ.setdefault("OPENROUTER_BASE_URL", llm_base_url)
        if llm_api_key:
            os.environ.setdefault("OPENROUTER_API_KEY", llm_api_key)
        if not cache:
            # Cache-less mode: disable learning engines
            os.environ.setdefault("SCP_DISABLE_LEARNING", "1")

    def _get_judge(self):
        """Lazy load Judge — only on first verify() call."""
        if self._judge is None:
            with self._init_lock:
                if self._judge is None:
                    logger.info("Initializing SCP Judge (lazy)...")
                    import time
                    t0 = time.time()
                    from scp.api_server import get_judge
                    self._judge = get_judge()
                    elapsed = time.time() - t0
                    logger.info(f"SCP Judge ready in {elapsed:.2f}s ({len(self._judge.slms)} SLMs)")
        return self._judge

    def verify(
        self,
        question: str,
        candidate_answer: str = "",
        session_id: str = "",
    ) -> VerificationResult:
        """Verify a question + optional candidate answer.

        Args:
            question: The question to verify (e.g. "Thủ đô Pháp?")
            candidate_answer: AI's answer to verify (e.g. "Paris"). If empty, SCP generates answer.
            session_id: Optional session ID for multi-turn tracking.

        Returns:
            VerificationResult with verdict, confidence, answer, sources.

        Example:
            >>> v = Verifier()
            >>> r = v.verify("2+2=?", "4")
            >>> assert r.verdict == "PASS"
        """
        import time
        t0 = time.time()

        judge = self._get_judge()
        v98_context = {
            "ip": "127.0.0.1",
            "headers": {},
            "session_id": session_id or "simple-api",
            "body": question,
            "_t0": t0,
        }

        # Run guardrails (L1+L2+L3)
        try:
            from scp.security.guardrails import check_input
            gr_result, _ = check_input(question)
            if not gr_result.passed:
                return VerificationResult(
                    verdict="FAIL",
                    confidence=0.0,
                    answer="[SCP: blocked by guardrail]",
                    reasoning=f"Guardrail block: {gr_result.reason}",
                    elapsed_ms=int((time.time() - t0) * 1000),
                )
        except Exception as e:
            logger.debug(f"Guardrail skip (non-critical): {e}")

        # Run full 11-phase pipeline
        verdict = judge.judge(
            question=question,
            ai_answer=candidate_answer,
            cycle_count=0,
            source="simple_api",
            v98_context=v98_context,
        )

        # Extract sources from SLM responses
        sources = []
        for r in (verdict.slm_responses or []):
            ev = r.get("evidence", {})
            if isinstance(ev, dict):
                src = ev.get("source", "")
                if src and src not in sources:
                    sources.append(src)

        return VerificationResult(
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            answer=verdict.final_answer,
            reasoning=verdict.reasoning[:500],
            sources=sources[:5],
            domain=verdict.domain,
            elapsed_ms=int((time.time() - t0) * 1000),
            evidence=verdict.evidence if self.mode == "strict" else {},
        )

    async def async_verify(
        self,
        question: str,
        candidate_answer: str = "",
        session_id: str = "",
    ) -> VerificationResult:
        """Async version of verify(). Uses asyncio.to_thread internally."""
        import asyncio
        return await asyncio.to_thread(
            self.verify, question, candidate_answer, session_id
        )

    def ask(self, question: str) -> VerificationResult:
        """Just ask a question — SCP generates + verifies answer.

        Example:
            >>> v = Verifier()
            >>> r = v.ask("Thủ đô Việt Nam?")
            >>> print(r.answer)
            Hà Nội
        """
        return self.verify(question, candidate_answer="")

    def check(self, statement: str) -> VerificationResult:
        """Check if a statement is true (fact-check mode).

        Example:
            >>> v = Verifier()
            >>> r = v.check("Nước sôi ở 100 độ C")
            >>> print(r.verdict)
            PASS
        """
        return self.verify(f"Câu sau có đúng không: {statement}", candidate_answer="Đúng")


# ============================================================
# CONVENIENCE — module-level functions
# ============================================================
_default_verifier: Verifier | None = None
_default_lock = threading.Lock()


def get_verifier() -> Verifier:
    """Get or create default Verifier singleton."""
    global _default_verifier
    if _default_verifier is None:
        with _default_lock:
            if _default_verifier is None:
                _default_verifier = Verifier()
    return _default_verifier


def verify(question: str, candidate_answer: str = "") -> VerificationResult:
    """One-line verify — uses default Verifier.

    Example:
        >>> from scp_simple import verify
        >>> r = verify("2+2=?", "4")
        >>> print(r.verdict)
        PASS
    """
    return get_verifier().verify(question, candidate_answer)


def ask(question: str) -> VerificationResult:
    """One-line ask — uses default Verifier."""
    return get_verifier().ask(question)


def check(statement: str) -> VerificationResult:
    """One-line fact-check — uses default Verifier."""
    return get_verifier().check(statement)


if __name__ == "__main__":
    # Demo
    print("=== SCP Simple API Demo ===\n")

    v = Verifier()

    # Example 1: Math
    r = v.verify("2+3=?", "5")
    print("Q: 2+3=? | A: 5")
    print(f"Verdict: {r.verdict} (conf={r.confidence:.2f})")
    print(f"Answer: {r.answer}")
    print(f"Sources: {r.sources}\n")

    # Example 2: Fact-check
    r = v.check("Nước sôi ở 100 độ C ở áp suất tiêu chuẩn")
    print("Statement: Nước sôi ở 100°C")
    print(f"Verdict: {r.verdict} (conf={r.confidence:.2f})\n")

    # Example 3: Ask
    r = v.ask("Thủ đô Pháp là gì?")
    print("Q: Thủ đô Pháp?")
    print(f"Answer: {r.answer}")
    print(f"Verdict: {r.verdict} (conf={r.confidence:.2f})")
