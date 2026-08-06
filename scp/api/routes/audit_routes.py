"""SCP V105 — Audit endpoints (24/7 multi-source audit)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger("scp.api.audit")
router = APIRouter(prefix="/v105/audit", tags=["audit"])

@router.get("/stats")
async def audit_stats():
    """Get audit fetcher statistics."""
    from scp.core.audit_fetcher import get_audit_stats
    return get_audit_stats()

@router.get("/findings")
async def audit_findings(limit: int = 20, source: str = ""):
    """Get recent audit findings."""
    import json
    from pathlib import Path
    db = Path("data/audit_findings.jsonl")
    if not db.exists():
        return {"findings": [], "total": 0}
    findings = []
    with open(db, encoding="utf-8") as f:
        for line in f:
            try:
                finding = json.loads(line)
                if not source or finding.get("source") == source:
                    findings.append(finding)
            except Exception: pass
    findings.reverse()  # newest first
    return {"findings": findings[:limit], "total": len(findings)}
