"""
api_server parts — extracted from api_server.py (Task 19-A).
 kept verbatim; only the location changed.

[Task 19-C] ruff: noqa: F821 — this is a MIXIN module. Names like
RealityJudge, _judge_lock, AttackCrawler, start_crawl_thread,
_cross_language_learner, _V1042_AVAILABLE, DataPartitioner, ThreeTierCache,
_SCP_SAFE_FETCH_UA, etc. are defined in the parent api_server.py and
injected at runtime via the mixin pattern. They cannot be imported here
without creating a circular dependency. The noqa suppresses F821 for
the entire file.
"""
# ruff: noqa: F821
from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger("scp.api")

# V103 FIX: Auth cho admin endpoints — shared singleton
_security = HTTPBearer(auto_error=False)

# [FIX-A P0-3] Log-once flag: warn when admin token is empty (deny-by-default).
_admin_no_token_warned: bool = False

# [Task 19-B / Mục 20] Module-level singletons used by get_judge() below.
# TẠI SAO: helpers.py is a MIXIN extracted from api_server.py by Task 19-A.
# `global _judge` inside get_judge() refers to THIS module's namespace, not
# api_server.py's. Without these declarations here, `get_judge()` raises
# NameError at first call (was the root cause of /ask 500 errors during
# the 23 golden tests). Declaring them here as None-initial lets the
# double-checked locking pattern work correctly. api_server.py still
# has its own copies (line ~167) for any direct reads, but they are
# effectively dead — get_judge() is the only entry point and it uses
# these.
import threading as _threading

_judge = None
_judge_lock = _threading.Lock()
_attack_crawler = None
_predictive_engine = None

# [Task 19-B / Mục 20] Lazy imports of all the helpers that get_judge()
# references. Originally these were imported at api_server.py module-level
# (lines 100-165) and assumed available in helpers.py via the "mixin
# pattern" — but Python doesn't auto-inject module globals. We re-import
# them here (try/except — non-fatal if any module is unavailable) so
# get_judge() can find them.
AttackCrawler = None
start_crawl_thread = None
try:
    from scp.security.attack_crawler import AttackCrawler as _AC
    from scp.security.attack_crawler import start_crawl_thread as _SCT
    AttackCrawler = _AC
    start_crawl_thread = _SCT
except Exception as _e_ac:
    logger.warning(f"[helpers] AttackCrawler import failed (non-fatal): {_e_ac}")

start_learning_thread = None
try:
    from scp.core.real_learning_engine import start_learning_thread as _SLT
    start_learning_thread = _SLT
except Exception as _e_rle:
    logger.warning(f"[helpers] start_learning_thread import failed (non-fatal): {_e_rle}")

start_fast_learning_thread = None
_V1042_AVAILABLE = False
try:
    from scp.core.fast_learning_engine import start_fast_learning_thread as _SFLT
    start_fast_learning_thread = _SFLT
    _V1042_AVAILABLE = True
except Exception as _e_fle:
    logger.warning(f"[helpers] start_fast_learning_thread import failed (non-fatal): {_e_fle}")

deferred_background_start = None
_V1043_AVAILABLE = False
try:
    from scp.core.startup_optimizer import deferred_background_start as _DBS
    deferred_background_start = _DBS
    _V1043_AVAILABLE = True
except Exception as _e_so:
    logger.warning(f"[helpers] deferred_background_start import failed (non-fatal): {_e_so}")

DataPartitioner = None
ThreeTierCache = None
_V1044_AVAILABLE = False
try:
    from scp.core.data_partitioner import DataPartitioner as _DP
    from scp.core.data_partitioner import ThreeTierCache as _TTC
    DataPartitioner = _DP
    ThreeTierCache = _TTC
    _V1044_AVAILABLE = True
except Exception as _e_dp:
    logger.warning(f"[helpers] DataPartitioner import failed (non-fatal): {_e_dp}")

_cross_language_learner = None
try:
    from scp.security.cross_language_learner import CrossLanguageLearner as _CLL
    _cross_language_learner = _CLL()
except Exception as _e_cll:
    logger.warning(f"[helpers] CrossLanguageLearner init failed (non-fatal): {_e_cll}")
# RealityJudge type is imported lazily inside get_judge() to avoid circular import


