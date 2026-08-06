"""
SCP V99 — API Server
Copyright (c) 2026 Minh. MIT License.

FastAPI server exposing V98 pipeline qua HTTP.

Endpoints:
  POST /ask                          — Main: question → V98 pipeline → verdict
  POST /v1/chat/completions          — OpenAI-compatible (for PyRIT/garak)
  GET  /v1/models                    — OpenAI models list
  POST /v98/analyze-session          — Rogue AI detection on session
  POST /v98/run-simulation           — Trigger threat simulation
  POST /v98/run-intel-crawl          — Trigger threat intel crawl
  GET  /v98/status                   — All V98 module status
  GET  /v98/counter/stats            — Counter response stats
  GET  /v98/canary/triggers          — Canary token triggers
  GET  /v98/error-store/stats        — ErrorStore stats
  GET  /dashboard                    — HTML dashboard
  GET  /health                       — Health check
  GET  /                             — Root info
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("scp.api")

from scp.api_server_parts.helpers import (
    AskRequest,
    AskResponse,
    _extract_v98_context,
    _safe_fetch_url,
    get_judge,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

# V103 FIX: Auth cho admin endpoints — shared singleton with helpers
# (imported from helpers to ensure all Depends(_security) use the same instance)



# ============================================================
# [FIX-A P0-2] SSRF/LFI-safe URL fetcher
# ============================================================
# Replaces every `urllib.request.urlopen(<user-supplied URL>)` call site.
# Defenses:
#   1. scheme must be exactly http/https (rejects file://, ftp://, gopher://, ...)
#   2. hostname must NOT resolve to private/loopback/link-local/reserved/
#      multicast/unspecified IP (checked across ALL getaddrinfo results to
#      mitigate DNS-rebinding); literal IP forms in those ranges rejected too
#   3. strict User-Agent
#   4. [AUDIT-3 FIX] redirect following: ≤5 hops, each hop re-validated against
#      _is_disallowed_ip (prevents SSRF via 302 → http://169.254.169.254/...).
#      Was: no redirects at all → broke legitimate image CDNs (Imgur, S3 presigned,
#      Bit.ly, Google Photos). Now: follow safe redirects, block unsafe ones.
#   5. max_bytes cap via streaming read + early abort (no unbounded resp.read())
#   6. per-request timeout (default 8s)
#   7. proxy disabled (prevents SSRF bypass via HTTP_PROXY env var)
# Callers MUST run this via asyncio.to_thread to avoid blocking the event loop.
# On any violation raises ValueError; callers return generic HTTP 400 (do NOT
# echo the URL or internal error back to the client).
_SCP_SAFE_FETCH_UA = "SCP-V104/1.0 (security-safe-fetch)"




# ============================================================
# V98 RealityJudge init (singleton)
# ============================================================
# [FIX-12] Single source of truth for SCP version — import from package root.
# RC-1 FIX: TYPE_CHECKING import so the Optional["PredictiveOrchestrator"]
# annotation at line ~341 resolves at type-check time (was F821 — the symbol
# referenced as "PredictiveEngine" never existed; the real class is
# PredictiveOrchestrator, imported lazily inside get_judge()).
from typing import TYPE_CHECKING

from scp import __version__ as _SCP_VERSION
from scp.core.streaming_factcheck import StreamingFactChecker
from scp.meta.simple_explainer import SimpleExplainer
from scp.runtime.judge import RealityJudge

# V103 NEW: AttackCrawler — tải tấn công mới từ internet
from scp.security.attack_crawler import AttackCrawler
from scp.security.cross_language_learner import CrossLanguageLearner
from scp.security.image_voice_detector import ImageJailbreakDetector, VoiceJailbreakDetector

# V104 NEW: Multi-turn + Image/Voice + Cross-language + Streaming + Simple Explainer
from scp.security.multi_turn_tracker import MultiTurnTracker

if TYPE_CHECKING:
    from scp.prediction.predictive import PredictiveOrchestrator as PredictiveEngine

# [V104.48] Chat WebSocket + Code Evolution Agent
try:
    from scp.api.chat import router as chat_router
    _CHAT_AVAILABLE = True
except ImportError as e:
    logger.warning(f"V104.48 Chat router unavailable: {e}")
    _CHAT_AVAILABLE = False

# [Task 7-A] V98 + V100 admin routes extracted to modules — imported LAZILY below
# (after SessionAnalyzeRequest/SimulationRequest are defined) to avoid circular import.
_V98_V100_ROUTERS_AVAILABLE = False
v98_admin_router = None
v100_admin_router = None

# V104 FIX: Real Learning Engine — học từ Ollama + Local + News
from scp.core.real_learning_engine import RealLearningEngine

# V104.2 NEW: Fast Learning Engine — parallel + skip-known + compounding
try:
    from scp.core.fast_learning_engine import (  # noqa: F401 (availability check)
        FastLearningEngine,
        start_fast_learning_thread,
    )
    _V1042_AVAILABLE = True
except ImportError as e:
    logger.warning(f"V104.2 FastLearningEngine unavailable: {e}")
    _V1042_AVAILABLE = False

# [V104.47 RESTORE] V104.3 StartupOptimizer + V104.4 DataPartitioner
# TẠI SAO: 2 file này có trong V104.4 nhưng mất ở V104.19 baseline.
# Khôi phục từ V104.4 gốc.
try:
    from scp.core.startup_optimizer import (  # noqa: F401 (availability check)
        STARTUP_DEFER_SECONDS,
        cleanup_data_directory,
        deferred_background_start,
        get_data_directory_stats,
        run_startup_optimization,
    )
    _V1043_AVAILABLE = True
except ImportError as e:
    logger.warning(f"V104.3 StartupOptimizer unavailable: {e}")
    _V1043_AVAILABLE = False

try:
    from scp.core.data_partitioner import (  # noqa: F401 (availability check)
        DOMAIN_KEYWORDS,
        DOMAIN_TABLES,
        TTL_QUESTION_LOG,
        TTL_VERDICT_CACHE,
        TTL_VERDICT_CACHE_DB,
        BypassLessonsStore,
        DataPartitioner,
        ThreeTierCache,
        TTLExpirer,
        detect_domain,
        migrate_old_to_new,
    )
    _V1044_AVAILABLE = True
except ImportError as e:
    logger.warning(f"V104.4 DataPartitioner unavailable: {e}")
    _V1044_AVAILABLE = False

_judge: RealityJudge | None = None
# [FIX-10] Module-level lock for get_judge() double-checked locking.
# TẠI SAO: previously get_judge() had no lock — two concurrent /ask
# requests at startup both saw `_judge is None`, both instantiated
# RealityJudge (which spawns AttackCrawler + RealLearningEngine +
# FastLearningEngine + StartupOptimizer threads). Result: thread
# explosion + duplicate singletons. Lock + double-check prevents it.
_judge_lock = threading.Lock()
_background_task: asyncio.Task | None = None
_attack_crawler: AttackCrawler | None = None
# V104 NEW: Singletons
_multi_turn_tracker = MultiTurnTracker()
_image_detector = ImageJailbreakDetector()
_voice_detector = VoiceJailbreakDetector()
_cross_language_learner = CrossLanguageLearner()
_fact_checker = StreamingFactChecker()
_simple_explainer = SimpleExplainer()

# V104 FIX: Real Learning Engine singleton
_real_learning = RealLearningEngine(scp_db_path="data/v13.db", data_dir="data")
# V104.2 NEW: Fast Learning Engine singleton (parallel + skip-known + compounding)
_fast_learning: FastLearningEngine | None = None
if _V1042_AVAILABLE:
    _fast_learning = FastLearningEngine(scp_db_path="data/v13.db", data_dir="data")
# [V104.36 #56-wire] PredictiveEngine singleton — wired to production judge
# TẠI SAO: V104.35 #56 fixed SelfLearner to accept judge, but PredictiveEngine
# was never instantiated with judge → self-learning still trained throwaway instance.
# Now: _predictive_engine is created in get_judge() AFTER _judge exists, so it
# gets the PRODUCTION judge reference → real self-correction.
_predictive_engine: PredictiveEngine | None = None



# ============================================================
# Request/Response models
# ============================================================



class SimulationRequest(BaseModel):
    count: int = Field(50, ge=1, le=500)


# [Task 7-A] V98 + V100 admin routers — imported AFTER SessionAnalyzeRequest/
# SimulationRequest are defined above to avoid circular import.
# TẠI SAO: admin_v98.py needs SessionAnalyzeRequest/SimulationRequest from this
# module. If imported at top (line 290), api_server.py is still loading → ImportError.
try:
    from scp.api.routes.admin_v98 import router as v98_admin_router
    from scp.api.routes.admin_v100 import router as v100_admin_router
    _V98_V100_ROUTERS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"[Task 7-A] V98/V100 admin routers unavailable: {e}")
    _V98_V100_ROUTERS_AVAILABLE = False

# [Task 8-A] DASHBOARD_HTML extracted to api/dashboard_html.py
try:
    from scp.api.dashboard_html import DASHBOARD_HTML
except ImportError as e:
    logger.warning(f"[Task 8-A] dashboard_html unavailable: {e}")
    DASHBOARD_HTML = "<html><body>Dashboard unavailable</body></html>"

# [Task 9-B] V102-V105 + openai-compat + import routers — lazy import AFTER
# all shared state defined (avoids circular import: openai_compat needs
# _extract_v98_context, v104 needs _multi_turn_tracker, etc.).
_EXTRA_ROUTERS_AVAILABLE = False


# ============================================================
# Lifespan
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _background_task
    logger.info("=" * 60)
    logger.info("SCP V99 API Server starting...")
    logger.info("=" * 60)

    # ============================================================
    # [STARTUP-GATE] Pre-startup deep audit — chạy TRƯỚC khi server start
    # Nếu có bugs không fix được → DỪNG (server không start)
    # Nếu không có → server start bình thường
    # ============================================================
    # [FIX-1] SCP_SKIP_STARTUP_GATE — bypass audit for dev/known-good state
    if os.environ.get("SCP_SKIP_STARTUP_GATE", "0") == "1":
        logger.warning("[STARTUP-GATE] SCP_SKIP_STARTUP_GATE=1 — audit BYPASSED (dev mode)")
    else:
        try:
            from scp.autofix.runner import pre_startup_audit
            audit_result = pre_startup_audit()  # [ROOT-FIX 47] was max_bugs=50 (hardcoded) — now reads SCP_MAX_STARTUP_BUGS env (default 200)

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
            raise  # Re-raise để server không start
        except Exception as e:
            logger.warning(f"[STARTUP-GATE] Pre-startup audit failed (non-blocking): {e}")
            logger.warning("  Server sẽ start nhưng deep audit không chạy. Kiểm tra logs.")

    # ============================================================
    # Server start (chỉ đến đây nếu audit PASS)
    # ============================================================
    judge = get_judge()
    logger.info(f"SLMs: {len(judge.slms)}")
    logger.info(f"V98 status: {judge.get_v98_status()}")

    # Start background jobs
    try:
        _background_task = asyncio.create_task(judge.schedule_background_jobs())
        logger.info("Background scheduler started (ThreatSimulator 6h + IntelCrawler 12h)")
    except Exception as e:
        logger.warning(f"Background scheduler failed: {e}")

    # [AUTO-WIRE] Start deep audit scheduler — tự động chạy mỗi 24h
    try:
        import threading as _threading
        import time as _time

        from scp.autofix.runner import run_deep_audit

        def _deep_audit_loop():
            """Chạy deep audit mỗi 24h (86400 giây)."""
            # Delay 60s sau startup để server ổn định
            _time.sleep(60)
            while True:
                try:
                    logger.info("[AUTO] Deep audit cycle starting...")
                    results = run_deep_audit(max_bugs=int(os.environ.get("SCP_MAX_AUDIT_BUGS", "100")))  # [ROOT-FIX 47] was 20 (hardcoded)
                    logger.info(f"[AUTO] Deep audit: {results.get('processed', 0)} bugs processed, "
                                f"{results.get('fixed', 0)} auto-fixed")
                except Exception as e:
                    logger.warning(f"[AUTO] Deep audit failed: {e}")
                _time.sleep(86400)  # 24 giờ

        _audit_thread = _threading.Thread(target=_deep_audit_loop, daemon=True,
                                          name="scp-deep-audit-scheduler")
        _audit_thread.start()
        logger.info("[AUTO] Deep audit scheduler started (24h interval)")
    except Exception as e:
        logger.warning(f"[AUTO] Deep audit scheduler failed: {e}")

    # [AUTO-WIRE] Start attack mode auto-detection — tự bật khi bị tấn công
    try:
        from scp.autofix.engine import get_autofix_engine

        def _attack_mode_monitor():
            """Tự động bật attack mode khi threat level cao.

            Logic:
            - Đếm KILL events trong 10 phút gần nhất
            - Nếu > 20 KILLs trong 10 phút → attack mode ON
            - Nếu < 5 KILLs trong 10 phút → attack mode OFF
            """
            _time.sleep(120)  # Delay 2 phút sau startup
            while True:
                try:
                    eng = get_autofix_engine()
                    # Đếm notifications (KILL events) trong 10 phút gần nhất
                    from scp.core.db_manager import db_query_one as _dq
                    row = _dq(
                        "SELECT COUNT(*) as cnt FROM notifications "
                        "WHERE timestamp > ?",
                        (_time.time() - 600,)  # 10 phút gần nhất
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
                _time.sleep(300)  # Check mỗi 5 phút

        _attack_thread = _threading.Thread(target=_attack_mode_monitor, daemon=True,
                                           name="scp-attack-mode-monitor")
        _attack_thread.start()
        logger.info("[AUTO] Attack mode auto-monitor started (5min interval)")
    except Exception as e:
        logger.warning(f"[AUTO] Attack mode monitor failed: {e}")

    # ============================================================
    # [OPT-14 / Gà §8] External trust root verification — at startup,
    # verify that the 3 external anchors (independent audit tests, CI/CD
    # pipeline, human-approved constitution) are intact. SCP cannot
    # self-verify; it needs these external anchors. If any are missing or
    # tampered, log a WARNING (non-blocking — server still starts, but the
    # admin must investigate). This is the "Gốc tin cậy bên ngoài" principle.
    # ============================================================
    try:
        from scp.meta.external_trust import get_external_trust_root
        _et = get_external_trust_root(".")
        _et_result = _et.verify_external()
        if _et_result["passed"]:
            logger.info("[Gà §8] External trust roots verified ✅ "
                        "(audit tests + CI/CD + constitution)")
        else:
            logger.warning(
                f"[Gà §8] External trust BROKEN — missing: "
                f"{_et_result['missing']}, constitution_approved: "
                f"{_et_result['constitution_approved']}. "
                f"Server will start but external anchors are not intact."
            )
    except Exception as _et_err:
        logger.warning(f"[Gà §8] External trust verification failed: {_et_err}")

    yield

    if _background_task:
        _background_task.cancel()
    logger.info("SCP V99 API Server shutting down...")


# ============================================================
# FastAPI app
# ============================================================
app = FastAPI(
    title="SCP V104.48 — Self-Correcting Pipeline API",
    description="V98 security + 53 SLMs + FalsificationEngine + Governance + Chat + Evolution",
    version="V104.49.0",
    lifespan=lifespan,
)
# [FIX-A P0-3 Bug C] CORS hardening — was allow_origins=["*"] + methods=*
# + headers=* which combined with no-auth endpoints let any website call any
# route cross-origin and exfiltrate responses. Now: explicit origin allowlist
# via SCP_CORS_ORIGINS (comma-separated, default localhost:3000 for dev),
# restricted methods, restricted headers, no credentials (we use bearer, not
# cookies — credentials=True with wildcard origin is invalid anyway).
_cors_origins_raw = os.environ.get("SCP_CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:3000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)
# ============================================================
# [OPT-43] CSRF + HTTPS HARDENING
# ============================================================
# TẠI SAO: auditor flagged "no CSRF protection, no HTTPS config".
#
# CSRF:
#   FastAPI does not ship built-in CSRF middleware. However, SCP API uses
#   Bearer-token auth (Authorization: Bearer <token>) for state-changing
#   routes — NOT cookies. A CSRF attacker (evil.com) cannot forge the
#   Authorization header on a victim's browser because:
#     1. Cross-origin XHR/fetch cannot set custom `Authorization` header
#        without a CORS preflight, and our CORS allowlist (above) blocks
#        unapproved origins.
#     2. Even if preflight passed, the attacker does not possess the
#        victim's bearer token (it is not auto-attached like a cookie).
#   → Bearer-token auth = inherent CSRF protection. OWASP CSWSH + SOP
#     confirm: APIs that do NOT use cookies for auth are not vulnerable
#     to classical CSRF. (See OWASP CSRF Prevention Cheat Sheet §"Use
#     Standard Headers to Verify Origin" + §"Use Synchronizer Token".)
#   This is logged at startup for audit visibility.
#
# HTTPS:
#   HTTPSRedirectMiddleware is OPT-IN via SCP_FORCE_HTTPS=1. We do NOT
#   force HTTPS in dev (breaks localhost:8000 testing). Production
#   deployments set SCP_FORCE_HTTPS=1 (and typically run behind a TLS-
#   terminating reverse proxy anyway, so the redirect is a defense-in-
#   depth backstop, not the primary TLS layer).
# ============================================================
try:
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    if os.environ.get("SCP_FORCE_HTTPS", "0") == "1":
        app.add_middleware(HTTPSRedirectMiddleware)
        logger.info("[Security] HTTPS redirect enabled (SCP_FORCE_HTTPS=1)")
    else:
        logger.info("[Security] HTTPS redirect disabled (set SCP_FORCE_HTTPS=1 in prod)")
except ImportError:
    logger.debug("[Security] HTTPSRedirectMiddleware unavailable (older FastAPI)")
logger.info("[Security] CSRF protection: Bearer token auth (attacker cannot forge Authorization header)")

# [V104.48] Register chat router AFTER app creation
if _CHAT_AVAILABLE:
    app.include_router(chat_router, tags=["chat"])

# [Task 7-A] Register V98 + V100 admin routers (extracted from inline routes)
if _V98_V100_ROUTERS_AVAILABLE:
    app.include_router(v98_admin_router)
    app.include_router(v100_admin_router)





# [Task 9-B] Register V102-V105 + openai-compat + import routers.
# Lazy import AFTER all shared state defined (avoids circular import:
# openai_compat.py needs _extract_v98_context).
try:
    from scp.api.routes.import_routes import router as import_router
    from scp.api.routes.openai_compat import router as openai_compat_router
    from scp.api.routes.v102_v103_routes import router as v102_v103_router
    from scp.api.routes.v104_routes import router as v104_router
    from scp.api.routes.v105_routes import router as v105_router
    _EXTRA_ROUTERS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"[Task 9-B] V102-V105/import routers unavailable: {e}")
    _EXTRA_ROUTERS_AVAILABLE = False

if _EXTRA_ROUTERS_AVAILABLE:
    app.include_router(openai_compat_router)
    app.include_router(v102_v103_router)
    app.include_router(import_router)
    app.include_router(v104_router)
    app.include_router(v105_router)

# [Task 42-B / OPT-41] Register webhook router (POST /api/analyze, /api/register,
# GET /api/threats, /api/alerts, /api/systems) — additive, no existing route touched.
# DNA SCP #6 Evidence: external AI systems need a programmatic endpoint to send
# prompts for analysis. DNA SCP #9 No harm: webhook is read-only (analyze, no exec).
try:
    from scp.api.webhook import router as webhook_router
    app.include_router(webhook_router)
    _WEBHOOK_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"[Task 42-B] Webhook router unavailable: {e}")
    _WEBHOOK_ROUTER_AVAILABLE = False

# ============================================================
# POST /ask — Main endpoint
# ============================================================
@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    """Main endpoint — question → V98 pipeline → verdict.

    Pipeline:
      1. [V98] MemoryPoisoningGuard + AttackPatternMemory + ThreatDetector
      2. [V88] Route → SLM predict → RealityJudge
      3. [V97] FalsificationEngine + ErrorStore + Governance
      4. [V98] AttackPolicy + CounterResponse + Canary + AttackPatternMemory.record_bypass
    """
    t0 = time.time()
    judge = get_judge()
    v98_context = _extract_v98_context(request)
    v98_context["body"] = req.question
    if req.session_id:
        v98_context["session_id"] = req.session_id

    # [V104.17 #1 FIX] DoS protection — check rate limit before processing
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            client_ip = request.client.host if request.client else "unknown"
            dos_alert = judge.dos_protection.check_request(client_ip)
            # [V104.22 #2 FIX] dos_alert may be a dataclass — use getattr, not .get
            # (was: .get raised AttributeError → swallowed → no block)
            action = getattr(dos_alert, "action_taken", "") if dos_alert else ""
            should_block = (
                action in ("block", "throttle")
                or (isinstance(dos_alert, dict) and dos_alert.get("should_block"))
            )
            if should_block:
                return JSONResponse(
                    {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
                    status_code=429
                )
        except Exception as e:
            logger.debug(f"[V104.17] DoS check error: {e}")

    # [V104.45 #CP] TẠI SAO: /ask was text-only → image/voice jailbreak bypassed.
    # Fix: if image_url or voice_url provided, run detector BEFORE judge.
    # [FIX-A P0-2] Was urllib.request.urlopen(req.image_url) — accepted
    # file:///etc/passwd (LFI), http://169.254.169.254/... (cloud metadata
    # SSRF), internal IPs, followed redirects, no size cap, AND blocked the
    # event loop. Now: _safe_fetch_url (scheme/IP/redirect/size defenses) +
    # asyncio.to_thread (non-blocking). Voice path was also broken (called
    # detect(audio_url=...) which has no such kwarg) — now fetches bytes safely
    # and passes audio_bytes=... to the detector.
    _multimodal_block = False
    if req.image_url:
        try:
            _img_bytes = await asyncio.to_thread(_safe_fetch_url, req.image_url)
        except ValueError:
            # SSRF/LFI policy violation — reject with 400 (do NOT echo URL).
            logger.warning("[V104.45 #CP] /ask image_url rejected by _safe_fetch_url policy")
            raise HTTPException(status_code=400, detail="Invalid or disallowed image_url") from None
        except Exception as e:
            logger.debug(f"[V104.45 #CP] Image fetch error: {e}")
            _img_bytes = None
        if _img_bytes:
            try:
                _img_result = _image_detector.detect(image_bytes=_img_bytes)
                if _img_result and _img_result.jailbreak_detected:
                    logger.warning("[V104.45 #CP] Image jailbreak detected on /ask")
                    _multimodal_block = True
            except Exception as e:
                logger.debug(f"[V104.45 #CP] Image detect error: {e}")

    if req.voice_url:
        try:
            _voice_bytes = await asyncio.to_thread(_safe_fetch_url, req.voice_url)
        except ValueError:
            logger.warning("[V104.45 #CP] /ask voice_url rejected by _safe_fetch_url policy")
            raise HTTPException(status_code=400, detail="Invalid or disallowed voice_url") from None
        except Exception as e:
            logger.debug(f"[V104.45 #CP] Voice fetch error: {e}")
            _voice_bytes = None
        if _voice_bytes:
            try:
                _voice_result = _voice_detector.detect(audio_bytes=_voice_bytes)
                if _voice_result and _voice_result.jailbreak_detected:
                    logger.warning("[V104.45 #CP] Voice jailbreak detected on /ask")
                    _multimodal_block = True
            except Exception as e:
                logger.debug(f"[V104.45 #CP] Voice detect error: {e}")

    if _multimodal_block:
        return AskResponse(
            verdict="FAIL",
            final_answer="[SCP: Answer withheld — multimodal jailbreak detected]",
            confidence=0.0,
            domain="security",
            elapsed_ms=0,
            session_id=v98_context["session_id"],
        )

    # [CHATBOT-FIX] Nếu user không cung cấp ai_answer → gọi Ollama sinh câu trả lời
    # TẠI SAO: SCP thiết kế để verify AI answer, nhưng chat UI chỉ gửi question (không ai_answer)
    # → SLM không có gì verify → UNKNOWN/KILL
    # Fix: Gọi Ollama local (llama3.2) sinh câu trả lời như chatbot, rồi SCP verify
    # [ROOT-FIX 44-A] task="chat" → routes to llama3.2 (fastest — 3B model, low latency
    # for chat UI responsiveness).
    _ai_answer = req.ai_answer
    if not _ai_answer or not _ai_answer.strip():
        try:
            from scp.llm_gateway import chat_sync
            _ollama_answer, _provider = chat_sync(
                req.question,
                context="",
                system_prompt="Bạn là SCP — một trợ lý AI thông minh. Trả lời ngắn gọn, chính xác, bằng tiếng Việt.",
                task="chat",
            )
            if _ollama_answer:
                _ai_answer = _ollama_answer
                logger.info(f"[CHATBOT] Ollama ({_provider}) generated answer: {_ollama_answer[:80]}...")
        except Exception as _ollama_err:
            logger.warning(f"[CHATBOT] Ollama call failed: {_ollama_err}")
            # Fallback: không có ai_answer → SCP chạy SLM-only (old behavior)

    # [Task 34-A / OPT-22] AsyncMultiSourceVerifier — pre-judge fact check.
    # TÁI SAO: Task 33-A created AsyncMultiSourceVerifier but it was NOT used
    # in /ask. The verifier was tested in isolation but never wired into the
    # production hot path. DNA SCP #1 (Reality > Model) + #6 (Evidence)
    # violated: when a user asks "is X true?", the judge ran without first
    # checking what independent fact-check sources (Google Fact Check API,
    # 100+ publishers) had already concluded.
    #
    # Fix (additive — only triggers for fact-check questions):
    #   1. Detect fact-check intent via keyword match (en + vi).
    #   2. Pull sources from DataSourceRegistry that can_handle("fact_check").
    #   3. Run AsyncMultiSourceVerifier.verify_async() in PARALLEL — N sources
    #      → ~1× latency instead of N×.
    #   4. Interpret each source's verdict (GoogleFactCheck returns
    #      metadata.consensus="FALSE"/"TRUE"/"MIXED" — translated to
    #      contradicted/verified so the aggregator's consensus reflects it).
    #   5. If consensus == "contradicted" → pass fact_check_hint to judge
    #      via v98_context (judge can use it to downgrade confidence /
    #      override verdict — additive, judge remains in charge).
    #
    # Backward compat: any exception → silently fall through to judge (the
    # existing path is unchanged). No new dependency on a specific source
    # being enabled — if no fact-check sources can handle the question,
    # verify_async() returns consensus="unclear" and the hint is not set.
    _q_lower = req.question.lower() if req.question else ""
    _FACT_CHECK_KEYWORDS = (
        "true or false", "fact check", "is it true", "fact-check",
        "có thật", "đúng không", "có thật không", "kiểm chứng",
        "real or fake", "verify this claim",
    )
    if any(kw in _q_lower for kw in _FACT_CHECK_KEYWORDS):
        try:
            from scp.core.multi_source_verifier import AsyncMultiSourceVerifier
            from scp.data_sources import get_registry

            # Pull sources that self-report fact-check capability. Each source's
            # `can_handle(intent="fact_check")` decides — disabled sources
            # (e.g. GoogleFactCheck without API key) return False and are skipped.
            # Use the registry's public API (`get_sources_for_intent`) plus a
            # final can_handle() guard so disabled sources don't waste a slot.
            _fc_sources = []
            try:
                _registry = get_registry()
                _candidates = []
                try:
                    _candidates = _registry.get_sources_for_intent("fact_check") or []
                except Exception:
                    # Older registries without get_sources_for_intent — fall back
                    # to iterating the internal dict (mirrors SourceWatchlist
                    # pattern in judge.py:345).
                    _candidates = list(getattr(_registry, "_sources", {}).values())
                for _src in _candidates:
                    try:
                        if _src.can_handle("fact_check"):
                            _fc_sources.append(_src)
<<<<<<< HEAD
                    except Exception:  # noqa: S112
=======
                    except Exception:
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
                        continue
            except Exception as _reg_err:
                logger.debug(f"[OPT-22] registry lookup failed: {_reg_err}")

            async_verifier = AsyncMultiSourceVerifier()
            fact_result = await async_verifier.verify_async(
                req.question, sources=_fc_sources or None
            )

            # [OPT-22b] Translate per-source verdicts (GoogleFactCheck returns
            # metadata.consensus="FALSE"/"TRUE"/"MIXED" — the aggregator only
            # counts `verified`/`contradicted` keys, so consensus is "unclear"
            # unless we synthesize these keys). Map FALSE → contradicted,
            # TRUE → verified, then recompute consensus.
            _extra_verified = 0
            _extra_contradicted = 0
            for _raw in fact_result.get("results", []) or []:
                try:
                    _meta = _raw.get("metadata", {}) if isinstance(_raw, dict) else {}
                    _verdict = (str(_meta.get("consensus", "")).upper()
                                if _meta else "")
                    if _verdict == "FALSE":
                        _extra_contradicted += 1
                    elif _verdict == "TRUE":
                        _extra_verified += 1
<<<<<<< HEAD
                except Exception:  # noqa: S112
=======
                except Exception:
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
                    continue
            if _extra_verified or _extra_contradicted:
                fact_result["verified"] = fact_result.get("verified", 0) + _extra_verified
                fact_result["contradicted"] = fact_result.get("contradicted", 0) + _extra_contradicted
                if fact_result["contradicted"] > fact_result["verified"]:
                    fact_result["consensus"] = "contradicted"
                elif fact_result["verified"] > fact_result["contradicted"]:
                    fact_result["consensus"] = "verified"
                # else: keep "unclear" (tie)

            if fact_result.get("consensus") == "contradicted":
                logger.info(
                    f"[OPT-22] Pre-judge fact check: claim contradicted by "
                    f"{fact_result.get('contradicted', 0)} sources "
                    f"(sources_checked={fact_result.get('sources_checked', 0)})"
                )
                v98_context["fact_check_hint"] = fact_result
            elif fact_result.get("consensus") == "verified":
                logger.info(
                    f"[OPT-22] Pre-judge fact check: claim verified by "
                    f"{fact_result.get('verified', 0)} sources "
                    f"(sources_checked={fact_result.get('sources_checked', 0)})"
                )
                v98_context["fact_check_hint"] = fact_result
        except Exception as e:
            logger.debug(f"[OPT-22] async fact check failed: {e}")

    # [OPT-8/9] Use async judge with ReActAgent fallback.
    # Was: asyncio.to_thread(judge.judge, ...) — blocked event loop thread.
    # Now: judge_with_react_fallback() — async LLM call + ReActAgent when
    # SmartClassifier confidence < 0.5. Falls back to sync judge() on error.
    if hasattr(judge, "judge_with_react_fallback"):
        v = await judge.judge_with_react_fallback(
            question=req.question,
            ai_answer=_ai_answer,
            cycle_count=0,
            source=req.source,
            v98_context=v98_context,
        )
    else:
        # Fallback for older judge instances without async method
        v = await asyncio.to_thread(
            judge.judge,
            question=req.question,
            ai_answer=_ai_answer,
            cycle_count=0,
            source=req.source,
            v98_context=v98_context,
        )

    # [V104.41 #AC] TẠI SAO: DoS record_verdict never called → verdict-quality circuit dead.
    # Fix: record verdict after judge completes (same as OpenAI path).
    if hasattr(judge, 'dos_protection') and judge.dos_protection:
        try:
            judge.dos_protection.record_verdict(v.verdict)
        except Exception as e:
            logger.debug(f"[V104.41 #AC] DoS record_verdict error: {e}")

    # [FIX-CRIT-46 BUG 2] TẠI SAO: observe() was called with wrong kwargs
    # (response=, verdict=, confidence=, domain=) but ResponseMonitor.observe
    # signature is (prompt, response, latency_ms). TypeError was swallowed by
    # `except Exception` → behavioral detection stayed dead even after BUG 1
    # init fix. Fix: match actual signature — pass the prompt + latency_ms.
    elapsed_ms = (time.time() - t0) * 1000
    if hasattr(judge, 'response_monitor') and judge.response_monitor:
        try:
            judge.response_monitor.observe(
                prompt=req.question,
                response=v.final_answer or "",
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            logger.debug(f"[V104.41 #AD] ResponseMonitor observe error: {e}")

    # Build SLM trace — which SLMs ran, their answers, timing
    slm_trace = []
    for r in v.slm_responses:
        slm_trace.append({
            "domain": r.get("domain", "?"),
            "slm_name": r.get("slm_name", r.get("domain", "?")),
            "answer": str(r.get("answer", ""))[:200],
            "confidence": r.get("confidence", 0),
            "source": r.get("evidence", {}).get("source", "?") if isinstance(r.get("evidence"), dict) else "?",
            "processing_time_ms": r.get("processing_time", 0),
        })

    # Extract phase timings
    phase_timings = v.evidence.get("v100_phase_timings", {})

    # ===== V104 FIX: Multi-turn tracking (0.1ms, sync, OK trong pipeline) =====
    mt_result = None
    _mt_session = req.session_id or v98_context.get("session_id", "")
    if _mt_session:
        mt_result = _multi_turn_tracker.track(_mt_session, req.question, v.verdict)
        if mt_result.suspicious:
            # [V104.17 #6 FIX] Flag all verdicts (was: only PASS)
            if v.verdict in ("PASS", "FAIL", "UNKNOWN", "PARTIAL"):
                v.verdict = "FLAGGED"
                v.confidence *= 0.5
                if v.reasoning:
                    v.reasoning += f" [V104 Multi-turn: {mt_result.pattern_type}]"
            logger.warning(
                f"V104 Multi-turn attack: session={_mt_session}, "
                f"pattern={mt_result.pattern_type}, reason={mt_result.reason}"
            )
            v98_context["v104_multi_turn"] = mt_result.__dict__

    # ===== V104 FIX: Simple explanation (0.1ms, sync, OK) =====
    has_attack = bool(v98_context.get("v99_vietnamese_attack"))
    has_bypass = bool(v.evidence.get("v100_bypass_detected"))
    has_human_review = bool(v.evidence.get("v102_human_review_pending"))
    source_count = len([r for r in v.slm_responses if r.get("answer")])
    _simple_explainer.explain(
        verdict=v.verdict,
        confidence=v.confidence,
        domain=v.domain or "",
        sources=source_count,
        has_attack=has_attack,
        has_bypass=has_bypass,
        has_human_review=has_human_review,
        lineage_overlap=0.0,
        reliability_factor=v.evidence.get("v102_reliability_factor", 1.0),
    )

    # ===== V104.41 #X: Enforce KILL/FAIL/FLAGGED at API boundary =====
    # TẠI SAO: was always returning v.final_answer regardless of verdict.
    # Constitution: "abstain rather than fabricate". OpenAI path already had
    # "I cannot comply" for FAIL, but /ask returned full content.
    # Fix: clear answer for FAIL/FLAGGED/KILL; warn for UNKNOWN.
    _api_final_answer = v.final_answer
    _gov_decision = v.evidence.get("governance_decision", "")
    # [FIX-CRIT-27 BUG 6] Previously only `final_answer` was cleared on abstain —
    # but `slm_responses`, `slm_trace`, `reasoning`, `v100_claims`, `v103_antibodies`,
    # `speculative_mode`, and `v98_canary_token` still leaked the same content via
    # other fields. The abstain was cosmetic. Now: clear ALL leak fields on
    # FAIL/KILL/FLAGGED.
    _api_slm_responses = [{k: str(v2)[:200] for k, v2 in r.items()} for r in v.slm_responses]
    _api_slm_trace = slm_trace
    _api_reasoning = v.reasoning[:500] if v.reasoning else None
    _api_v100_claims = v.evidence.get("v100_claims")
    _api_v103_antibodies = v.evidence.get("v103_antibodies")
    _api_speculative_mode = v.evidence.get("speculative_mode")
    _api_v98_canary_token = v.evidence.get("v98_canary_token")
    _api_v98_guard = v.evidence.get("v98_guard_verdict") or v.evidence.get("v98_guard")
    _api_v98_classification = v.evidence.get("v98_classification")
    _api_v98_attack_policy = v.evidence.get("v98_attack_policy")
    _api_v98_counter_executed = v.evidence.get("v98_counter_executed")
    _api_v98_bypass_recorded = v.evidence.get("v98_bypass_recorded")
    _api_falsification_status = v.evidence.get("falsification_status")
    if _gov_decision == "KILL" or v.verdict in ("FAIL", "FLAGGED"):
        # [V104.41 #X] Don't return killed/failed content to client
        _api_final_answer = f"[SCP: Answer withheld — verdict: {v.verdict}]"
        if _gov_decision == "KILL":
            _api_final_answer = "[SCP: Answer withheld — Governance KILL]"
        # [FIX-CRIT-27 BUG 6] Clear ALL leak fields — abstain must be total.
        _api_slm_responses = []
        _api_slm_trace = []
        _api_reasoning = "[SCP: Answer withheld]"
        _api_v100_claims = None
        _api_v103_antibodies = None
        _api_speculative_mode = None
        _api_v98_canary_token = None  # honeypot — must not leak
        _api_v98_guard = None
        _api_v98_classification = None
        _api_v98_attack_policy = None
        _api_v98_counter_executed = None
        _api_v98_bypass_recorded = None
        _api_falsification_status = None
        logger.info(f"[V104.41 #X] API boundary enforcing abstain (all fields cleared): verdict={v.verdict}, gov={_gov_decision}")
    elif v.verdict == "UNKNOWN" and v.evidence.get("why_gate", {}).get("decision") == "REJECT":
        # [FIX-1] WHY Gate blocked PASS → UNKNOWN. Treat like FAIL/KILL:
        # withhold answer + clear all leak fields. Without this the WHY block
        # in judge.py was cosmetic — final_answer still shipped to client.
        _api_final_answer = "[SCP: Answer withheld — WHY Gate blocked]"
        _api_slm_responses = []
        _api_slm_trace = []
        _api_reasoning = "[SCP: WHY Gate blocked]"
        _api_v100_claims = None
        _api_v103_antibodies = None
        _api_speculative_mode = None
        _api_v98_canary_token = None
        _api_v98_guard = None
        _api_v98_classification = None
        _api_v98_attack_policy = None
        _api_v98_counter_executed = None
        _api_v98_bypass_recorded = None
        _api_falsification_status = None
        logger.info(f"[FIX-1] API boundary enforcing WHY Gate block (all fields cleared): verdict={v.verdict}")
    elif v.verdict == "UNKNOWN":
        # [V104.41 #X] For UNKNOWN, append warning but keep answer (low-risk)
        if _api_final_answer and "[SCP: unverified]" not in _api_final_answer:
            # [CHATBOT-FIX] Thêm explanation chi tiết cho chatbot
            _sources = []
            for r in v.slm_responses:
                if r.get("answer"):
                    _sources.append(f"  • {r.get('slm_name','?')}: {str(r.get('answer',''))[:60]}")
            _source_text = "\n".join(_sources) if _sources else "  (không có SLM nào trả lời)"
            _api_final_answer = _api_final_answer + f"\n\n💡 SCP đã kiểm tra:\n{_source_text}\n📊 Confidence: {v.confidence:.0%} — chưa đủ ngưỡng (cần ≥70%)"

    # ===== V104 FIX: Fact-check ASYNC (background, KHÔNG block /ask) =====
    # [V104.41 #Z] Only fact-check if we're actually returning an answer (not abstained)
    if v.verdict == "PASS" and _api_final_answer and len(_api_final_answer) > 20:
        try:
            asyncio.create_task(_async_fact_check(_api_final_answer, req.question, v98_context.get("session_id", "")))
        except Exception as e:
            logger.debug(f"[V104.37] api_server.py: e={e}")

    return AskResponse(
        verdict=v.verdict,
        final_answer=_api_final_answer,  # [V104.41 #X] enforced answer
        confidence=v.confidence,
        domain=v.domain,
        falsification_status=_api_falsification_status,
        governance_decision=v.evidence.get("governance_decision"),
        v98_guard=_api_v98_guard,
        v98_classification=_api_v98_classification,
        v98_attack_policy=_api_v98_attack_policy,
        v98_counter_executed=_api_v98_counter_executed,
        v98_canary_token=_api_v98_canary_token,
        v98_bypass_recorded=_api_v98_bypass_recorded,
        elapsed_ms=round(elapsed_ms, 1),
        session_id=v98_context["session_id"],
        # V105: Full pipeline trace
        slm_trace=_api_slm_trace,
        phase_timings=phase_timings,
        reasoning=_api_reasoning,
        slm_responses=_api_slm_responses,
        v100_claims=_api_v100_claims,
        v103_antibodies=_api_v103_antibodies,
        speculative_mode=_api_speculative_mode,
    )


# ===== V104: Async fact-check helper (background, không block /ask) =====
# [V104.45 #Z] TẠI SAO: fact-check was post-hoc only — FALSE claims logged but
# answer already sent to client. Fix: retract queue — if FALSE claims found,
# mark the answer as retracted in a queue that clients can poll.
_fact_check_retract_queue: list[dict] = []  # in-memory retract queue (cap 1000)


async def _async_fact_check(answer: str, question: str, session_id: str = ""):
    """Run fact-check in background — update stats + queue retract if FALSE."""
    try:
        results = await _fact_checker.check_text(answer[:500], question)
        if results:
            false_claims = [r for r in results if r.verdict == "FALSE"]
            if false_claims:
                logger.warning(
                    f"[V104.45 #Z] FactCheck: {len(false_claims)} FALSE claims found "
                    f"in answer to '{question[:50]}' — queuing retract"
                )
                # [V104.45 #Z] Add to retract queue
                _fact_check_retract_queue.append({
                    "question": question[:200],
                    "answer": answer[:200],
                    "false_claims": len(false_claims),
                    "session_id": session_id,
                    "timestamp": time.time(),
                })
                # Cap queue at 1000
                if len(_fact_check_retract_queue) > 1000:
                    _fact_check_retract_queue.pop(0)
    except Exception as e:
        logger.debug(f"V104 async fact-check error: {e}")


# ============================================================
# POST /v1/chat/completions — OpenAI-compatible (for PyRIT/garak)
# ============================================================




# ============================================================
# V102+V103 ENDPOINTS (V98 + V100 routes extracted to api/routes/ — Task 7-A)
# ============================================================











# ============================================================
# IMPORT — Excel/JSONL file upload
# ============================================================







# ============================================================
# Dashboard + Health + Root
# ============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """HTML dashboard — real-time V98 stats."""
    return HTMLResponse(DASHBOARD_HTML)


# ============================================================
# V103 NEW: Attack Crawler endpoints
# ============================================================






@app.get("/health")
async def health():
    judge = get_judge()
    # V104: Detailed health check
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "data")
    db_size = 0
    if _os.path.exists(data_dir):
        for f in _os.listdir(data_dir):
            fp = _os.path.join(data_dir, f)
            if _os.path.isfile(fp):
                db_size += _os.path.getsize(fp)
    return {
        "status": "ok",
        "version": _SCP_VERSION,  # [FIX-12] single source
        "slms": len(judge.slms),
        "v98_modules": sum(1 for v in judge.get_v98_status().values() if v != "inactive"),
        "routes": len(app.routes),
        "modules": "136+ Python files",
        "data_size_mb": round(db_size / (1024*1024), 2),
        "multi_turn_tracker": _multi_turn_tracker.stats(),
        "cross_language": _cross_language_learner.stats(),
        "fact_checker": _fact_checker.stats(),
    }


# ============================================================
# V104 NEW: Multi-turn + Image/Voice + Cross-language + SSE + Explain
# ============================================================




























# ============================================================
# V104.2 NEW: Fast Learning (Parallel + Skip-Known + Compounding)
# ============================================================







@app.get("/")
async def root():
    return {
        "name": "SCP",
        "version": _SCP_VERSION,  # [FIX-12] single source
        "description": "Self-Correcting Pipeline with V98 security",
        "endpoints": [
            "POST /ask",
            "POST /v1/chat/completions",
            "GET  /v1/models",
            "POST /v98/analyze-session",
            "POST /v98/run-simulation",
            "POST /v98/run-intel-crawl",
            "GET  /v98/status",
            "GET  /v98/counter/stats",
            "GET  /v98/canary/triggers",
            "GET  /v98/error-store/stats",
            "GET  /v98/attack-memory/stats",
            "GET  /dashboard",
            "GET  /health",
        ],
    }


# ============================================================
# Dashboard HTML
# ============================================================


# ============================================================
# [ARCH-2] AutoFix Permission Management — human reviews logic bug fixes
# ============================================================










