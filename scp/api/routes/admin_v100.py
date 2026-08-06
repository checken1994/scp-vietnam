"""
[Task 7-A] V100 Knowledge endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,285 LOC god file. Tách 9 routes /v100/* vào module
này. Backward-compatible — public API paths/methods unchanged.

Routes:
  GET  /v100/status               — V100 knowledge + timing modules status
  POST /v100/crawl                — Trigger scheduled data crawl
  GET  /v100/antibodies/stats     — DomainAntibodySystem stats
  POST /v100/antibodies/check     — Run antibodies on a question + answer
  GET  /v100/knowledge/stats      — DomainKnowledgeStore stats
  GET  /v100/knowledge/search     — Search knowledge base
  GET  /v100/h8/stats             — H8 RedTeamBridge stats
  GET  /v100/h8/bypasses          — Get recent bypasses
  GET  /v100/h8/analyses          — Get recent bypass analyses
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

# Import shared deps from api_server (same pattern as api/chat.py)
from scp.api._shared import (
    get_judge,
    verify_admin,
)

router = APIRouter(tags=["v100"])


@router.get("/v100/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v100_status():
    """V100 knowledge + timing modules status."""
    judge = get_judge()
    return {
        "v100_modules": judge.get_v100_status(),
        "v98_modules": judge.get_v98_status(),
        "slms": len(judge.slms),
    }


@router.post("/v100/crawl")
async def v100_crawl(max_per_domain: int = 3, _admin: bool = Depends(verify_admin)):
    """Trigger scheduled data crawl."""
    judge = get_judge()
    result = await judge.run_scheduled_crawl(max_per_domain=max_per_domain)
    return result


@router.get("/v100/antibodies/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def antibody_stats():
    """DomainAntibodySystem stats."""
    # Antibodies run inline, not stored as instance — return static stats
    return {
        "total_antibodies": 38,
        "domains": ["medical", "finance", "legal", "security", "environment", "tech", "general"],
        "antibody_names": [a["name"] for a in __import__("scp.knowledge.antibody_system", fromlist=["ANTIBODIES"]).ANTIBODIES],
    }


@router.post("/v100/antibodies/check")
async def antibody_check(request: Request, _admin: bool = Depends(verify_admin)):
    """Run antibodies on a question + answer."""
    from scp.knowledge.antibody_system import DomainAntibodySystem
    body = await request.json()
    question = body.get("question", "")
    answer = body.get("answer", "")
    domain = body.get("domain", "general")
    system = DomainAntibodySystem()
    results = system.check(question, answer, domain)
    return {
        "total_run": len(results),
        "flagged": sum(1 for r in results if not r.passed),
        "results": [r.to_dict() for r in results],
    }


@router.get("/v100/knowledge/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def knowledge_stats():
    """DomainKnowledgeStore stats."""
    judge = get_judge()
    if not judge.domain_knowledge_store:
        raise HTTPException(status_code=503, detail="DomainKnowledgeStore not available")
    return judge.domain_knowledge_store.stats()


@router.get("/v100/knowledge/search", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def knowledge_search(q: str = "", domain: str = "", limit: int = 5):
    """Search knowledge base."""
    judge = get_judge()
    if not judge.domain_knowledge_store:
        raise HTTPException(status_code=503, detail="DomainKnowledgeStore not available")
    results = judge.domain_knowledge_store.search(q, domain, limit)
    return {
        "query": q,
        "results": [
            {
                "question": r.question[:100],
                "answer": r.answer[:100],
                "domain": r.domain,
                "source": r.source,
                "tier": r.source_tier,
                "confidence": r.confidence,
                "collected_at": r.collected_at,
            }
            for r in results
        ],
    }


@router.get("/v100/h8/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def h8_stats():
    """H8 RedTeamBridge stats."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return judge.h8_redteam.stats()


@router.get("/v100/h8/bypasses", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def h8_bypasses(limit: int = 20):
    """Get recent bypasses detected by H8."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return {"bypasses": judge.h8_redteam.get_recent_bypasses(limit)}


@router.get("/v100/h8/analyses", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def h8_analyses(limit: int = 20):
    """Get recent bypass analyses (chiều 2 — 'tại sao fail?')."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return {"analyses": judge.h8_redteam.get_recent_analyses(limit)}