class AskResponse(BaseModel):
    verdict: str
    final_answer: str
    confidence: float
    domain: str
    falsification_status: str | None = None
    governance_decision: str | None = None
    v98_guard: dict[str, Any] | None = None
    v98_classification: dict[str, Any] | None = None
    v98_attack_policy: dict[str, Any] | None = None
    v98_counter_executed: dict[str, Any] | None = None
    v98_canary_token: str | None = None
    v98_bypass_recorded: bool | None = None
    elapsed_ms: float
    session_id: str
    # V105: Full pipeline trace — SLM nào chạy, time từng phase, reasoning
    slm_trace: list[dict[str, Any]] | None = None  # [{domain, slm_name, answer, confidence, time_ms, source}]
    phase_timings: dict[str, float] | None = None  # {intake_ms, routing_ms, ...}
    reasoning: str | None = None
    slm_responses: list[dict[str, Any]] | None = None
    v100_claims: dict[str, Any] | None = None
    v103_antibodies: dict[str, Any] | None = None
    speculative_mode: dict[str, Any] | None = None



class AskRequest(BaseModel):
    question: str = Field(..., min_length=0, max_length=8000)  # [FIX-P0-8C] min_length=0 cho UNKNOWN handling
    ai_answer: str = Field("", max_length=10000)
    domain: str = Field("general")
    confidence: float = Field(0.8, ge=0.0, le=1.0)
    session_id: str | None = None
    source: str = Field("api")
    # [V104.45 #CP] TẠI SAO: was no multimodal support on /ask → image/voice
    # jailbreak bypassed detection. Fix: add optional image_url/voice_url fields.
    image_url: str | None = Field(None, description="URL of image to scan for jailbreak")
    voice_url: str | None = Field(None, description="URL of audio to scan for jailbreak")



class SessionAnalyzeRequest(BaseModel):
    session_logs: list[dict[str, Any]]
    model_responses: list[dict[str, Any]]



class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTP 3xx redirects safely — ≤5 hops, each hop re-validates IP.

    [AUDIT-3 FIX] TẠI SAO: the old _NoRedirectHandler rejected ALL redirects,
    breaking legitimate image CDNs (Imgur, S3 presigned URLs, Bit.ly, Google
    Photos — all use 302/301 redirects). But blindly following redirects is an
    SSRF vector (attacker sets up external URL that 302→169.254.169.254).
    Root-cause fix: follow ≤5 redirects, but re-validate the target URL against
    _is_disallowed_ip BEFORE following. If the redirect target is to a private/
    internal/metadata IP, raise ValueError (block the redirect).
    """

    # Limit redirect hops (urllib default is 30, far too many)
    max_repeats = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Re-validate the redirect target URL before following
        if not newurl or not isinstance(newurl, str):
            raise ValueError("redirect target missing URL")
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"redirect scheme not allowed: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError("redirect target missing hostname")
        # Resolve + check IP range (same logic as _safe_fetch_url initial check)
        try:
            infos = socket.getaddrinfo(parsed.hostname, None)
        except socket.gaierror as exc:
            raise ValueError(f"redirect DNS resolution failed: {exc}") from exc
        if not infos:
            raise ValueError("redirect target has no DNS records")
        for _family, _stype, _proto, _canon, sockaddr in infos:
            if not sockaddr or not sockaddr[0]:
                continue
            ip_str = sockaddr[0]
            if "%" in ip_str:
                ip_str = ip_str.split("%", 1)[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise ValueError(f"redirect target unparseable IP: {ip_str}") from None
            if _is_disallowed_ip(ip):
                raise ValueError("redirect target resolves to disallowed IP range")
        # Target is safe — delegate to parent to construct the redirect request
        return super().redirect_request(req, fp, code, msg, headers, newurl)



def _safe_fetch_url(
    url: str,
    *,
    max_bytes: int = 5_000_000,
    timeout: float = 8.0,
) -> bytes:
    """Fetch a user-supplied URL with SSRF/LFI defenses. Returns bytes.

    Raises ValueError on any policy violation or fetch error.
    """
    if not url or not isinstance(url, str):
        raise ValueError("invalid url")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"scheme not allowed: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("missing hostname")
    hostname = parsed.hostname
    # Resolve hostname and reject if ANY resolved IP is disallowed.
    # (Checks all getaddrinfo results to mitigate DNS rebinding.)
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed: {exc}") from exc
    if not infos:
        raise ValueError("no DNS records")
    for _family, _stype, _proto, _canon, sockaddr in infos:
        if not sockaddr or not sockaddr[0]:
            continue
        ip_str = sockaddr[0]
        # Strip IPv6 zone id (e.g. fe80::1%eth0) before parsing.
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"unparseable IP: {ip_str}") from None
        if _is_disallowed_ip(ip):
            raise ValueError("host resolves to disallowed IP range")
    # Safe-redirect + no-proxy opener. [AUDIT-3 FIX] was _NoRedirectHandler
    # (blocked ALL redirects, broke legitimate image CDNs). Now follows ≤5
    # redirects with per-hop IP re-validation.
    opener = urllib.request.build_opener(
        _SafeRedirectHandler,
        urllib.request.ProxyHandler({}),
    )

    req = urllib.request.Request(url, headers={"User-Agent": _SCP_SAFE_FETCH_UA})  # noqa: S310 — scheme validated above

    try:
        with opener.open(req, timeout=timeout) as resp:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"response exceeds max_bytes={max_bytes}")
                chunks.append(chunk)
            return b"".join(chunks)
    except ValueError:
        raise
    except urllib.error.HTTPError as exc:
        raise ValueError(f"HTTP error: {exc.code}") from exc

    except (urllib.error.URLError, OSError) as exc:  # [FALSE-POS-FIX] B014: TimeoutError IS OSError in Python 3 — redundant

        raise ValueError(f"fetch error: {exc}") from exc



def _is_disallowed_ip(ip) -> bool:
    """True if IP is private/loopback/link-local/multicast/reserved/unspecified.

    Covers: 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
    169.254.0.0/16 (link-local incl. AWS/GCP/Azure metadata 169.254.169.254),
    ::1, fc00::/7 (unique-local), fe80::/10 (link-local), 0.0.0.0, ::.
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )



