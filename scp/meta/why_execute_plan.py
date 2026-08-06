"""
[Task 8-A] WHY engine execute_plan logic — extracted from why_engine.py

TẠI SAO: WhyEngine.execute_plan was 124 LOC inline — core verification logic.
Extracted as standalone function taking `engine` as first arg (for access to
_query_source method). Backward-compatible — WhyEngine.execute_plan delegates.

Logic:
  1. Skip deterministic evidence types (verified by direct computation)
  2. Query each source in plan.sources_to_query
  3. Compare ai_answer vs source values:
     - 0 sources → UNKNOWN
     - 1 source → PASS/FAIL based on substring match
     - N sources → PASS/FAIL/CONFLICT based on agreement
  4. UPDATE why_verification_plans SET status='executed'
  5. Record to MetaWhyMonitor (passive, no env var)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from scp.core.db_manager import db_exec

logger = logging.getLogger("scp.why.execute_plan")


def _normalize_for_comparison(text: str) -> str:
    """[ROOT-FIX 45-A] Normalize text for WHY Engine comparison.

    SLM returns "thủ đô France = Paris" but DataSource returns "Paris".
    This extracts the key value from formatted SLM output.
    """
    text = str(text).lower().strip()
    if "=" in text:
        text = text.split("=")[-1].strip()
    if ":" in text and len(text.split(":")[-1].strip()) > 2:
        text = text.split(":")[-1].strip()
    for prefix in ["thủ đô", "capital of", "capital", "the capital is", "answer is"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.rstrip(".!?,;").strip()
    return text


def execute_plan(engine, plan, ai_answer: str) -> dict[str, Any]:
    """Execute verification plan — query sources, compare values, return verdict.

    [WHY-FIX] TÁI SAO: trước đây method này chỉ return metadata, không execute gì.
    5.356 plans stuck ở 'pending' mãi mãi. Giờ:
    1. Query sources_to_query (Wikipedia, PubChem, etc.)
    2. Compare ai_answer vs source values
    3. Return verdict (PASS/FAIL/CONFLICT)
    4. UPDATE why_verification_plans SET status='executed'

    Returns:
        {verdict, confidence, sources_queried, all_values, reasoning}
    """
    result = {
        "plan_id": None,
        "target": plan.target,
        "evidence_type": plan.evidence_type,
        "strategy": plan.verification_strategy,
        "sources_to_query": plan.sources_to_query,
        "proof_criteria": plan.proof_criteria,
        "falsification_criteria": plan.falsification_criteria,
        "confidence_threshold": plan.confidence_threshold,
        "verdict": "UNKNOWN",
        "confidence": 0.0,
        "sources_queried": [],
        "all_values": [],
        "reasoning": "",
    }

    # Skip deterministic — they're verified by MathSLM directly
    if plan.evidence_type in ("deterministic_calculation", "deterministic_evaluation",
                               "codata_constants", "biological_database"):
        result["verdict"] = "PASS"
        result["confidence"] = 0.95
        result["reasoning"] = "Deterministic — verified by direct computation"
        return result

    # Query each source in sources_to_query
    source_values = []
    for source_name in plan.sources_to_query:
        try:
            value = engine._query_source(source_name, plan.target, plan.question)
            if value is not None:
                source_values.append({"source": source_name, "value": value})
                result["sources_queried"].append(source_name)
        except Exception as e:
            logger.debug(f"WHY execute: source {source_name} failed: {e}")

    result["all_values"] = source_values

    # Compare ai_answer vs source values
    if not source_values:
        # No sources available — can't verify
        result["verdict"] = "UNKNOWN"
        result["confidence"] = 0.0
        result["reasoning"] = f"No sources queried successfully for target '{plan.target}'"
    elif len(source_values) == 1:
        # Single source — check if ai_answer matches
        source_val = str(source_values[0]["value"]).lower().strip()
        ai_val = str(ai_answer).lower().strip()
        # [ROOT-FIX 45-A] Also try normalized comparison (SLM format ≠ DataSource format)
        norm_source = _normalize_for_comparison(source_values[0]["value"])
        norm_ai = _normalize_for_comparison(ai_answer)
        if source_val and (source_val in ai_val or ai_val in source_val
                           or norm_source in norm_ai or norm_ai in norm_source):
            # [ROOT-FIX 43-A / Fix 3] Accept single source as PASS with high confidence
            # WHY: GeographySLM has a trusted local DB (50+ countries). When 1 source
            # matches ai_answer, that IS verification — 1 trusted source is enough.
            # Was: confidence = plan.confidence_threshold (default 0.5) → judge saw
            # why_result.confidence=0.5 < 0.65 threshold → verdict became PARTIAL
            # even though SLM returned Paris with conf=0.7 and WHY confirmed match.
            # Now: confidence = max(plan.confidence_threshold, 0.85) — high enough
            # that judge's max(slm_conf, why_conf) yields PASS not PARTIAL.
            # DNA SCP #2: PASS = ĐÚNG — single trusted source matching IS PASS,
            # not PARTIAL (PARTIAL = "don't know for sure").
            # DNA SCP #7: AutoFix safe — only upgrades the 1-source-matches case
            # (which already returns PASS); the 0-source and disagreeing paths
            # are untouched.
            result["verdict"] = "PASS"
            _one_source_conf = max(plan.confidence_threshold, 0.85)
            result["confidence"] = _one_source_conf
            result["reasoning"] = (
                f"AI answer matches source '{source_values[0]['source']}' "
                f"(single source — high confidence accepted, conf={_one_source_conf:.2f})"
            )
        else:
            result["verdict"] = "FAIL"
            result["confidence"] = 0.3
            result["reasoning"] = f"AI answer '{ai_val[:50]}' != source '{source_val[:50]}'"
    else:
        # Multiple sources — check agreement
        values = [str(v["value"]).lower().strip() for v in source_values]
        unique = set(values)
        if len(unique) == 1:
            # All sources agree
            ai_val = str(ai_answer).lower().strip()
            if list(unique)[0] in ai_val or ai_val in list(unique)[0]:
                result["verdict"] = "PASS"
                result["confidence"] = min(0.95, plan.confidence_threshold + 0.1)
                result["reasoning"] = f"AI matches {len(source_values)} agreeing sources"
            else:
                result["verdict"] = "FAIL"
                result["confidence"] = 0.3
                result["reasoning"] = f"AI != {len(source_values)} agreeing sources"
        else:
            # Sources disagree → CONFLICT
            result["verdict"] = "CONFLICT"
            result["confidence"] = 0.3
            result["reasoning"] = f"Sources disagree: {len(unique)} different values"

    # UPDATE plan status in DB
    try:
        ts = datetime.now().astimezone().isoformat()
        db_exec(
            "UPDATE why_verification_plans SET status='executed', verdict=?, executed_at=? "
            "WHERE question=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (result["verdict"], ts, plan.question)
        )
    except Exception as e:
        logger.debug(f"WHY execute: update status failed: {e}")

    # [V5.3-WIRE] MetaWhyMonitor — passive pattern monitoring (no env var).
    # TẠI SAO: record mỗi WHY plan executed để MetaWhyMonitor detect:
    #   - Pattern lặp (cùng category >30% → bias)
    #   - Loop detection (cùng câu hỏi 5+ lần/giờ → stuck)
    #   - Emergent themes (missing piece SCP chưa tự giải được)
    # PASSIVE: chỉ append to data/metawhy_patterns.jsonl — không block, không
    # thay đổi result. Wrap try/except để monitor failure không break execute_plan.
    from scp.meta.why_engine import _get_metawhy_monitor
    monitor = _get_metawhy_monitor()
    if monitor is not None:
        try:
            why_plan_dict_for_monitor = {
                "target": plan.target,
                "verification_strategy": plan.verification_strategy,
                "evidence_type": plan.evidence_type,
                "verdict": result.get("verdict"),
                "confidence": result.get("confidence"),
            }
            monitor.record_why(plan.question, why_plan_dict_for_monitor)
        except Exception as e:
            logger.debug(f"[V5.3-WIRE] metawhy record_why failed: {e}")

    return result
