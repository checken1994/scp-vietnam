"""
SCP V105 — Prediction endpoints (Reality v4)

[COMPLETION-FIX] Wire PredictiveOrchestrator vào API:
- POST /v105/predictions/run-cycle — chạy 1 prediction cycle
- GET  /v105/predictions/pending — list pending predictions
- GET  /v105/predictions/all — list all predictions
- POST /v105/predictions/verify — verify pending predictions
- GET  /v105/predictions/stats — prediction statistics
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("scp.api.predictions")

router = APIRouter(prefix="/v105/predictions", tags=["predictions"])


class VerifyRequest(BaseModel):
    limit: int = Field(20, ge=1, le=100)


def _get_engine():
    """Get PredictiveOrchestrator singleton."""
    from scp.api_server import _predictive_engine
    if _predictive_engine is None:
        raise HTTPException(status_code=503, detail="PredictiveEngine not initialized")
    return _predictive_engine


@router.post("/run-cycle")
async def run_prediction_cycle():
    """Chạy 1 cycle: Crawl → Generate → Predict → Verify → Learn."""
    engine = _get_engine()
    try:
        result = engine.run_cycle()
        return {"status": "ok", "cycle": result}
    except Exception as e:
        logger.error(f"Prediction cycle failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/pending")
async def get_pending_predictions(limit: int = 20):
    """List pending predictions (chưa verify)."""
    engine = _get_engine()
    preds = engine.predictor.get_pending_predictions()
    return {"pending": preds[:limit], "total": len(preds)}


@router.get("/all")
async def get_all_predictions(limit: int = 100):
    """List all predictions (pending + verified)."""
    engine = _get_engine()
    preds = engine.predictor.get_all_predictions(limit=limit)
    return {"predictions": preds, "total": len(preds)}


@router.post("/verify")
async def verify_predictions(req: VerifyRequest):
    """Verify pending predictions (nếu đến check_date)."""
    engine = _get_engine()
    try:
        results = engine.verifier.verify_pending(limit=req.limit)
        return {"verified": len(results), "results": results}
    except Exception as e:
        logger.error(f"Verify failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/stats")
async def prediction_stats():
    """Prediction statistics."""
    engine = _get_engine()
    all_preds = engine.predictor.get_all_predictions(limit=10000)
    pending = [p for p in all_preds if p.get("status") == "pending"]
    verified = [p for p in all_preds if p.get("status") != "pending"]
    correct = [p for p in verified if p.get("status") == "correct"]
    wrong = [p for p in verified if p.get("status") == "wrong"]
    return {
        "total": len(all_preds),
        "pending": len(pending),
        "verified": len(verified),
        "correct": len(correct),
        "wrong": len(wrong),
        "accuracy": len(correct) / len(verified) if verified else 0.0,
    }
