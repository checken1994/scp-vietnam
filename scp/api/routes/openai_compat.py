"""
[Task 8-A] OpenAI-compatible endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,144 LOC god file. Tách 2 routes /v1/* vào module
này cho PyRIT/garak integration tests. Backward-compatible — public API
paths/methods unchanged.

Routes:
  POST /v1/chat/completions   — OpenAI-compatible chat (PyRIT/garak target)
  GET  /v1/models             — OpenAI models list
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
from scp.api._shared import _extract_v98_context, get_judge, logger

router = APIRouter(tags=["openai-compat"])


@router.post("/v1/chat/completions")
async def openai_chat(request: Request):
    """OpenAI-compatible endpoint — PyRIT/garak gọi endpoint này.

    Extracts user message → runs V98 pipeline → returns OpenAI-format response.
    """
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "scp-v99")

    # Extract last user message
    question = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            question = msg.get("content", "")
            break

    if not question:
        return JSONResponse({"error": {"message": "No user message", "type": "invalid_request"}}, status_code=400)

    judge = get_judge()
    v98_context = _extract_v98_context(request)
    v98_context["body"] = question

    # [V104.41 #AA] TẠI SAO: was calling judge.judge() synchronously in async def
    # → blocks event loop when SLM/API slow. PyRIT/garak parallel requests → server hang.
    # Fix: use asyncio.to_thread (same as /ask path).
    v = await asyncio.to_thread(
        judge.judge,
        question=question, ai_answer="", cycle_count=0, source="openai_compat",
        v98_context=v98_context
    )

    # [V104.41 #AC] TẠI SAO: DoS record_verdict never called → verdict-quality circuit dead.
    # Fix: record verdict after judge completes.
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            judge.dos_protection.record_verdict(v.verdict)
        except Exception as e:
            logger.debug(f"[V104.41 #AC] DoS record_verdict error: {e}")

    # [V104.41 #X] Enforce KILL/FAIL/FLAGGED at OpenAI boundary too (consistency with /ask)
    answer = v.final_answer
    _gov = v.evidence.get("governance_decision", "")
    if _gov == "KILL" or v.verdict in ("FAIL", "FLAGGED"):
        answer = "I cannot comply with this request."
    elif v.verdict == "UNKNOWN" and answer:
        answer = answer + "\n\n[SCP: unverified — confidence below threshold]"

    # [V104.41 #AB] TẠI SAO: canary was appended to visible content → attacker sees it
    # immediately → honeypot value destroyed. Fix: put canary in response metadata only,
    # NOT in visible content.
    canary = v.evidence.get("v98_canary_token")

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "scp_metadata": {
            "verdict": v.verdict,
            "confidence": v.confidence,
            "falsification_status": v.evidence.get("falsification_status"),
            "governance_decision": v.evidence.get("governance_decision"),
            "v98_guard": v.evidence.get("v98_guard_verdict", {}).get("recommendation") if v.evidence.get("v98_guard_verdict") else None,
            "v98_classification": v.evidence.get("v98_classification", {}).get("actor") if v.evidence.get("v98_classification") else None,
            "v98_counter_phase": v.evidence.get("v98_attack_policy", {}).get("phase") if v.evidence.get("v98_attack_policy") else 0,
            "v98_canary_token": canary,
            "v98_bypass_recorded": v.evidence.get("v98_bypass_recorded", False),
            "elapsed_ms": round((time.time() - v98_context.get("_t0", time.time())) * 1000, 1),
        },
    }


@router.get("/v1/models")
async def openai_models():
    """OpenAI-compatible models list."""
    return {
        "object": "list",
        "data": [{"id": "scp-v99", "object": "model", "created": int(time.time()), "owned_by": "scp"}],
    }
