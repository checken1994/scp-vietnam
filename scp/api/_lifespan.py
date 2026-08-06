"""Lifespan startup/shutdown for FastAPI app.

[Task 17-C] Extracted from api_server.py for modularity.

Startup:
  - Pre-startup deep audit gate (block server start if unfixed bugs)
  - RealityJudge init + background scheduler (ThreatSimulator/IntelCrawler)
  - Auto-wire deep audit scheduler (24h interval)
  - Auto-wire attack mode monitor (5min interval)

Shutdown:
  - Cancel background tasks
"""
from __future__ import annotations

import asyncio
import logging

# [COMPLETION-FIX] Multi-source audit fetcher
try:
    from scp.core.audit_fetcher import start_audit_fetcher, stop_audit_fetcher
except ImportError:
    start_audit_fetcher = None
    stop_audit_fetcher = None

try:
    from scp.core.ai_threat_scanner import start_scanner as start_ai_scanner
    from scp.core.ai_threat_scanner import stop_scanner as stop_ai_scanner
except ImportError:
    start_ai_scanner = None
    stop_ai_scanner = None

try:
    from scp.core.harm_detector import start_detector as start_harm_detector
    from scp.core.harm_detector import stop_detector as stop_harm_detector
except ImportError:
    start_harm_detector = None
    stop_harm_detector = None

try:
    from scp.core.audit_fetcher import start_audit_fetcher as _af_start
except ImportError:
    start_audit_fetcher = None
    stop_audit_fetcher = None
import os
import threading
import time as _time
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger("scp.api")


