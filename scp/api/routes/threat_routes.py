"""SCP V105 — Threat + Harm endpoints (Layer 2+3)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("scp.api.threats")
router = APIRouter(prefix="/v105/threats", tags=["threats"])

@router.get("/ai-scan/stats")
async def ai_threat_stats():
    from scp.core.ai_threat_scanner import get_threat_stats
    return get_threat_stats()

@router.get("/ai-scan/findings")
async def ai_threat_findings(limit: int = 20, source: str = ""):
    db = Path("data/ai_threats.jsonl")
    if not db.exists(): return {"findings": [], "total": 0}
    findings = []
    with open(db, encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line)
                if not source or t.get("source") == source:
                    findings.append(t)
            except Exception: pass
    findings.reverse()
    return {"findings": findings[:limit], "total": len(findings)}

@router.get("/harm/stats")
async def harm_stats():
    from scp.core.harm_detector import get_harm_stats
    return get_harm_stats()

@router.get("/harm/incidents")
async def harm_incidents(limit: int = 20, harm_type: str = ""):
    db = Path("data/ai_harm_incidents.jsonl")
    if not db.exists(): return {"incidents": [], "total": 0}
    incidents = []
    with open(db, encoding="utf-8") as f:
        for line in f:
            try:
                inc = json.loads(line)
                if not harm_type or inc.get("harm_type") == harm_type:
                    incidents.append(inc)
            except Exception: pass
    incidents.reverse()
    return {"incidents": incidents[:limit], "total": len(incidents)}
