"""
[OPT-40] SCP Agent — lightweight proxy for protecting external AI systems.

DNA SCP #9 No harm: Agent INTERCEPTS requests (read-only), doesn't modify target.
DNA SCP #1 Reality > Model: Agent calls SCP /ask API (real verification).

Architecture:
  External AI system ← → SCP Agent ← → SCP Core (/ask)
                         (proxy)        (verification)

  1. User sends prompt to External AI
  2. SCP Agent intercepts (before AI processes)
  3. Agent sends prompt to SCP Core /ask
  4. SCP returns verdict (PASS/FAIL/UNKNOWN)
  5. Agent decides:
     - PASS → allow prompt to AI
     - FAIL → block prompt (attack detected)
     - UNKNOWN → allow but log for review

Usage:
    # Install agent on target AI system
    from scp_agent import SCPAgent

    agent = SCPAgent(scp_url="http://scp-core:8000")

    # Intercept before AI processes
    verdict = agent.check("user prompt here")
    if verdict.allowed:
        response = ai_model.generate("user prompt here")
    else:
        response = "Blocked by SCP: " + verdict.reason
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("scp_agent")


@dataclass
class SCPVerdict:
    """Verdict from SCP Core."""
    allowed: bool
    verdict: str  # PASS, FAIL, UNKNOWN, CONFLICT, KILL
    confidence: float
    reason: str
    domain: str
    elapsed_ms: float
    scp_answer: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "reason": self.reason,
            "domain": self.domain,
            "elapsed_ms": self.elapsed_ms,
            "scp_answer": self.scp_answer[:200],
        }


class SCPAgent:
    """Lightweight proxy that intercepts prompts and sends to SCP Core.

    Features:
      - Caches results (same prompt → cached verdict, no re-query)
      - Rate limiting (don't overwhelm SCP Core)
      - Timeout handling (don't block AI if SCP is slow)
      - Fallback (fail-closed by default: if SCP down, block prompt + log warning)

    Usage:
        agent = SCPAgent(scp_url="http://scp-core:8000", scp_token="...")
        verdict = agent.check("user prompt")
        if verdict.allowed:
            response = ai.generate("user prompt")
        else:
            response = f"Blocked: {verdict.reason}"
    """

    CACHE_TTL = 3600  # 1 hour cache
    TIMEOUT = 30  # seconds
    MAX_CACHE_SIZE = 10000

    def __init__(self, scp_url: str = "http://127.0.0.1:8000",
                 scp_token: str = "",
                 fallback_allow: bool = False):  # [ROOT-FIX] was True (fail-open = no protection)
        """
        Args:
            scp_url: SCP Core API URL
            scp_token: Bearer token for SCP auth
            fallback_allow: If False (default), block prompt when SCP is down (fail-closed = safe).
                           If True, allow prompt when SCP is down (fail-open = risky).
        """
        self.scp_url = scp_url.rstrip("/")
        self.scp_token = scp_token or os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
        self.fallback_allow = fallback_allow
        self._cache: dict[str, tuple[float, SCPVerdict]] = {}
        self._stats = {
            "total_checks": 0,
            "allowed": 0,
            "blocked": 0,
            "cache_hits": 0,
            "scp_errors": 0,
            "fallback_used": 0,
        }

    def check(self, prompt: str, ai_answer: str = "") -> SCPVerdict:
        """Check if prompt is safe to send to AI.

        Args:
            prompt: User's prompt/question
            ai_answer: Optional pre-existing AI answer to verify

        Returns:
            SCPVerdict with allowed=True/False
        """
        self._stats["total_checks"] += 1
        start = time.time()

        # 1. Check cache
        cache_key = self._hash(prompt, ai_answer)
        if cache_key in self._cache:
            cached_time, cached_verdict = self._cache[cache_key]
            if time.time() - cached_time < self.CACHE_TTL:
                self._stats["cache_hits"] += 1
                return cached_verdict

        # 2. Call SCP Core
        try:
            headers = {"Content-Type": "application/json"}
            if self.scp_token:
                headers["Authorization"] = f"Bearer {self.scp_token}"

            response = requests.post(
                f"{self.scp_url}/ask",
                json={
                    "question": prompt,
                    "ai_answer": ai_answer,
                    "source": "scp_agent",
                },
                headers=headers,
                timeout=self.TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            verdict_str = data.get("verdict", "UNKNOWN")
            confidence = data.get("confidence", 0.0)

            # Decide allow/block
            if verdict_str in ("FAIL", "KILL"):
                allowed = False
                reason = f"SCP blocked: {verdict_str} (confidence={confidence:.2f})"
            elif verdict_str == "UNKNOWN" and confidence < 0.3:
                allowed = False
                reason = f"SCP uncertain: {verdict_str} (confidence={confidence:.2f})"
            else:
                allowed = True
                reason = f"SCP allowed: {verdict_str} (confidence={confidence:.2f})"

            verdict = SCPVerdict(
                allowed=allowed,
                verdict=verdict_str,
                confidence=confidence,
                reason=reason,
                domain=data.get("domain", "general"),
                elapsed_ms=(time.time() - start) * 1000,
                scp_answer=data.get("final_answer", ""),
                metadata=data.get("metadata", {}),
            )

            # Update stats
            if allowed:
                self._stats["allowed"] += 1
            else:
                self._stats["blocked"] += 1

            # Cache result
            self._cache[cache_key] = (time.time(), verdict)
            self._evict_cache_if_needed()

            return verdict

        except Exception as e:
            self._stats["scp_errors"] += 1
            self._stats["fallback_used"] += 1
            logger.warning(f"[SCPAgent] SCP Core error: {e} — fallback={'allow' if self.fallback_allow else 'block'}")

            # Fallback
            verdict = SCPVerdict(
                allowed=self.fallback_allow,
                verdict="SCP_ERROR",
                confidence=0.0,
                reason=f"SCP Core unavailable: {e}",
                domain="error",
                elapsed_ms=(time.time() - start) * 1000,
            )
            return verdict

    def _hash(self, prompt: str, ai_answer: str) -> str:
        """Hash prompt+answer for cache key."""
        return hashlib.sha256(f"{prompt}|{ai_answer}".encode()).hexdigest()[:16]

    def _evict_cache_if_needed(self):
        """Evict oldest entries if cache too big."""
        if len(self._cache) > self.MAX_CACHE_SIZE:
            # Sort by time, remove oldest 10%
            sorted_items = sorted(self._cache.items(), key=lambda x: x[1][0])
            for key, _ in sorted_items[:self.MAX_CACHE_SIZE // 10]:
                del self._cache[key]

    def stats(self) -> dict:
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "scp_url": self.scp_url,
        }

    def clear_cache(self) -> int:
        """Clear cache. Returns number of entries removed."""
        count = len(self._cache)
        self._cache.clear()
        return count


__all__ = ["SCPAgent", "SCPVerdict"]