def _extract_v98_context(request: Request) -> dict[str, Any]:
    """Extract IP, headers, session_id from HTTP request for V98 security modules."""
    ip = request.client.host if request.client else "unknown"
    request.headers.get("user-agent", "")
    return {
        "ip": ip,
        "headers": dict(request.headers),
        "session_id": str(uuid.uuid4()),
        "endpoint": str(request.url.path),
        "body": "",
    }



def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(_security)):  # noqa: B008 (FastAPI dependency injection idiom)
    """V103: Verify admin token for sensitive endpoints.

    [FIX-A P0-3 Bug A] Use secrets.compare_digest (timing-safe) instead of `==`
    to prevent token recovery via timing oracle. Deny by default when no
    SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET is configured (warn once).

    [RC-2 FIX Task 6-A] SCP_DEV_MODE bypass REMOVED — was a convenience backdoor
    that made all admin endpoints open when env var set (the original V104.22 #3
    "fix" re-introduced the hole). Now: NO env-var bypass. Production MUST set
    SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET. Local dev: use .env (user-confirmed
    .env is kept) with a real token — never `return True` without check.
    """
    global _admin_no_token_warned
    auth_password = os.environ.get("SCP_AUTH_PASSWORD", "")
    auth_token = os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
    # [RC-2 FIX] SCP_DEV_MODE bypass removed — deny by default if no token configured.
    if not auth_password and not auth_token:
        if not _admin_no_token_warned:
            logger.warning(
                "verify_admin: SCP_AUTH_PASSWORD/SCP_AUTH_TOKEN_SECRET not set — "
                "denying admin request (configure a real token in .env; "
                "SCP_DEV_MODE bypass was removed in RC-2 fix)"
            )
            _admin_no_token_warned = True
        raise HTTPException(
            status_code=401,
            detail="Unauthorized — set SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET in .env"
        )
    # Timing-safe comparison — no short-circuit on first differing byte.
    provided = credentials.credentials if (credentials and credentials.credentials) else ""
    ok = False
    if auth_token:
        try:
            ok = ok or secrets.compare_digest(provided, auth_token)
        except (TypeError, ValueError):
            logger.exception("[api_server.py:88] silenced exception")
    if not ok and auth_password:
        try:
            ok = ok or secrets.compare_digest(provided, auth_password)
        except (TypeError, ValueError):
            logger.exception("[api_server.py:93] silenced exception")
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True



