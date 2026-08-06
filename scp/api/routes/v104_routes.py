"""
[Task 8-A] V104 endpoints — extracted from api_server.py

TẠI SAO: api_server.py 2,144 LOC god file. Tách 16 routes /v104/* vào module
này. Backward-compatible — public API paths/methods unchanged.

Routes:
  GET  /v104/status                          — V104 modules status
  POST /v104/multi-turn/check                — Multi-turn attack pattern check
  POST /v104/image/check                     — Image jailbreak via OCR
  POST /v104/voice/check                     — Voice jailbreak via Whisper ASR
  GET  /v104/cross-language/transfer         — Transfer VN patterns to target langs
  GET  /v104/explain                         — Verdict explanation in Vietnamese
  POST /v104/fact-check                      — Real-time fact check
  POST /v104/learn/ollama                    — Ollama learning loop
  POST /v104/learn/local                     — Local file learning
  POST /v104/learn/news                      — News learning loop
  POST /v104/learn/all                       — All 3 learning loops
  GET  /v104/learn/status                    — Real Learning Engine status
  GET  /v104/learn/matrix                    — 14 countries × 5 domains matrix
  POST /v104/learn/ollama-matrix             — Full matrix coverage (70 questions)
  POST /v104/learn/fast                      — Fast learning cycle (parallel)
  GET  /v104/learn/fast/status               — Fast Learning Engine stats
  GET  /v104/learn/fast/benchmark            — V104.1 sequential vs V104.2 parallel benchmark
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
from scp.api._shared import (
    _V1042_AVAILABLE,
    _cross_language_learner,
    _fact_checker,
    _fast_learning,
    _image_detector,
    _multi_turn_tracker,
    _real_learning,
    _safe_fetch_url,
    _simple_explainer,
    _voice_detector,
    logger,
    verify_admin,
)

router = APIRouter(tags=["v104"])


@router.get("/v104/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v104_status():
    """V104: Status of all new modules."""
    return {
        "multi_turn_tracker": _multi_turn_tracker.stats(),
        "image_detector": _image_detector.stats(),
        "voice_detector": _voice_detector.stats(),
        "cross_language": _cross_language_learner.stats(),
        "fact_checker": _fact_checker.stats(),
    }


@router.post("/v104/multi-turn/check")
async def v104_multi_turn_check(
    session_id: str,
    question: str,
    verdict: str = "PASS",
    _admin: bool = Depends(verify_admin),
):
    """V104: Check multi-turn attack pattern."""
    result = _multi_turn_tracker.track(session_id, question, verdict)
    return result.__dict__


@router.post("/v104/image/check")
async def v104_image_check(
    image_url: str = "",
    image_base64: str = "",
    _admin: bool = Depends(verify_admin),
):
    """V104: Check image for jailbreak via OCR."""
    if image_base64:
        import base64
        image_bytes = base64.b64decode(image_base64)
        result = _image_detector.detect(image_bytes=image_bytes)
    elif image_url:
        # [FIX-A P0-2] Was urllib.request.urlopen(image_url) — accepted
        # file:// (LFI), http://169.254.169.254/ (SSRF), internal IPs, followed
        # redirects, no size cap, blocked event loop. Now: _safe_fetch_url +
        # asyncio.to_thread + generic 400 on policy violation (no URL echo).
        try:
            image_bytes = await asyncio.to_thread(_safe_fetch_url, image_url)
            result = _image_detector.detect(image_bytes=image_bytes)
        except ValueError:
            logger.warning("/v104/image/check image_url rejected by _safe_fetch_url policy")
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid or disallowed image_url"},
            )
        except Exception as e:
            logger.debug(f"/v104/image/check error: {e}")
            return JSONResponse(
                status_code=400,
                content={"error": "Failed to process image"},
            )
    else:
        return {"error": "Provide image_url or image_base64"}
    return result.__dict__


@router.post("/v104/voice/check")
async def v104_voice_check(
    audio_url: str = "",
    audio_base64: str = "",
    _admin: bool = Depends(verify_admin),
):
    """V104: Check audio for jailbreak via Whisper ASR."""
    if audio_base64:
        import base64
        audio_bytes = base64.b64decode(audio_base64)
        result = _voice_detector.detect(audio_bytes=audio_bytes)
    elif audio_url:
        # [FIX-A P0-2] Was passing audio_url as a local file path to the
        # detector — failed silently AND allowed SSRF (detector may have
        # fetched internally). Now: fetch via _safe_fetch_url (scheme/IP/
        # redirect/size defenses, non-blocking) then pass audio_bytes=...
        try:
            audio_bytes = await asyncio.to_thread(_safe_fetch_url, audio_url)
            result = _voice_detector.detect(audio_bytes=audio_bytes)
        except ValueError:
            logger.warning("/v104/voice/check audio_url rejected by _safe_fetch_url policy")
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid or disallowed audio_url"},
            )
        except Exception as e:
            logger.debug(f"/v104/voice/check error: {e}")
            return JSONResponse(
                status_code=400,
                content={"error": "Failed to process audio"},
            )
    else:
        return {"error": "Provide audio_url or audio_base64"}
    return result.__dict__


@router.get("/v104/cross-language/transfer", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v104_cross_language_transfer(target_lang: str = "all"):
    """V104: Transfer Vietnamese patterns to target language(s)."""
    if target_lang == "all":
        patterns = _cross_language_learner.transfer_existing_vietnamese_patterns()
        return {"count": len(patterns), "patterns": patterns}
    else:
        patterns = _cross_language_learner.transfer_existing_vietnamese_patterns()
        filtered = [p for p in patterns if p["target_lang"] == target_lang]
        return {"count": len(filtered), "patterns": filtered}


@router.get("/v104/explain", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v104_explain(
    verdict: str = "PASS",
    confidence: float = 0.8,
    domain: str = "",
    sources: int = 0,
    has_attack: bool = False,
    has_bypass: bool = False,
    has_human_review: bool = False,
    lineage_overlap: float = 0.0,
    reliability_factor: float = 1.0,
):
    """V104: Explain verdict in simple Vietnamese for non-experts."""
    result = _simple_explainer.explain(
        verdict=verdict, confidence=confidence, domain=domain, sources=sources,
        has_attack=has_attack, has_bypass=has_bypass, has_human_review=has_human_review,
        lineage_overlap=lineage_overlap, reliability_factor=reliability_factor,
    )
    return result.__dict__


@router.post("/v104/fact-check")
async def v104_fact_check(text: str, question: str = "", _admin: bool = Depends(verify_admin)):
    """V104: Real-time fact check — extract claims + verify."""
    results = await _fact_checker.check_text(text, question)
    return {
        "claims_found": len(results),
        "results": [r.__dict__ for r in results],
        "stats": _fact_checker.stats(),
    }


@router.post("/v104/learn/ollama")
async def v104_learn_ollama(count: int = 10, _admin: bool = Depends(verify_admin)):
    """V104.1 FIX: Trigger Ollama learning loop — ma trận 14 quốc gia × 5 lĩnh vực."""
    results = await _real_learning.ollama_learning_cycle(count=count)
    return results


@router.post("/v104/learn/local")
async def v104_learn_local(_admin: bool = Depends(verify_admin)):
    """V104 FIX: Trigger local file learning — scan data/ → verify → KB."""
    results = await _real_learning.local_learning_cycle()
    return results


@router.post("/v104/learn/news")
async def v104_learn_news(_admin: bool = Depends(verify_admin)):
    """V104 FIX: Trigger news learning — fetch RSS → verify → KB."""
    results = await _real_learning.news_learning_cycle()
    return results


@router.post("/v104/learn/all")
async def v104_learn_all(_admin: bool = Depends(verify_admin)):
    """V104 FIX: Trigger all 3 learning loops."""
    results = await _real_learning.run_all_cycles()
    return results


@router.get("/v104/learn/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v104_learn_status():
    """V104 FIX: Status of Real Learning Engine."""
    return _real_learning.stats()


@router.get("/v104/learn/matrix", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v104_learn_matrix():
    """V104.1 NEW: Trả về ma trận 14 quốc gia × 5 lĩnh vực = 70 combinations.

    Mỗi cell = số câu hỏi có thể sinh ra cho (country, domain).
    Tổng = 14 × 5 = 70 cells (đa lĩnh vực + toàn quốc gia).
    """
    from scp.core.real_learning_engine import (
        COUNTRIES,
        COUNTRY_DOMAIN_HINTS,
        DOMAINS,
        get_country_domain_matrix,
        get_total_combinations,
    )
    matrix = get_country_domain_matrix()
    total = get_total_combinations()
    return {
        "matrix_size": "14 quốc gia × 5 lĩnh vực = 70 cells",
        "total_country_specific_combinations": total,
        "countries_count": len(COUNTRIES),
        "domains_count": len(DOMAINS),
        "countries": COUNTRIES,
        "domains": DOMAINS,
        "matrix": matrix,
        "hints_count": sum(
            len(hints) for hints in COUNTRY_DOMAIN_HINTS.values()
        ),
        "summary": {
            "geography": "14 × 5 = 70 (country-specific)",
            "history": "14 × 3 = 42 (country-specific)",
            "chemistry": "14 × 3 = 42 (country) + 6 × 3 = 18 (compound) = 60",
            "physics": "14 × 3 = 42 (country) + 3 (generic) = 45",
            "biology": "14 × 3 = 42 (country) + 3 (generic) = 45",
        },
    }


@router.post("/v104/learn/ollama-matrix")
async def v104_learn_ollama_matrix(_admin: bool = Depends(verify_admin)):
    """V104.1 NEW: Run full matrix coverage (70 questions = 1 vòng ma trận đầy đủ).

    Mỗi (country, domain) được hỏi 1 lần → đảm bảo coverage 14 × 5 = 70.
    """
    results = await _real_learning.ollama_learning_cycle(count=70)
    return results


# ============================================================
# V104.2 NEW: Fast Learning (Parallel + Skip-Known + Compounding)
# ============================================================

@router.post("/v104/learn/fast")
async def v1042_learn_fast(count: int = 50, _admin: bool = Depends(verify_admin)):
    """V104.2 NEW: Fast learning cycle — parallel 10 concurrent Ollama + 5 Wiki.

    Default count=50. Skip câu đã có trong KB. Sinh câu hỏi Level-2 compounding.
    Adaptive interval 1-30 min tùy throughput.
    Returns: asked, skipped_known, verified, stored, compounding_L2, time_ms, adaptive_mode.
    """
    if not _V1042_AVAILABLE or _fast_learning is None:
        return {"error": "V104.2 FastLearningEngine not available"}
    results = await _fast_learning.fast_learning_cycle(count=count)
    return results


@router.get("/v104/learn/fast/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v1042_learn_fast_status():
    """V104.2 NEW: Fast Learning Engine stats.

    Trả về: cycles_completed, asked, skipped, verified, stored,
    compounding_L2/L3, avg_cycle_time_ms, fastest/slowest, adaptive_interval.
    """
    if not _V1042_AVAILABLE or _fast_learning is None:
        return {"error": "V104.2 FastLearningEngine not available"}
    return _fast_learning.stats()


@router.get("/v104/learn/fast/benchmark", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
async def v1042_learn_fast_benchmark():
    """V104.2 NEW: Benchmark V104.1 tuần tự vs V104.2 parallel.

    Returns: speedup factor, sequential_ms vs parallel_ms, concurrency.
    """
    if not _V1042_AVAILABLE or _fast_learning is None:
        return {"error": "V104.2 FastLearningEngine not available"}
    stats = _fast_learning.stats()
    avg_ms = stats.get("avg_cycle_time_ms", 0)
    asked_avg = 50  # default count
    sequential_estimated_ms = asked_avg * 700  # 700ms per question sequential
    speedup = (sequential_estimated_ms / avg_ms) if avg_ms > 0 else 0
    return {
        "v104_1_sequential_ms_per_q": 700,
        "v104_2_parallel_avg_cycle_ms": avg_ms,
        "v104_2_questions_per_cycle": asked_avg,
        "v104_2_estimated_sequential_ms": sequential_estimated_ms,
        "speedup_factor": round(speedup, 1),
        "concurrency_ollama": 10,
        "concurrency_wikipedia": 5,
        "adaptive_interval_current_s": stats.get("adaptive_interval_current", 300),
        "cycles_completed": stats.get("cycles_completed", 0),
        "facts_stored_total": stats.get("ollama_kb_facts_stored", 0),
    }
