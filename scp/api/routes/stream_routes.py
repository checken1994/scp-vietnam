"""
SCP V105 — Streaming /ask endpoint (real-time response)

[COMPLETION-FIX] Thêm streaming response cho /ask:
- POST /v105/ask/stream — streaming verdict (real-time)
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("scp.api.stream")

router = APIRouter(tags=["stream"])


class StreamAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    ai_answer: str = Field("", max_length=10000)


@router.post("/v105/ask/stream")
async def ask_stream(req: StreamAskRequest):
    """Streaming /ask — trả verdict từng bước real-time."""

    async def generate():
        try:
            # Step 1: Classify
            yield f"data: {json.dumps({'step': 'classify', 'status': 'running', 'ts': time.time()})}\n\n"

            from scp.api._shared import get_judge
            judge = get_judge()

            from scp.core.smart_classifier import SmartClassifier
            classifier = SmartClassifier()
            classification = classifier.classify(req.question)

            yield f"data: {json.dumps({'step': 'classify', 'status': 'done', 'domain': classification.domain, 'confidence': classification.confidence, 'method': classification.method})}\n\n"

            # Step 2: SLM predict
            yield f"data: {json.dumps({'step': 'slm_predict', 'status': 'running', 'ts': time.time()})}\n\n"

            # Step 3: Judge (full pipeline)
            yield f"data: {json.dumps({'step': 'judge', 'status': 'running', 'ts': time.time()})}\n\n"

            verdict = judge.judge(req.question, req.ai_answer, cycle_count=0)

            yield f"data: {json.dumps({'step': 'judge', 'status': 'done', 'verdict': verdict.verdict, 'confidence': verdict.confidence, 'domain': verdict.domain, 'reasoning': verdict.reasoning[:200] if verdict.reasoning else ''})}\n\n"

            # Final
            result = {
                "step": "final",
                "status": "complete",
                "question": req.question,
                "verdict": verdict.verdict,
                "confidence": verdict.confidence,
                "domain": verdict.domain,
                "final_answer": verdict.final_answer,
                "reasoning": verdict.reasoning,
                "evidence": verdict.evidence if hasattr(verdict, 'evidence') else {},
            }
            yield f"data: {json.dumps(result)}\n\n"

        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield f"data: {json.dumps({'step': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
