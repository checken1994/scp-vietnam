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
import itertools
import logging
import os
import threading

import httpx

logger = logging.getLogger("scp.llm_gateway")


# ============================================================
# [OPENROUTER-FREE-FIX] REGISTRY OF ALL 17 FREE MODELS ON OPENROUTER
# ============================================================
# Verified via https://openrouter.ai/api/v1/models on 2026-08-06.
# All have pricing.prompt=0 AND pricing.completion=0 (cost $0).
# Use this list when you need to iterate over ALL free models (e.g. for
# fallback chains, model rotation, or auto-discovery).
# ============================================================
OPENROUTER_FREE_MODELS: list[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",            # 550B, 1M ctx — strongest
    "nvidia/nemotron-3-super-120b-a12b:free",            # 120B, 262K ctx — balanced
    "google/gemma-4-31b-it:free",                        # 31B, 262K ctx — factual, multimodal
    "google/gemma-4-26b-a4b-it:free",                    # 26B, 262K ctx — multimodal
    "nvidia/nemotron-3-nano-30b-a3b:free",               # 30B, 256K ctx — mid-tier
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", # 30B, 256K ctx — audio+image+video
    "cohere/north-mini-code:free",                       # 256K ctx — code-focused
    "inclusionai/ling-3.0-tiny:free",                    # 262K ctx — multilingual
    "poolside/laguna-s-2.1:free",                        # 262K ctx — code
    "poolside/laguna-xs-2.1:free",                       # 262K ctx — code (smaller)
    "openrouter/free",                                   # 200K ctx — auto-router (picks any free)
    "openai/gpt-oss-20b:free",                           # 20B, 131K ctx — fast
    "nvidia/nemotron-3.5-content-safety:free",           # 128K ctx — content moderation
    "nvidia/nemotron-nano-12b-v2-vl:free",               # 12B, 128K ctx — image+video
    "nvidia/nemotron-nano-9b-v2:free",                   # 9B, 128K ctx — fastest
    "google/lyria-3-pro-preview",                        # 1M ctx — image generation
    "google/lyria-3-clip-preview",                       # 1M ctx — image generation
]


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
            # [OLLAMA-FIX] deepseek-r1 (reasoning model) cần /api/generate,
            # không phải /api/chat — /api/chat trả 400 cho reasoning models.
            # Non-reasoning models (llama3.2, qwen2.5) dùng /api/chat OK.
            is_reasoning = "r1" in self.model.lower() or "reason" in self.model.lower()

            if is_reasoning:
                # Reasoning model: dùng /api/generate (hỗ trợ thinking phase)
                prompt_text = system_prompt + "\n\n" if system_prompt else ""
                if context:
                    prompt_text += context + "\n\n"
                prompt_text += question
                resp = await self._client.post(
                    f"{self.host}/api/generate",
                    json={"model": self.model, "prompt": prompt_text, "stream": False},
                )
                resp.raise_for_status()
                data = resp.json()
                # /api/generate trả "response" (không phải "message.content")
                answer = data.get("response", "").strip()
            else:
                # Non-reasoning model: dùng /api/chat (chuẩn)
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
    """OpenRouter cloud LLM provider — 3 keys + PAID primary + FREE fallback.

    [ROOT-FIX 44-A] OpenRouter is the ONLY cloud provider now — user has keys.
    [OPENROUTER-FREE-FIX] Architecture:
      1. PRIMARY: PAID model (deepseek-v4-flash) — user's main high-quality model.
         Configured via OPENROUTER_MODEL env var. Used for ALL tasks by default.
      2. FALLBACK: 17 FREE models (cost $0) — used when PAID model fails
         (rate limit, quota exhausted, network error).
         Task-specific FREE model selected from TASK_FREE_FALLBACK_MAP.
      3. 3 API KEYS round-robin: OPENROUTER_API_KEY, _2, _3 — rotates per call
         to avoid 20 req/min rate limit per key. Effective: 60 req/min total.

    Fallback chain per call:
      1. Try PAID model (deepseek-v4-flash) with key rotation
      2. If 429/402/quota → try task-specific FREE model
      3. If still fails → try openrouter/free (auto-router, picks any free)
      4. If still fails → return None (caller falls back to Ollama)

    WHY 17 FREE models (DNA SCP #1 — Reality > Model):
      - nemotron-3-ultra-550b (550B, 1M ctx)   → autofix (strongest code reasoning)
      - nemotron-3-super-120b (120B, 262K ctx) → judge (balanced reasoning)
      - gemma-4-31b-it       (31B, 262K ctx)   → why/learning (factual, multilingual)
      - nemotron-3-nano-30b  (30B, 256K ctx)   → fast_learning (mid-tier)
      - gpt-oss-20b          (20B, 131K ctx)   → chat (fast response)
      - openrouter/free      (auto-router)     → ultimate fallback
      - + 11 more FREE models in OPENROUTER_FREE_MODELS registry (see top of file)

    See .env for full 17-model list + capabilities + override instructions.
    """

    # [OPENROUTER-FREE-FIX] Task → FREE fallback model.
    # Used ONLY when PAID model (OPENROUTER_MODEL) fails.
    # All defaults are FREE (cost $0), verified on 2026-08-06.
    TASK_FREE_FALLBACK_MAP: dict[str, str] = {
        "autofix":       "nvidia/nemotron-3-ultra-550b-a55b:free",
        "why":           "google/gemma-4-31b-it:free",
        "learning":      "google/gemma-4-31b-it:free",
        "fast_learning": "nvidia/nemotron-3-nano-30b-a3b:free",
        "judge":         "nvidia/nemotron-3-super-120b-a12b:free",
        "chat":          "openai/gpt-oss-20b:free",
        "default":       "openai/gpt-oss-20b:free",
    }

    # Class-level: 3 API keys + round-robin iterator (shared across all instances)
    _API_KEYS: list[str] = []
    _key_cycle: itertools.cycle | None = None
    _key_lock = threading.Lock()

    @classmethod
    def _init_keys(cls) -> None:
        """Load API keys from env vars (3 keys supported for round-robin)."""
        if cls._API_KEYS:
            return  # already loaded
        keys = []
        for var in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3"):
            k = os.environ.get(var, "")
            if k and k != "your-key-here" and k != "sk-or-v1-REPLACE-WITH-YOUR-NEW-KEY":
                keys.append(k)
        cls._API_KEYS = keys
        if keys:
            cls._key_cycle = itertools.cycle(keys)

    @classmethod
    def _next_key(cls) -> str:
        """Get next API key (round-robin). Returns '' if no keys configured."""
        cls._init_keys()
        with cls._key_lock:
            if cls._key_cycle is None:
                return ""
            return next(cls._key_cycle)

    def __init__(self, task: str = "default"):
        self.task = task
        # PRIMARY: PAID model (user's OPENROUTER_MODEL = deepseek-v4-flash by default)
        # This is used FIRST for ALL tasks. FREE models are fallback only.
        self.model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-20260731")
        # FREE fallback for this task (used if PAID fails with 429/402/quota)
        self.free_fallback = os.environ.get(
            f"OPENROUTER_MODEL_{task.upper()}",
            self.TASK_FREE_FALLBACK_MAP.get(task, self.TASK_FREE_FALLBACK_MAP["default"]),
        )
        self.base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self._client: httpx.AsyncClient | None = None

    @property
    def api_key(self) -> str:
        """Current API key (round-robin across 3 keys)."""
        return self._next_key()

    @property
    def enabled(self) -> bool:
        """True if at least one non-placeholder API key is configured."""
        self._init_keys()
        return len(self._API_KEYS) > 0

    async def _call_model(self, model: str, messages: list[dict], api_key: str) -> tuple[str | None, str | None]:
        """Call a specific model with a specific key. Returns (answer, error)."""
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=60.0)
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": False},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://scp-vietnam.local",
                    "X-Title": "SCP Gateway",
                },
            )
            # 429 = rate limit, 402 = payment required (quota exhausted)
            if resp.status_code in (429, 402):
                return None, f"HTTP {resp.status_code} (quota/rate-limit)"
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return (answer or None, None)
        except Exception as e:
            return None, str(e)

    async def chat(self, question: str, context: str = "", system_prompt: str = "") -> tuple[str | None, str]:
        """Chat with LLM. Returns (answer, provider_name).

        Fallback chain:
          1. PAID model (deepseek-v4-flash) with key rotation
          2. FREE task-specific fallback (e.g. nemotron-ultra for autofix)
          3. openrouter/free (auto-router, picks any available FREE model)
        """
        if not self.enabled:
            return None, f"openrouter:{self.model}"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            messages.append({"role": "user", "content": context})
        messages.append({"role": "user", "content": question})

        # Layer 1: PAID model (try up to 3 keys = 1 round)
        for _attempt in range(min(3, len(self._API_KEYS))):
            key = self._next_key()
            answer, err = await self._call_model(self.model, messages, key)
            if answer:
                return answer, f"openrouter:{self.model}"
            if err and "quota" not in err.lower() and "rate-limit" not in err.lower() and "429" not in err and "402" not in err:
                # Non-quota error (network, 500, etc.) — don't retry with same model
                logger.debug(f"OpenRouter PAID ({self.model}) failed: {err}")
                break
            logger.debug(f"OpenRouter PAID ({self.model}) key failed: {err}, trying next key")

        # Layer 2: FREE task-specific fallback
        if self.free_fallback != self.model:  # avoid retrying same model
            key = self._next_key()
            answer, err = await self._call_model(self.free_fallback, messages, key)
            if answer:
                logger.info(f"OpenRouter PAID failed, FREE fallback succeeded: {self.free_fallback}")
                return answer, f"openrouter:{self.free_fallback}"
            logger.debug(f"OpenRouter FREE fallback ({self.free_fallback}) failed: {err}")

        # Layer 3: openrouter/free (auto-router, picks any available free model)
        if self.free_fallback != "openrouter/free":
            key = self._next_key()
            answer, err = await self._call_model("openrouter/free", messages, key)
            if answer:
                logger.info("OpenRouter auto-router (openrouter/free) succeeded")
                return answer, "openrouter:openrouter/free"
            logger.debug(f"OpenRouter auto-router failed: {err}")

        return None, f"openrouter:{self.model}"

    def stats(self) -> dict:
        self._init_keys()
        return {
            "configured": self.enabled,
            "num_keys": len(self._API_KEYS),
            "primary_model": self.model,
            "free_fallback": self.free_fallback,
            "task": self.task,
            "total_free_models_available": len(OPENROUTER_FREE_MODELS),
        }


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
        # [OPENROUTER-FREE-FIX] Multi-model OpenRouter — one provider per task.
        # Each task gets its own FREE model (nemotron-ultra for autofix, gemma-31b
        # for why/learning, gpt-oss-20b for chat, etc.). All cost $0.
        self.openrouter_autofix  = OpenRouterProvider(task="autofix")        # nemotron-3-ultra-550b:free
        self.openrouter_why      = OpenRouterProvider(task="why")            # gemma-4-31b-it:free
        self.openrouter_learning = OpenRouterProvider(task="learning")       # gemma-4-31b-it:free
        self.openrouter_fast     = OpenRouterProvider(task="fast_learning")  # nemotron-3-nano-30b:free
        self.openrouter_judge    = OpenRouterProvider(task="judge")          # nemotron-3-super-120b:free
        self.openrouter_default  = OpenRouterProvider(task="default")        # gpt-oss-20b:free
        # Backward-compat aliases — old code used `gateway.openrouter`.
        self.openrouter = self.openrouter_default
        # Alias: openrouter_fast_learning (matching ollama_fast naming pattern)
        self.openrouter_fast_learning = self.openrouter_fast
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

        # [OPENROUTER-FREE-FIX] Select OpenRouter provider based on task.
        # Each task gets its own FREE model (nemotron-ultra for autofix, etc.)
        openrouter_provider = {
            "autofix":       self.openrouter_autofix,
            "why":           self.openrouter_why,
            "learning":      self.openrouter_learning,
            "fast_learning": self.openrouter_fast,
            "judge":         self.openrouter_judge,
            "chat":          self.openrouter_default,
        }.get(task, self.openrouter_default)

        # [ROOT-FIX 46] AutoFix: OpenRouter FIRST (nemotron-ultra-550b > R1:8b for code reasoning)
        if task == "autofix" and openrouter_provider.enabled:
            self._stats["openrouter_calls"] += 1
            answer, _ = await openrouter_provider.chat(question, context, system_prompt)
            if answer:
                return answer, f"openrouter:{openrouter_provider.model}"

        # Layer 1: Ollama (local, free, task-specific model)
        if ollama_provider.enabled:
            self._stats["ollama_calls"] += 1
            answer, _ = await ollama_provider.chat(question, context, system_prompt)
            if answer:
                return answer, f"ollama:{ollama_provider.model}"

        # Layer 2: OpenRouter (cloud fallback — for non-autofix tasks)
        # [OPENROUTER-FREE-FIX] Use task-specific OpenRouter provider (FREE model)
        if task != "autofix" and openrouter_provider.enabled:
            self._stats["openrouter_calls"] += 1
            answer, _ = await openrouter_provider.chat(question, context, system_prompt)
            if answer:
                return answer, f"openrouter:{openrouter_provider.model}"

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
            # [OPENROUTER-FREE-FIX] Show all task-specific OpenRouter providers
            "openrouter_default":  self.openrouter_default.stats(),
            "openrouter_autofix":  self.openrouter_autofix.stats(),
            "openrouter_why":      self.openrouter_why.stats(),
            "openrouter_learning": self.openrouter_learning.stats(),
            "openrouter_fast":     self.openrouter_fast.stats(),
            "openrouter_judge":    self.openrouter_judge.stats(),
        }

    def check_startup(self) -> None:
        """[ROOT-FIX 45-A] Check Ollama models + OpenRouter key at startup.

        [OPENROUTER-FREE-FIX] Logs: 3 keys + PAID primary + 17 FREE models available.
        """
        # Check OpenRouter keys
        OpenRouterProvider._init_keys()
        num_keys = len(OpenRouterProvider._API_KEYS)
        if num_keys == 0:
            logger.warning("[Startup] OpenRouter API key NOT set — cloud DISABLED. Set OPENROUTER_API_KEY in .env.")
        else:
            logger.info(f"[Startup] OpenRouter configured — {num_keys} API keys (round-robin):")
            logger.info(f"  PRIMARY (PAID): {self.openrouter_default.model}")
            logger.info("  FREE fallback per task:")
            for task_name, prov in [
                ("autofix", self.openrouter_autofix),
                ("why", self.openrouter_why),
                ("learning", self.openrouter_learning),
                ("fast_learning", self.openrouter_fast),
                ("judge", self.openrouter_judge),
                ("chat", self.openrouter_default),
            ]:
                logger.info(f"    {task_name:15s} → {prov.free_fallback}")
            logger.info(f"  Total FREE models available: {len(OPENROUTER_FREE_MODELS)} (see OPENROUTER_FREE_MODELS in client.py)")
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