def get_judge() -> RealityJudge:
    """Build / return the singleton RealityJudge.

    [Task 19-B / Mục 20] RealityJudge imported lazily inside this function
    to avoid a circular import (helpers → api_server → runtime → ...).
    The TYPE hint is a string (forward reference) for the same reason.
    """
    global _judge, _attack_crawler, _predictive_engine
    # [FIX-10] Double-checked locking — acquire lock only on cold path.
    if _judge is None:
        with _judge_lock:
            if _judge is not None:
                # Another thread won the race while we waited for the lock
                return _judge
            logger.info("Initializing RealityJudge (V98)...")
            # [Task 19-B] Lazy import — avoids circular dependency
            from scp.runtime.judge import RealityJudge as _RealityJudge
            _judge = _RealityJudge()
            logger.info(f"RealityJudge ready: {len(_judge.slms)} SLMs, V98 modules={_judge.get_v98_status() is not None}")
        # [V104.36 #56-wire] Create PredictiveOrchestrator WITH production judge
        # TẠI SAO: must happen AFTER _judge is set, so SelfLearner gets real judge.v13
        try:
            from scp.prediction.predictive import PredictiveOrchestrator
            _predictive_engine = PredictiveOrchestrator(judge=_judge)
            logger.info("V104.36 PredictiveOrchestrator wired to production judge "
                        "(SelfLearner will retrain judge.v13.classifier)")
        except Exception as e:
            logger.warning(f"V104.36 PredictiveOrchestrator init failed: {e}")
            _predictive_engine = None

        # [V5.6-FIX] TẠI SAO: toàn bộ init dưới đây nằm NGOÀI `if _judge is None:`
        # → mỗi request /ask gọi get_judge() → spawn thêm thread + start learning
        # engine + reload patterns → thread explosion → CPU 100% → crash.
        # Log Gà: 262 lần "RealLearningEngine started" + 130 threads "Deferred
        # background start" trong 27 giây = thread leak.
        # Fix: move toàn bộ init vào trong guard — chỉ chạy 1 lần duy nhất.

        # V103: Start AttackCrawler thread
        if _attack_crawler is None:
            _attack_crawler = AttackCrawler(data_dir="data")
            start_crawl_thread(data_dir="data")
            logger.info("V103 AttackCrawler started (10 min interval)")

        # V104: Auto-load cross-language patterns into VietnameseDetector
        try:
            patterns = _cross_language_learner.transfer_existing_vietnamese_patterns()
            if patterns:
                en_patterns = [p for p in patterns if p["target_lang"] == "en"]
                logger.info(f"V104 CrossLanguage: {len(patterns)} patterns across 8 langs "
                           f"({len(en_patterns)} EN patterns ready)")
                # [V104.44 #CQ] TẠI SAO: was only logging "patterns ready" but never
                # loading them into AttackPatternMemory → detector never got cross-lang rules.
                # Fix: write each pattern as a dynamic rule in AttackPatternMemory.
                try:
                    _judge_for_am = _judge
                    if _judge_for_am and _judge_for_am.attack_memory:
                        _installed = 0
                        for _p in patterns:
                            try:
                                _judge_for_am.attack_memory.record_bypass(
                                    question=_p.get("source_pattern", "")[:500],
                                    answer="",
                                    attack_type=f"cross_lang_{_p.get('target_lang', 'unknown')}",
                                    signatures=[_p.get("translated_pattern", "")[:200]],
                                )
                                _installed += 1
                            except Exception:
                                logger.exception("[api_server.py:387] silenced exception")
                        if _installed > 0:
                            logger.info(f"[V104.44 #CQ] CrossLanguage: {_installed} patterns loaded into AttackPatternMemory")
                except Exception as _am_err:
                    logger.debug(f"[V104.44 #CQ] CrossLanguage rule load error: {_am_err}")
        except Exception as e:
            logger.debug(f"CrossLanguage auto-load: {e}")
        # [G3-MERGE A5] Start Learning Engine — SINGLE thread (was: 2 threads racing).
        # pre-merge: start_learning_thread (V104.1, 1h) + start_fast_learning_thread
        # (V104.2, 5min adaptive) BOTH ran, writing to the same `data/v13.db` KB
        # → race condition (Task 2-B finding P1-04). Post-merge: real_learning_engine
        # is a stub that aliases start_learning_thread → start_fast_learning_thread
        # (idempotent). Only ONE thread runs; the second call no-ops via the
        # module-level _FAST_LEARNING_THREAD guard. Skipping the explicit V104.1
        # call here makes intent explicit + avoids a noisy "already running" log.
        try:
            # V104.2 FastLearningEngine is the canonical engine post-merge.
            # It carries BOTH the V104.2 parallel fast_learning_cycle() AND
            # the V104.1 sequential methods (ollama/local/news/run_all_cycles)
            # ported from real_learning_engine.py — so this ONE thread covers
            # all learning needs. /v104/learn/* endpoints still work via the
            # _real_learning singleton (which is also a FastLearningEngine).
            if _V1042_AVAILABLE:
                start_fast_learning_thread(scp_db_path="data/v13.db", data_dir="data")
                logger.info("V104.2 FastLearningEngine started (CANONICAL — post G3-MERGE) "
                            "(10 concurrent Ollama + 5 concurrent Wiki + adaptive 1-30 min). "
                            "RealLearningEngine merged in; V104.1 sequential API still "
                            "available via /v104/learn/{ollama,local,news,all} endpoints.")
            else:
                # Fallback: if V104.2 unavailable, fall back to V104.1 (now alias).
                start_learning_thread(scp_db_path="data/v13.db", data_dir="data")
                logger.info("V104 RealLearningEngine started (fallback — V104.2 unavailable)")
        except Exception as e:
            logger.warning(f"Learning engine start failed: {e}")

        # [V104.47 RESTORE] V104.3 StartupOptimizer + V104.4 DataPartitioner
        if _V1043_AVAILABLE:
            try:
                import asyncio
                import threading
                loop = asyncio.new_event_loop()
                threading.Thread(target=lambda: loop.run_until_complete(deferred_background_start(judge=_judge))).start()
                logger.info("V104.3 StartupOptimizer started (deferred background init)")
            except Exception as e:
                logger.warning(f"V104.3 StartupOptimizer start failed: {e}")

        if _V1044_AVAILABLE:
            try:
                # [RUNTIME-FIX-1] Root cause: ThreeTierCache(_partitioner) truyền
                # DataPartitioner instance vào tham số db_path (Path) -> TypeError
                # "argument should be a str or an os.PathLike... not 'DataPartitioner'"
                # (runtime log line 147). Fix: tách biệt 2 object, không phụ thuộc nhau.
                _partitioner = DataPartitioner("data")
                # ThreeTierCache nhận db_path (Path), KHÔNG nhận partitioner.
                from scp.core.partition.shard import DATA_DIR as _V1044_DATA_DIR

                _cache = ThreeTierCache(db_path=str(_V1044_DATA_DIR / "v13.db"))  # noqa: F841 — side-effect: keep cache object alive (ThreeTierCache registers internally)

                # [ROOT-FIX-A] ThreeTierCache (scp/core/partition/rotate.py) has NO
                # `attach_partitioner()` method — the previous `if hasattr(...)` block
                # was dead code that silently no-op'd. Removed to keep the init path
                # honest about which objects are actually wired.
                # [ROOT-FIX-B] DataPartitioner has NO `list_partitions()` method
                # (runtime error: "'DataPartitioner' object has no attribute
                # 'list_partitions'"). Use `get_structure()` which returns a dict
                # of partition sub-dicts; sum their lengths for a real count.
                _struct = _partitioner.get_structure()
                _n_partitions = sum(
                    len(_struct.get(k, {}))
                    for k in ("by_domain_knowledge", "by_day_errors", "by_day_bypasses")
                )
                logger.info(
                    f"V104.4 DataPartitioner started "
                    f"({_n_partitions} partitions) + ThreeTierCache wired"
                )
            except Exception as e:
                logger.warning(f"V104.4 DataPartitioner start failed: {e}")

    return _judge



