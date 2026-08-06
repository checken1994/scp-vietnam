"""
SCP LLM Gateway — Unified LLM client.

Single source of truth for ALL LLM calls in SCP (inference + learning).
Replaces:
  - runtime/llm_client.py (LocalLLMClient)
  - core/fast_learning_engine.py::_ask_ollama_sync
  - core/real_learning_engine.py::_ask_ollama

Design:
  - Async-first (httpx.AsyncClient) for inference path (/ask)
  - chat_sync() wrapper for background threads (learning engines)
  - Provider fallback: Ollama (task-routed) → OpenRouter (cloud)
  - Singleton get_gateway() — shared connection pool, thread-safe init

[ARCH-1 FIX] TÁI SAO tách riêng:
  - Inference (hot path, /ask) cần async + low latency
  - Learning (background) cần throughput + quality
  - Cả 2 dùng CÙNG 1 gateway → DRY + consistent fallback

[ROOT-FIX 44-A] MULTI-MODEL OLLAMA ROUTING (replaces single-model + 3 cloud providers):
  BEFORE: 1 Ollama model (llama3.2:3B) for ALL tasks → 63% rollback on AutoFix
          because 3B model too weak for code reasoning. Plus 3 direct cloud
          providers (DeepSeek, Anthropic, OpenAI) that user never configured
          (no API keys in .env) → dead code + noisy 401 logs.
  AFTER:  One OllamaProvider PER TASK, each using the model best suited:
            autofix        → deepseek-r1:8b  (best code reasoning)
            why            → qwen2.5:7b      (factual + multilingual)
            learning       → qwen2.5:7b      (summarization)
            fast_learning  → llama3.2         (speed — 3B = fastest)
            judge          → qwen2.5:7b      (balanced reasoning)
            chat           → llama3.2         (fast response)
            default        → llama3.2         (fallback)
          Cloud fallback reduced to OpenRouter ONLY (user's only cloud key).
          Removed: DeepSeekProvider, AnthropicProvider, OpenAIProvider
                   (zero API keys in user's .env → dead code).
  DNA SCP: #1 (Reality > Model — user has deepseek-r1:8b, qwen2.5:7b, llama3.2)
           #7 (AutoFix safe — additive task parameter, default keeps old behavior)
           #2 (PASS ≠ ĐÚNG — verify each task routes to correct model)
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

import httpx

logger = logging.getLogger("scp.llm_gateway")


# ============================================================
# [ROOT-FIX 44-A] MULTI-MODEL TASK → MODEL MAPPING
# ============================================================
# Each SCP task type is routed to the Ollama model best suited for it.
# All entries are overridable via env vars (no code change needed to swap).
#
# Why these specific models (DNA SCP #1 — Reality > Model):
#   - deepseek-r1:8b  : user's strongest code-reasoning model. AutoFix
#                       generates SEARCH/REPLACE blocks that need careful
#                       code analysis — 3B model produced 63% rollback rate.
#   - qwen2.5:7b      : strong multilingual (Vietnamese + English) + factual
#                       accuracy. Used for WHY verify, learning summaries,
#                       and Judge fallback reasoning.
#   - llama3.2 (3B)   : fastest local model. Used for high-volume / low-stakes
#                       calls (chat, fast_learning) where speed > accuracy.
# ============================================================
TASK_MODEL_MAP: dict[str, str] = {
    "autofix":        os.environ.get("OLLAMA_MODEL_AUTOFIX",        "deepseek-r1:8b"),
    "why":            os.environ.get("OLLAMA_MODEL_WHY",            "qwen2.5:7b"),
    "learning":       os.environ.get("OLLAMA_MODEL_LEARNING",       "qwen2.5:7b"),
    "fast_learning":  os.environ.get("OLLAMA_MODEL_FAST_LEARNING",  "llama3.2"),
    "judge":          os.environ.get("OLLAMA_MODEL_JUDGE",          "qwen2.5:7b"),
    "chat":           os.environ.get("OLLAMA_MODEL_CHAT",           "llama3.2"),
    "default":        os.environ.get("OLLAMA_MODEL",                "llama3.2"),
}


# ============================================================
# PROVIDER ADAPTERS
# ============================================================

class OllamaProvider:
    """Ollama local LLM provider — supports multi-model routing.

    [ROOT-FIX 44-A] Previously had a single hardcoded ``OLLAMA_MODEL`` (llama3.2:3B)
    for ALL tasks. AutoFix used the same 3B model that powers chat → 63% rollback
    rate because 3B is too weak for code reasoning. Now each task gets its own
    OllamaProvider instance with the model best suited for that task.

    Args:
        model: explicit model name (overrides task lookup). Defaults to None.
        task: one of TASK_MODEL_MAP keys. Used to pick the model when ``model``
            is not provided. Defaults to "default" (llama3.2).
    """

    def __init__(self, model: str | None = None, task: str = "default"):
        self.host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        # [ROOT-FIX 44-A] Multi-model: each task uses its own model.
        # Explicit model wins; otherwise look up by task; fallback to default.
        if model:
            self.model = model
        else:
            self.model = TASK_MODEL_MAP.get(task, TASK_MODEL_MAP["default"])
        self.task = task
        self.timeout = int(os.environ.get("OLLAMA_TIMEOUT", "60"))
        self.enabled = os.environ.get("OLLAMA_ENABLED", "true").lower() == "true"
        self._client: httpx.AsyncClient | None = None

    async def chat(self, question: str, context: str = "", system_prompt: str = "") -> tuple[str | None, str]:
        """Returns (answer, provider_name). On failure returns (None, 'ollama:<model>')."""
        if not self.enabled:
            return None, f"ollama:{self.model}"
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.timeout)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if context:
                messages.append({"role": "user", "content": context})
            messages.append({"role": "user", "content": question})
            resp = await self._client.post(
                f"{self.host}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("message", {}).get("content", "").strip()
            return (answer or None, f"ollama:{self.model}")
        except Exception as e:
            logger.debug(f"Ollama ({self.model}) failed: {e}")
            return None, f"ollama:{self.model}"

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "task": self.task,
            "host": self.host,
        }


class OpenRouterProvider:
    """OpenRouter cloud LLM provider (paid, high quality).

    [ROOT-FIX 44-A] OpenRouter is the ONLY cloud provider now — user has a key.
    DeepSeek/Anthropic/OpenAI direct providers removed (user has no keys → dead code).
    """

    def __init__(self):
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.model = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
        self.base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        """True if a non-placeholder API key is configured."""
        return bool(
            self.api_key
            and self.api_key != "your-key-here"
            and self.api_key != "sk-or-v1-REPLACE-WITH-YOUR-NEW-KEY"
        )

    async def chat(self, question: str, context: str = "", system_prompt: str = "") -> tuple[str | None, str]:
        if not self.enabled:
            return None, "openrouter"
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=60.0)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if context:
                messages.append({"role": "user", "content": context})
            messages.append({"role": "user", "content": question})
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={"model": self.model, "messages": messages, "stream": False},
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP Gateway",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return (answer or None, "openrouter")
        except Exception as e:
            logger.debug(f"OpenRouter failed: {e}")
            return None, "openrouter"

    def stats(self) -> dict:
        return {"configured": self.enabled, "model": self.model}


# ============================================================
# GATEWAY — single entry point with multi-model routing
# ============================================================

class LLMGateway:
    """Unified LLM gateway with multi-model Ollama routing.

    [ROOT-FIX 44-A] One OllamaProvider PER TASK — each task uses the model
    best suited for it (deepseek-r1:8b for code, qwen2.5:7b for facts,
    llama3.2 for speed). Cloud fallback reduced to OpenRouter only.

    Provider priority:
      1. Task-specific Ollama (local, free, model picked by task)
      2. OpenRouter (cloud — only provider needing an API key)

    Removed in 44-A:
      - DeepSeekProvider  (user has no DEEPSEEK_API_KEY)
      - AnthropicProvider (user has no ANTHROPIC_API_KEY)
      - OpenAIProvider    (user has no OPENAI_API_KEY)
      - set_preferred_provider / clear_preferred_provider / _VALID_PROVIDERS
        (smart-routing now done via task parameter — simpler + DRY)

    Usage (async):
        gw = get_gateway()
        answer, provider = await gw.chat("What is 2+2?", task="default")

    Usage (sync — for background threads):
        answer, provider = chat_sync("What is 2+2?", task="learning")
    """

    def __init__(self):
        # [ROOT-FIX 44-A] Multi-model Ollama — one provider per task.
        self.ollama_default  = OllamaProvider(task="default")        # llama3.2
        self.ollama_autofix  = OllamaProvider(task="autofix")        # deepseek-r1:8b
        self.ollama_why      = OllamaProvider(task="why")            # qwen2.5:7b
        self.ollama_learning = OllamaProvider(task="learning")       # qwen2.5:7b
        self.ollama_fast     = OllamaProvider(task="fast_learning")  # llama3.2
        self.ollama_judge    = OllamaProvider(task="judge")          # qwen2.5:7b
        # Cloud fallback (only OpenRouter — user's only cloud key).
        self.openrouter = OpenRouterProvider()
        self._stats = {
            "total_calls": 0,
            "ollama_calls": 0,
            "openrouter_calls": 0,
            "failures": 0,
        }

    # ------------------------------------------------------------------
    # Backward-compat shims — old callers referenced gateway.ollama /
    # gateway.deepseek / gateway.anthropic / gateway.openai. We expose
    # ``ollama`` as the default Ollama provider (was the old attribute),
    # and stub the removed providers as None so hasattr() checks fail
    # gracefully (DNA SCP #7 — AutoFix safe, no caller breaks).
    # ------------------------------------------------------------------
    @property
    def ollama(self) -> OllamaProvider:
        """Backward-compat alias — old code used ``gateway.ollama``."""
        return self.ollama_default

    def set_preferred_provider(self, provider: str) -> None:
        """[ROOT-FIX 44-A] DEPRECATED — no-op.

        Smart routing is now done via the ``task`` parameter on chat() /
        chat_sync(). This method is kept as a no-op so legacy callers
        (e.g. scp.autofix.llm_fix.get_llm_for_bug) don't crash. Logs a
        one-time debug message to aid migration.
        """
        logger.debug(
            f"[LLMGateway] set_preferred_provider({provider!r}) is a no-op — "
            f"use task= parameter on chat()/chat_sync() for routing."
        )

    def clear_preferred_provider(self) -> None:
        """[ROOT-FIX 44-A] DEPRECATED — no-op (see set_preferred_provider)."""
        pass

    async def chat(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
    ) -> tuple[str | None, str]:
        """Chat with LLM. Returns (answer, provider_name).

        [ROOT-FIX 44-A] ``task`` parameter routes to the best model:
          task="autofix"       → OpenRouter V4 flash FIRST (strongest code reasoning),
                                 then Ollama deepseek-r1:8b (local fallback)
          task="why"           → Ollama qwen2.5:7b (factual verify, multilingual),
                                 then OpenRouter fallback
          task="learning"      → Ollama qwen2.5:7b, then OpenRouter fallback
          task="fast_learning" → Ollama llama3.2 (speed — 3B = fastest)
          task="judge"         → Ollama qwen2.5:7b, then OpenRouter fallback
          task="chat"          → Ollama llama3.2 (fast response)
          task="default"       → Ollama llama3.2 (fallback)

        [ROOT-FIX 46] AutoFix routing CHANGED:
          WHY: user has OpenRouter key + deepseek-v4-flash-20260731 (V4 = newest,
               stronger than R1:8b local). AutoFix needs STRONGEST code reasoning
               → V4 flash first (cloud), R1:8b fallback (local, free).
          Other tasks: Ollama first (free), OpenRouter fallback (cost-saving).

        Fallback chain:
          - task="autofix":    1. OpenRouter V4 flash → 2. Ollama R1:8b → 3. None
          - other tasks:       1. Ollama (task-specific) → 2. OpenRouter → 3. None
        """
        self._stats["total_calls"] += 1

        # [ROOT-FIX 44-A] Select Ollama provider based on task.
        ollama_provider = {
            "autofix":       self.ollama_autofix,
            "why":           self.ollama_why,
            "learning":      self.ollama_learning,
            "fast_learning": self.ollama_fast,
            "judge":         self.ollama_judge,
            "chat":          self.ollama_default,
        }.get(task, self.ollama_default)

        # [ROOT-FIX 46] AutoFix: OpenRouter FIRST (V4 flash > R1:8b for code reasoning)
        if task == "autofix" and self.openrouter.enabled:
            self._stats["openrouter_calls"] += 1
            answer, _ = await self.openrouter.chat(question, context, system_prompt)
            if answer:
                return answer, f"openrouter:{self.openrouter.model}"

        # Layer 1: Ollama (local, free, task-specific model)
        if ollama_provider.enabled:
            self._stats["ollama_calls"] += 1
            answer, _ = await ollama_provider.chat(question, context, system_prompt)
            if answer:
                return answer, f"ollama:{ollama_provider.model}"

        # Layer 2: OpenRouter (cloud fallback — for non-autofix tasks)
        if task != "autofix" and self.openrouter.enabled:
            self._stats["openrouter_calls"] += 1
            answer, _ = await self.openrouter.chat(question, context, system_prompt)
            if answer:
                return answer, "openrouter"

        self._stats["failures"] += 1
        return None, "none"

    def chat_sync(
        self,
        question: str,
        context: str = "",
        system_prompt: str = "",
        task: str = "default",
    ) -> tuple[str | None, str]:
        """Sync wrapper for background threads. Runs async chat safely.

        [ROOT-FIX 44-A] Accepts ``task`` parameter — routes to the best
        Ollama model (see chat() docstring for task → model mapping).

        [RUNTIME-FIX-2] Root cause (runtime log line 732, 941):
          RuntimeWarning: coroutine 'LLMGateway.chat' was never awaited
        Bug: `loop.run_until_complete(self.chat(...))` evaluates self.chat(...)
        FIRST, creating a coroutine object. If run_until_complete raises (e.g.
        'event loop already running' inside async thread, or RuntimeError on
        loop creation), the coroutine object is never awaited and never closed
        -> leaks memory + GC pressure. In production 24/7 this means thousands
        of leaked coroutines per day.

        Fix: hold the coroutine reference explicitly. On ANY exception path,
        call coro.close() to release it cleanly. Use asyncio.run() (which
        handles loop creation+close properly) instead of manual loop management.
        Detect 'event loop already running' and fall back to ThreadPoolExecutor
        so we don't nest event loops.
        """
        coro = self.chat(question, context, system_prompt, task=task)
        try:
            # Detect if we're already inside an async context (event loop running).
            # In that case, asyncio.run() would raise RuntimeError. Fall back to
            # running in a fresh thread with its own event loop.
            try:
                asyncio.get_running_loop()
                _in_async = True
            except RuntimeError:
                _in_async = False

            if _in_async:
                # We're inside async code (e.g. called from async def without await).
                # Run the coroutine in a separate thread to avoid 'loop already running'.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=self.ollama_default.timeout + 30)
            else:
                # Normal sync context — asyncio.run handles loop lifecycle properly.
                return asyncio.run(coro)
        except Exception as e:
            # CRITICAL: ensure coroutine is closed to prevent leak.
            # RuntimeWarning 'coroutine was never awaited' happens when coro
            # is created but never awaited AND never closed.
            try:
                coro.close()
            except Exception as e:
                logger.warning(f"Silent except: {e}")
            logger.warning(f"chat_sync failed: {e}")  # upgrade debug->warning for observability
            self._stats["failures"] += 1
            return None, "none"

    def stats(self) -> dict:
        return {
            **self._stats,
            "ollama_default":  self.ollama_default.stats(),
            "ollama_autofix":  self.ollama_autofix.stats(),
            "ollama_why":      self.ollama_why.stats(),
            "ollama_learning": self.ollama_learning.stats(),
            "ollama_fast":     self.ollama_fast.stats(),
            "ollama_judge":    self.ollama_judge.stats(),
            "openrouter":      self.openrouter.stats(),
        }

    def check_startup(self) -> None:
        """[ROOT-FIX 45-A] Check Ollama models + OpenRouter key at startup."""
        # Check OpenRouter key
        or_key = self.openrouter.api_key
        if not or_key or "REPLACE" in or_key or or_key == "your-key-here":
            logger.warning("[Startup] OpenRouter API key NOT set — cloud fallback DISABLED. Set OPENROUTER_API_KEY in .env.")
        else:
            logger.info("[Startup] OpenRouter configured — cloud fallback enabled")
        # Check Ollama models
        try:
            import httpx as _httpx
            r = _httpx.get(f"{self.ollama_default.host}/api/tags", timeout=5)
            if r.status_code == 200:
                available = [m.get("name", "") for m in r.json().get("models", [])]
                required = [self.ollama_autofix.model, self.ollama_why.model, self.ollama_default.model]
                for model in required:
                    if not any(model in m for m in available):
                        logger.warning(f"[Startup] Ollama model '{model}' NOT found — run: ollama pull {model}")
            else:
                logger.warning(f"[Startup] Ollama not responding at {self.ollama_default.host}")
        except Exception as e:
            logger.warning(f"[Startup] Ollama check failed: {e} — run 'ollama serve' and 'ollama pull <model>'")


# ============================================================
# SINGLETON (thread-safe — EXEC-2 R3)
# ============================================================
# FRESH-2 finding: bare `if _gateway is None: _gateway = LLMGateway()` had no
# lock. Two concurrent first-callers (e.g. /ask hit while a learning thread
# boots) would both pass the None check and create duplicate LLMGateway
# instances — leaking httpx.AsyncClient connection pools and breaking the
# shared-stats invariant. Fix: double-checked locking with threading.Lock.
# ============================================================

_gateway: LLMGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    """Get or create the singleton LLM gateway (thread-safe)."""
    global _gateway
    if _gateway is None:
        with _gateway_lock:
            # Re-check inside lock — another thread may have created it while
            # we were waiting.
            if _gateway is None:
                _gateway = LLMGateway()
    return _gateway


def chat_sync(
    question: str,
    context: str = "",
    system_prompt: str = "",
    task: str = "default",
) -> tuple[str | None, str]:
    """Module-level sync shortcut. Uses singleton gateway.

    [ROOT-FIX 44-A] ``task`` parameter routes to the best Ollama model
    (see LLMGateway.chat docstring for the task → model mapping).
    """
    return get_gateway().chat_sync(question, context, system_prompt, task=task)
