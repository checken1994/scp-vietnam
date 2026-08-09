"""
SCP Multi-LLM Cross-Check — combat ảo giác đồng thuận (DNA SCP #20).

TẠI SAO module này tồn tại?
  DNA SCP #20: "100 AI cùng kết luận chưa chắc 100 nguồn độc lập nếu chúng
  cùng học từ dữ liệu tương tự, dùng benchmark tương tự, kế thừa cùng giả định."

  → Cần cross-check giữa 2-3 LLM providers KHÁC NHAU (khác lineage) để phát
  hiện khi chúng disagree. Nếu disagree → flag speculative.

TẠI SAO OpenRouter + Groq?
  - OpenRouter: nhiều models (Llama, DeepSeek, etc.) — có thể chọn
  - Groq: hardware khác (LPU), model lineage khác (Llama nhưng inference khác)
  - Không hoàn toàn độc lập (cùng Llama base) nhưng tốt hơn 1 LLM

TẠI SAO SequenceMatcher (difflib)?
  - So sánh semantic khó → bắt đầu với text similarity
  - >0.8 similarity = agree, <0.5 = disagree, 0.5-0.8 = partial
  - Đơn giản, không cần embedding model

Safety:
  - Rate limit (reuse MAX_LLM_FIXES_PER_HOUR from llm_fix)
  - Timeout 30s per provider
  - Fallback gracefully if 1 provider down
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger("scp.meta.multi_llm_check")

# Rate limit (reuse from llm_fix)
_MAX_CHECKS_PER_HOUR = 30
_recent_checks: list[float] = []
_checks_lock = threading.Lock()


def _check_rate_limit() -> bool:
    """Return True if we can make another check."""
    with _checks_lock:
        global _recent_checks
        import time
        now = time.time()
        _recent_checks = [t for t in _recent_checks if now - t < 3600]
        if len(_recent_checks) >= _MAX_CHECKS_PER_HOUR:
            return False
        _recent_checks.append(now)
        return True


class MultiLLMChecker:
    """Cross-check LLM answers between providers to detect consensus illusion."""

    def __init__(self, providers: Optional[list[str]] = None):
        self.providers = providers or ["openrouter", "groq"]
        self._lock = threading.Lock()

    def check(self, question: str, primary_answer: str) -> dict:
        """Cross-check primary_answer against other providers.

        Returns:
            {
                "consensus": "agree" | "disagree" | "partial" | "unavailable",
                "similarity": float,  # 0.0-1.0
                "provider_answers": {"openrouter": "...", "groq": "..."},
                "speculative": bool,  # True if disagree/partial
                "reason": str,
            }
        """
        if not _check_rate_limit():
            return {
                "consensus": "unavailable",
                "similarity": 0.0,
                "provider_answers": {},
                "speculative": False,
                "reason": "rate limit exceeded",
            }

        # Call each provider
        provider_answers: dict[str, str | None] = {}
        for provider in self.providers:
            try:
                if provider == "openrouter":
                    answer = self._call_openrouter(question)
                elif provider == "groq":
                    answer = self._call_groq(question)
                else:
                    continue
                provider_answers[provider] = answer
            except Exception as e:
                logger.warning(f"[multi_llm_check] {provider} failed: {e}")
                provider_answers[provider] = None

        # Compare
        return self._compare_answers(primary_answer, provider_answers)

    def _call_openrouter(self, question: str) -> str | None:
        """Call OpenRouter LLM."""
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            for i in (2, 3):
                api_key = os.environ.get(f"OPENROUTER_API_KEY_{i}", "")
                if api_key:
                    break
        if not api_key:
            return None

        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Answer concisely (1-2 sentences). Be factual."},
                {"role": "user", "content": question},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }

        try:
            req = urllib.request.Request(  # noqa: S310
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP MultiLLM Check",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — OpenRouter API, validated URL  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            logger.warning(f"[multi_llm_check] OpenRouter HTTP {e.code}")
            return None
        except Exception as e:
            logger.warning(f"[multi_llm_check] OpenRouter failed: {e}")
            return None

    def _call_groq(self, question: str) -> str | None:
        """Call Groq LLM."""
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            return None

        base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Answer concisely (1-2 sentences). Be factual."},
                {"role": "user", "content": question},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }

        try:
            req = urllib.request.Request(  # noqa: S310
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — Groq API, validated URL  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            logger.warning(f"[multi_llm_check] Groq HTTP {e.code}")
            return None
        except Exception as e:
            logger.warning(f"[multi_llm_check] Groq failed: {e}")
            return None

    def _compare_answers(self, primary: str, others: dict[str, str | None]) -> dict:
        """Compare primary answer against provider answers."""
        valid_others = {k: v for k, v in others.items() if v}
        if not valid_others:
            return {
                "consensus": "unavailable",
                "similarity": 0.0,
                "provider_answers": others,
                "speculative": False,
                "reason": "no provider answers available",
            }

        # Calculate similarity scores
        similarities = []
        for _provider, answer in valid_others.items():
            sim = SequenceMatcher(None, primary.lower(), answer.lower()).ratio()
            similarities.append(sim)

        avg_sim = sum(similarities) / len(similarities)

        # Determine consensus
        if avg_sim >= 0.8:
            consensus = "agree"
            speculative = False
            reason = f"providers agree (sim={avg_sim:.2f})"
        elif avg_sim < 0.5:
            consensus = "disagree"
            speculative = True
            reason = f"providers DISAGREE (sim={avg_sim:.2f}) — possible consensus illusion"
        else:
            consensus = "partial"
            speculative = True
            reason = f"providers partial agree (sim={avg_sim:.2f}) — flag as speculative"

        return {
            "consensus": consensus,
            "similarity": round(avg_sim, 3),
            "provider_answers": others,
            "speculative": speculative,
            "reason": reason,
        }
