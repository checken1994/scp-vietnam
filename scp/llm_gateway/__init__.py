"""
SCP LLM Gateway — Unified LLM access layer.

TÁI SAO tách riêng: trước đây có 3 bản implement LLM call:
  1. runtime/llm_client.py (async httpx, retry, fallback) — inference path
  2. core/fast_learning_engine.py::_ask_ollama (sync urllib, no retry) — learning
  3. core/real_learning_engine.py::_ask_ollama (sync urllib, no retry) — learning

3 bản diverge trên 7 chiều: sync/async, endpoint, retry, prompt template,
token limit, stats, singleton. Learning path không có retry/fallback →
1 network glitch = mất câu hỏi. Inference path có retry nhưng learning
không được hưởng.

[ARCH-1 FIX] Tách thành 1 gateway duy nhất:
  - Async-first (httpx) + sync wrapper (cho background threads)
  - Provider adapters: Ollama, OpenRouter, Groq
  - ProviderRouter: ordered fallback (Ollama → OpenRouter → Groq)
  - Singleton get_gateway() shared by inference + learning
"""
from scp.llm_gateway.client import LLMGateway, chat_sync, get_gateway

__all__ = ["LLMGateway", "get_gateway", "chat_sync"]
