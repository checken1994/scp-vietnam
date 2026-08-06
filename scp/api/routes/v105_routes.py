"""
[Task 8-A] V105 AutoFix endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,144 LOC god file. Tách 6 routes /v105/* vào module
này. Backward-compatible — public API paths/methods unchanged.

Routes:
  GET  /v105/autofix/permissions                          — List pending permission requests
  POST /v105/autofix/permissions/{request_id}/approve     — Human approves a fix
  POST /v105/autofix/permissions/{request_id}/deny        — Human denies a fix
  POST /v105/autofix/attack-mode/{enabled}                — Toggle attack mode
  GET  /v105/autofix/stats                                — AutoFix engine stats
  POST /v105/autofix/run-audit                            — Trigger deep audit cycle
  GET  /v105/autofix/monitor                              — [OPT-31] AutoFixMonitor stats (success rate by bug_type/provider/diagnosis)
  POST /v105/autofix/cleanup-cache                        — [OPT-32] Remove legacy broken SmartCache disk entries
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
from scp.api._shared import verify_admin

router = APIRouter(tags=["v105"])


@router.get("/v105/autofix/permissions", dependencies=[Depends(verify_admin)])
async def v105_list_permissions():
    """List pending permission requests (logic bugs awaiting human approval)."""
    try:
        # [EXEC-1 A2] TẠI SAO: was `AutoFixEngine()` per-request → throwaway
        # instance → attack_mode / rate limits / cooldowns were all no-ops.
        # Use singleton so state persists across handlers.
        from scp.autofix.engine import get_autofix_engine
        eng = get_autofix_engine()
        pending = eng.permission_gate.list_pending()
        return {
            "pending": [
                {
                    "request_id": r.request_id,
                    "file": r.file,
                    "line": r.line,
                    "bug_type": r.bug_type,
                    "description": r.description,
                    "suggested_fix": r.suggested_fix,
                    "timestamp": r.timestamp,
                }
                for r in pending
            ],
            "count": len(pending),
        }
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.post("/v105/autofix/permissions/{request_id}/approve", dependencies=[Depends(verify_admin)])
async def v105_approve_permission(request_id: str, note: str = ""):
    """Human approves a logic bug fix. SCP then applies it.

    [OPT-14 / Gà §11] The `note` field is checked by UnderstandingChecker —
    the human must explain what the fix does in their own words. Empty,
    trivial, or copy-paste notes are rejected with HTTP 400 (the request
    was found, but understanding was not demonstrated).
    """
    try:
        # [EXEC-1 A2] singleton — see v105_list_permissions
        from scp.autofix.engine import get_autofix_engine
        eng = get_autofix_engine()
        # Pre-check: is the request even in the pending dict? If not, 404.
        if request_id not in eng.permission_gate._pending:
            raise HTTPException(404, "Permission request not found")
        ok = eng.permission_gate.approve(request_id, decided_by="api_admin", note=note)
        if not ok:
            # [OPT-14 / Gà §11] approve() returned False — either not found
            # (handled above) or understanding check failed. The latter means
            # the human note didn't demonstrate understanding of the fix.
            raise HTTPException(
                400,
                "Approval rejected — note does not demonstrate understanding "
                "(Gà §11). Re-approve with a real explanation in your own "
                "words: explain what the fix does, not just repeat the "
                "suggested_fix text."
            )
        # Apply the approved fix
        result = eng.apply_approved_fix(request_id)
        return {"approved": True, "fix_result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.post("/v105/autofix/permissions/{request_id}/deny", dependencies=[Depends(verify_admin)])
async def v105_deny_permission(request_id: str, note: str = ""):
    """Human denies a logic bug fix. SCP does not apply it."""
    try:
        # [EXEC-1 A2] singleton — see v105_list_permissions
        from scp.autofix.engine import get_autofix_engine
        eng = get_autofix_engine()
        ok = eng.permission_gate.deny(request_id, decided_by="api_admin", note=note)
        if not ok:
            raise HTTPException(404, "Permission request not found")
        return {"denied": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.post("/v105/autofix/attack-mode/{enabled}", dependencies=[Depends(verify_admin)])
async def v105_toggle_attack_mode(enabled: bool):
    """Toggle attack mode. When ON, SCP auto-applies restraints (Tier 4).
    Use during active attacks — SCP reacts faster than human review."""
    try:
        # [EXEC-1 A2] singleton — CRITICAL: with per-request AutoFixEngine(),
        # attack_mode toggle was LOST on next request (new engine defaulted to
        # False). Singleton persists the toggle across all handlers + runner.
        from scp.autofix.engine import get_autofix_engine
        eng = get_autofix_engine()
        eng.set_attack_mode(enabled)
        return {
            "attack_mode": enabled,
            "message": f"Attack mode {'ENABLED — SCP auto-applies restraints' if enabled else 'DISABLED — normal permission flow'}",
        }
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.get("/v105/autofix/stats", dependencies=[Depends(verify_admin)])
async def v105_autofix_stats():
    """Get AutoFix engine stats for monitoring."""
    try:
        # [EXEC-1 A2] singleton — stats reflect cumulative state across all
        # fixes applied by the engine (not just this request's throwaway).
        from scp.autofix.engine import get_autofix_engine
        eng = get_autofix_engine()
        return eng.stats()
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.post("/v105/autofix/run-audit", dependencies=[Depends(verify_admin)])
async def v105_run_deep_audit():
    """[EXEC-1 A5] Trigger a deep audit cycle. SCP scans itself for bugs and
    auto-fixes (Tier 1/2) or requests permission (Tier 3).

    This is the WIRING that was missing: AutoFixEngine.process_bug() existed
    but had zero callers — the 4-tier autonomy system was dead code. This
    endpoint invokes scp.autofix.runner.run_deep_audit() which:
      1. AST-scans scp/ for bare `except: pass`, undefined names, syntax errors
      2. For each finding, calls get_autofix_engine().process_bug(bug)
      3. Writes per-bug result to data/deep_audit_results.jsonl

    Returns a summary: {processed, fixed, permission_requested, skipped,
    details, engine_stats, source} — source='ast_scan' confirms the scanner
    actually ran (vs returning empty when no audit_bugs.jsonl exists).

    Idempotent: re-running hits the engine's cooldown (same bug not re-fixed
    within 1h) so safe to call repeatedly.
    """
    try:
        from scp.autofix.runner import run_deep_audit
        results = run_deep_audit()
        return {"audit_complete": True, "results": results}
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.get("/v105/autofix/monitor", dependencies=[Depends(verify_admin)])
async def autofix_monitor():
    """[OPT-31] AutoFixMonitor stats — success rate by bug_type/provider/diagnosis.

    DNA SCP #8 KB accumulation: expose AutoFix performance for admin dashboard.

    Distinct from `/v105/autofix/stats` (which returns AutoFixEngine cumulative
    counters like total bugs/fixed/skipped) — this endpoint returns the
    AutoFixMonitor view: per-bug-type / per-provider / per-diagnosis breakdowns
    + recent_attempts (last 10). Together they give the admin full visibility:
      - stats   = "what has the engine done?"
      - monitor = "how well is it doing it? which providers succeed?"
    """
    try:
        from scp.autofix.monitor import get_monitor
        monitor = get_monitor()
        stats = monitor.get_stats()
        recent = monitor.get_recent(limit=10)
        return {
            "status": "ok",
            "stats": stats,
            "recent_attempts": recent,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/v105/autofix/cleanup-cache", dependencies=[Depends(verify_admin)])
async def cleanup_cache():
    """[OPT-32] Clean up legacy broken SmartCache disk entries.

    TẠI SAO: Task 35-A fixed SLMResponse serialization going forward
    (_slm_response_to_dict at slm_cache_set boundary), but ~9 legacy rows
    already in `smart_cache_disk` table store the broken string repr
    (`"SLMResponse(question='...', ...)"`) instead of a proper JSON dict.
    Task 35-A made _disk_get return None for those (treated as cache miss),
    so they're harmless — but they waste disk space + pollute debug queries.

    DNA SCP #7 safe: cleanup ONLY matches rows whose value_blob decodes to a
    JSON string starting with `"SLMResponse(" — valid JSON dict entries
    (the new format) are untouched.
    """
    try:
        from scp.core.smart_cache import cleanup_legacy_cache_entries
        result = cleanup_legacy_cache_entries()
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e


@router.post("/v105/autofix/tier3-auto/{enabled}", dependencies=[Depends(verify_admin)])
async def v105_toggle_tier3_auto(enabled: str):
    """[V4.3] Toggle Tier-3 auto-approve at RUNTIME — no restart needed.

    User cấp quyền qua API thay vì .env:
      POST /v105/autofix/tier3-auto/1  → enable auto-approve
      POST /v105/autofix/tier3-auto/0  → disable auto-approve

    Safety guards vẫn active (1h timeout, 5/hour limit, etc.)
    Audit log ghi lại: who toggled, when, from what source.
    """
    import os as _os
    old_val = _os.environ.get("SCP_AUTO_APPROVE_TIER3", "0")
    new_val = "1" if enabled in ("1", "true", "on", "yes") else "0"
    _os.environ["SCP_AUTO_APPROVE_TIER3"] = new_val

    # Audit log
    from scp.autofix.engine import get_tier3_config
    config = get_tier3_config()

    # Log toggle event
    import json as _json
    import time as _time
    from pathlib import Path as _Path
    audit = _Path("data/tier3_auto_audit.jsonl")
    audit.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _time.time(),
        "action": "toggle",
        "old_value": old_val,
        "new_value": new_val,
        "source": "API (/v105/autofix/tier3-auto/)",
        "permission_source": config.get_permission_source(),
    }
    try:
        with open(audit, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        import logging
        logging.getLogger("scp.autofix").warning(f"Failed to write tier3 audit log: {e!r}")

    # Log to SCP console
    import logging as _logging
    _logging.getLogger("scp.autofix").info(
        f"[TIER3-AUTO] Permission TOGGLED via API: {old_val} → {new_val}\n"
        f"  Source: API endpoint\n"
        f"  Safety guards: {'ACTIVE' if new_val == '1' else 'N/A (disabled)'}\n"
        f"  Audit: {audit}"
    )

    return {
        "status": "ok",
        "old_value": old_val,
        "new_value": new_val,
        "enabled": new_val == "1",
        "message": (
            f"Tier-3 auto-approve {'ENABLED' if new_val == '1' else 'DISABLED'} "
            f"(was {old_val}). Safety guards active: 1h timeout, 5/hour limit, "
            f"no relaxation, no BareExceptPass."
        ),
        "audit_log": str(audit),
    }