@asynccontextmanager
async def lifespan(app: FastAPI, *, get_judge, _background_task_holder: dict):
    """FastAPI lifespan — startup gate + background schedulers.

    Args:
        app: FastAPI app (unused but required by FastAPI signature convention).
        get_judge: callable returning the RealityJudge singleton.
        _background_task_holder: dict with single key "task" — used as a
            mutable holder for the asyncio background task so we can cancel
            it on shutdown. (Dict avoids `global` declaration.)
    """
    logger.info("=" * 60)
    logger.info("SCP V99 API Server starting...")
    logger.info("=" * 60)

    # ============================================================
    # [STARTUP-GATE] Pre-startup deep audit
    # [V4.6] PHÂN BIỆT: QUYỀN (capability) vs HÀNH ĐỘNG (execution)
    #
    # FULL_PRODUCTION = CÓ QUYỀN fix mọi bug, NHƯNG:
    #   - Startup gate CHỈ QUÉT + BÁO CÁO (không fix tất cả 1 lần)
    #   - AutoFix background fix TỪNG CÁI khi server đã listen
    #   - User có thể trigger manual qua /v105/autofix/run-audit
    #
    # → Server start NGAY (không block 100 phút)
    # → AutoFix chạy nền, fix từng bug KHI CẦN
    # ============================================================
    if os.environ.get("SCP_SKIP_STARTUP_GATE", "0") == "1":
        logger.warning("[STARTUP-GATE] SCP_SKIP_STARTUP_GATE=1 — audit BYPASSED (dev mode)")
    else:
        try:
            # [V4.6] Chỉ QUÉT, không FIX tất cả — tránh block server 100 phút
            from scp.autofix.runner import ast_scan_scp

            # Quét bugs (fast — chỉ scan, không LLM call)
            bugs = ast_scan_scp(max_files=int(os.environ.get("SCP_MAX_STARTUP_FILES", "100")))
            logger.info(f"[STARTUP-GATE] Scan complete: {len(bugs)} bugs found")
            logger.info("  → AutoFix sẽ xử lý background (không block server)")
            logger.info("  → Manual: POST /v105/autofix/run-audit (khi rảnh)")

            # KHÔNG fix tất cả — chỉ report
            # [G2-FIX PERM-10] blocking now based on critical bug count (not hardcoded False)
            # [REAUDIT-FIX] BugReport has no .severity attr — use .tier + .affects_logic instead
            critical_bugs = [b for b in bugs if getattr(b, "tier", None) and str(getattr(b, "tier", "")).endswith("PERMISSION") or getattr(b, "affects_logic", False)]
            audit_result = {
                "blocking": len(critical_bugs) > 0 and os.environ.get("SCP_SKIP_STARTUP_GATE", "0") != "1",
                "total_bugs": len(bugs),
                "critical_bugs": len(critical_bugs),
                "fixed": 0,
                "unfixed": len(bugs),
                "permission_requested": 0,
                "skipped": 0,
                "non_blocking_skipped": len(bugs),
                "blocking_skipped": 0,
                "bugs_queued": len(bugs),  # Sẽ fix background
            }

            if audit_result.get("blocking"):
                logger.error("[STARTUP-GATE] ❌ SERVER START BLOCKED")
                logger.error(f"  Total bugs: {audit_result['total_bugs']}")
                logger.error(f"  Auto-fixed: {audit_result['fixed']}")
                logger.error(f"  Unfixed:    {audit_result['unfixed']}")
                logger.error("")
                logger.error("  SCP không thể start khi có bugs chưa fix.")
                logger.error("  Cách fix:")
                logger.error("    1. Chạy: python -m scp.autofix.runner --verbose --ast-scan")
                logger.error("    2. Xem bugs pending: cat data/permission_requests.jsonl")
                logger.error("    3. Approve Tier 3: curl -X POST .../v105/autofix/permissions/{id}/approve")
                logger.error("    4. Hoặc fix thủ công rồi restart")
                logger.error("    5. Hoặc: set SCP_SKIP_STARTUP_GATE=1 (dev bypass)")
                logger.error("")
                raise RuntimeError(
                    f"[STARTUP-GATE] {audit_result['unfixed']} bugs cannot be auto-fixed. "
                    f"Server start blocked. Fix bugs, approve permissions, or set SCP_SKIP_STARTUP_GATE=1."
                )
            else:
                logger.info(f"[STARTUP-GATE] ✅ Pre-startup audit passed — "
                            f"{audit_result['total_bugs']} bugs found, "
                            f"{audit_result['fixed']} auto-fixed")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[STARTUP-GATE] Pre-startup audit failed (non-blocking): {e}")
            logger.warning("  Server sẽ start nhưng deep audit không chạy. Kiểm tra logs.")

    # ============================================================
    # Server start (audit PASS)
    # ============================================================
    judge = get_judge()
    logger.info(f"SLMs: {len(judge.slms)}")
    logger.info(f"V98 status: {judge.get_v98_status()}")

    try:
        _background_task_holder["task"] = asyncio.create_task(judge.schedule_background_jobs())
        logger.info("Background scheduler started (ThreatSimulator 6h + IntelCrawler 12h)")
    except Exception as e:
        logger.warning(f"Background scheduler failed: {e}")

    # [AUTO-WIRE] Deep audit scheduler (24h)
    try:
        def _deep_audit_loop():
            _time.sleep(60)
            while True:
                try:
                    logger.info("[AUTO] Deep audit cycle starting...")
                    from scp.autofix.runner import run_deep_audit
                    results = run_deep_audit(max_bugs=int(os.environ.get("SCP_MAX_AUDIT_BUGS", "100")))  # [ROOT-FIX 47] was 20
                    logger.info(f"[AUTO] Deep audit: {results.get('processed', 0)} bugs processed, "
                                f"{results.get('fixed', 0)} auto-fixed")
                except Exception as e:
                    logger.warning(f"[AUTO] Deep audit failed: {e}")
                _time.sleep(86400)

        _audit_thread = threading.Thread(target=_deep_audit_loop, daemon=True,
                                          name="scp-deep-audit-scheduler")
        _audit_thread.start()
        logger.info("[AUTO] Deep audit scheduler started (24h interval)")
    except Exception as e:
        logger.warning(f"[AUTO] Deep audit scheduler failed: {e}")

    # [AUTO-WIRE] Attack mode monitor (5min)
    try:
        def _attack_mode_monitor():
            _time.sleep(120)
            while True:
                try:
                    from scp.autofix.engine import get_autofix_engine
                    from scp.core.db_manager import db_query_one as _dq
                    eng = get_autofix_engine()
                    row = _dq(
                        "SELECT COUNT(*) as cnt FROM notifications WHERE timestamp > ?",
                        (_time.time() - 600,)
                    )
                    kill_count = row["cnt"] if row else 0
                    if kill_count > 20 and not eng.in_attack_mode:
                        eng.set_attack_mode(True)
                        logger.warning(f"[AUTO] Attack mode ENABLED — {kill_count} KILLs in 10min")
                    elif kill_count < 5 and eng.in_attack_mode:
                        eng.set_attack_mode(False)
                        logger.info(f"[AUTO] Attack mode DISABLED — {kill_count} KILLs in 10min (normal)")
                except Exception as e:
                    logger.debug(f"[AUTO] Attack mode monitor: {e}")
                _time.sleep(300)

        _attack_thread = threading.Thread(target=_attack_mode_monitor, daemon=True,
                                           name="scp-attack-mode-monitor")
        _attack_thread.start()
        logger.info("[AUTO] Attack mode auto-monitor started (5min interval)")
    except Exception as e:
        logger.warning(f"[AUTO] Attack mode monitor failed: {e}")

    yield

    if _background_task_holder.get("task"):
        _background_task_holder["task"].cancel()
    logger.info("SCP V99 API Server shutting down...")
