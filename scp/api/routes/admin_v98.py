"""
[Task 7-A] V98 Security endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,285 LOC god file. Tách 8 routes /v98/* vào module
này. Backward-compatible — public API paths/methods unchanged.

Routes:
  POST /v98/analyze-session      — Rogue AI detection on session
  POST /v98/run-simulation       — Trigger threat simulation
  POST /v98/run-intel-crawl      — Trigger threat intel crawl
  GET  /v98/status               — All V98 module status
  GET  /v98/counter/stats        — Counter response stats
  GET  /v98/canary/triggers      — Canary token triggers
  GET  /v98/error-store/stats    — ErrorStore stats
  GET  /v98/attack-memory/stats  — AttackPatternMemory stats
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

# Import shared deps from api_server (same pattern as api/chat.py)
from scp.api._shared import (
    _SCP_VERSION,
    SessionAnalyzeRequest,
    SimulationRequest,
    get_judge,
    verify_admin,
)

router = APIRouter(tags=["v98"])


@router.post("/v98/analyze-session")
async def analyze_session(req: SessionAnalyzeRequest, _admin: bool = Depends(verify_admin)):
    """Analyze session for rogue AI behavior (9 lenses)."""
    judge = get_judge()
    result = judge.analyze_session_rogue(req.session_logs, req.model_responses)
    if result is None:
        raise HTTPException(status_code=503, detail="RogueAIDetector not available")
    return result


@router.post("/v98/run-simulation")
async def run_simulation(req: SimulationRequest, _admin: bool = Depends(verify_admin)):
    """Trigger threat simulation cycle."""
    judge = get_judge()
    result = await judge.run_threat_simulation(count=req.count)
    if result is None:
        raise HTTPException(status_code=503, detail="ThreatSimulatorEngine not available")
    return result


@router.post("/v98/run-intel-crawl")
async def run_intel_crawl(_admin: bool = Depends(verify_admin)):
    """Trigger threat intelligence crawl."""
    judge = get_judge()
    result = await judge.run_threat_intel_crawl()
    return {"updates": result, "count": len(result)}


@router.get("/v98/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v98_status():
    """Get status of all V98 security modules."""
    judge = get_judge()
    return {
        "version": _SCP_VERSION,  # [FIX-12] single source
        "slms": len(judge.slms),
        "v98_modules": judge.get_v98_status(),
        "falsification": judge.falsification is not None,
        "error_store": judge.error_store is not None,
        "governance": judge.governance is not None,
    }


@router.get("/v98/counter/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def counter_stats():
    """Counter response engine stats."""
    judge = get_judge()
    if not judge.counter_response:
        raise HTTPException(status_code=503, detail="CounterResponseEngine not available")
    return judge.counter_response.stats()


@router.get("/v98/canary/triggers", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def canary_triggers(limit: int = 20):
    """Get canary token triggers."""
    judge = get_judge()
    if not judge.canary_monitor:
        raise HTTPException(status_code=503, detail="CanaryTokenMonitor not available")
    return {"triggers": judge.canary_monitor.get_all_triggers(limit=limit)}


@router.get("/v98/error-store/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def error_store_stats():
    """ErrorStore stats."""
    judge = get_judge()
    if not judge.error_store:
        raise HTTPException(status_code=503, detail="ErrorStore not available")
    return judge.error_store.stats()


@router.get("/v98/attack-memory/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def attack_memory_stats():
    """AttackPatternMemory stats."""
    judge = get_judge()
    if not judge.attack_memory:
        raise HTTPException(status_code=503, detail="AttackPatternMemory not available")
    return judge.attack_memory.stats()
