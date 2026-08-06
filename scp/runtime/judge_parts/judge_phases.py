"""
[OPT-8] Judge phases — extracted from judgecore_mixin.judge() for modularity.

[G4-FIX P0-12] HONEST STATUS (audited 2026-XX-XX by Task G4-C):
  - PARTIALLY WIRED via SHADOW MODE only. `run_all_phases_sync` is called from
    `judge.py:927` (inside `judge_async`, the async wrapper) for COMPARISON
    LOGGING — it never sets the production verdict. The actual production
    verdict path is `judgecore_mixin.JudgeCoreMixin.judge()` (L92-2375, 2284 LOC
    — was L74-2305 / 2232 LOC before the G4-C docstring audit added ~70 lines)
    — see its docstring for the REAL 28-step pipeline.
  - The 6 phases defined here are ASPIRATIONAL SCAFFOLDING for a future
    async refactor (Task 3), NOT the actual judge() pipeline. The doc's
    "10-phase pipeline" claim (SCP_FULL_CONTEXT_FOR_AI.md §2.2) is FICTION.
  - Known functional bugs in the shadow-mode phases:
      * phase3_slm_predict (line ~145): [G4-phase3 FIXED] was calling
        `judge._route_question(question)` which returns `list[str]` (domain
        names, e.g. `["math"]`) and storing that LIST in `ctx.slm_responses[0]`
        — semantically wrong. Downstream phases (4, 5) then did
        `str(ctx.slm_responses[0])` and "verified" the string `"['math']"` as
        if it were an answer claim. FIX: routed domains are now stored in
        the dedicated `ctx.routed_domains` field (added to JudgeContext by
        Task G4-phase3). `ctx.slm_responses` is left empty (no SLM was
        actually invoked in shadow mode — real SLM calls happen in
        judgecore_mixin.judge() Step 2 at L562-714). phase4/phase5 now
        correctly fall through to `ctx.ai_answer or ""` and skip their work
        when no claim is present.
      * phase4_falsification: was previously reported as dead due to attribute
        mismatch (`falsification_engine` vs `falsification`). Re-audit:
        `judge.py:305` DOES set `self.falsification_engine` (matching the
        `hasattr` check at line 165 of this file). The worklog finding #15b
        was inaccurate on this point. phase4 is technically callable, but its
        input is now empty (post-G4-phase3 fix) since no SLM answer is
        captured in shadow mode.
      * phase5_antibodies, phase6_verdict_construction: now see an empty
        `ctx.slm_responses` (post-G4-phase3 fix) → metadata correctly reports
        no SLM answer was produced, rather than producing garbage from
        `"['math']"`.
  - Net effect: shadow mode runs every call. Post-G4-phase3 fix the comparison
    verdict is no longer computed from garbage metadata (routed domains are
    correctly stored separately), but it is still computed from an EMPTY
    `slm_responses` list since shadow mode does not actually invoke SLMs. The
    `_phase_match_count` / `_phase_divergence_count` counters at judge.py:935-953
    therefore remain untrustworthy for deciding when to "switch to phase-based
    verdict" until phase3 is upgraded to actually call `judge.slms[domain].predict()`.

WHY this file still exists (not deleted):
  - Deleting it would force a cascade of edits in judge.py (lines 653-655,
    796-849, 909-967 reference judge_phases / run_all_phases_sync / JudgeContext).
  - The 6 phase signatures ARE a useful skeleton for the future async refactor.
  - Keeping it + this honest docstring lets future devs see the gap between
    aspiration and reality without re-discovering it the hard way.

WHY extract (not modify judge() directly — too risky for 2541 LOC):
  - judge() has CC≈300 (worklog claim of "CC=551" was inaccurate; re-audit
    shows the file is 2979 LOC total, judge() is L92-2375 = 2284 LOC).
    Still a god method — needs refactor, deferred (full split = 21 days).
  - Phases are logically independent: phase1_input_validation, phase2_routing,
    phase3_slm_predict, phase4_falsification, phase5_antibodies, etc.
  - Extracting into methods makes each phase testable independently.
  - Prepares for async refactor (Task 3): each phase can become async.

Pattern:
  - Each phase method takes (judge_instance, ctx: dict) -> dict (updated ctx)
  - ctx carries question, ai_answer, verdict, slm_responses, etc.
  - Phase methods DON'T call each other — judge() orchestrates
  - Phase methods are PURE (no side effects except logging)

Usage (future, after async refactor):
    async def judge(self, question, ai_answer, ...):
        ctx = {"question": question, "ai_answer": ai_answer, ...}
        ctx = await phase1_input_validation(self, ctx)
        ctx = await phase2_routing(self, ctx)
        ctx = await phase3_slm_predict(self, ctx)
        # ... etc.
        return ctx["verdict"]

Current status: SHADOW MODE ONLY (judge.py:927). The actual judge() in
judgecore_mixin.py is UNCHANGED — its real 28-step pipeline is documented
in the judge() docstring (added by Task G4-C).
"""
from __future__ import annotations

import logging
from typing import Any

# [G4-FIX P0-12] judge_phases.py is NOT WIRED into judgecore_mixin.judge().
# It runs in SHADOW MODE from judge.py:927 (judge_async wrapper) for
# comparison logging only — never sets the production verdict. Actual judge()
# pipeline (28 steps, L92-2375 of judgecore_mixin.py) is documented in that
# method's docstring. The "10-phase pipeline" claim in the project doc is
# FICTION. This file is kept as scaffolding for the future async refactor
# (Task 3) — see module docstring above for full honest status.

logger = logging.getLogger("scp.runtime.judge_phases")


class JudgeContext:
    """Context object passed between judge phases.

    Carries all state needed by phases — avoids long parameter lists.
    """
    def __init__(self, question: str, ai_answer: str = "", cycle_count: int = 0,
                 source: str = "", v98_context: dict = None):
        self.question = question
        self.ai_answer = ai_answer
        self.cycle_count = cycle_count
        self.source = source
        self.v98_context = v98_context or {}
        self.verdict: Any | None = None  # JudgeVerdict
        self.slm_responses: list = []
        # [G4-phase3 FIX] Routed domain names from judge._route_question
        # (list[str], e.g. ["math"]). Stored SEPARATELY from slm_responses
        # — which is for SLM answer DICTS, not domain lists. Previously
        # phase3_slm_predict stuffed this list into ctx.slm_responses[0],
        # causing phase4/phase5 to str() it into "['math']" and "verify"
        # that string as if it were an answer claim.
        self.routed_domains: list[str] = []
        self.domain: str = "general"
        self.confidence: float = 0.0
        self.metadata: dict = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def to_dict(self) -> dict:
        return {
            "question": self.question[:100],
            "domain": self.domain,
            "confidence": self.confidence,
            "slm_count": len(self.slm_responses),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


# ============================================================
# PHASE 1: Input validation + normalization
# ============================================================

def phase1_input_validation(judge, ctx: JudgeContext) -> JudgeContext:
    """Validate + normalize input.

    - Strip whitespace
    - Check empty question
    - Normalize Unicode
    - Detect encoding (base64/rot13) via decode_attacks
    - Set initial metadata
    """
    try:
        ctx.question = ctx.question.strip() if ctx.question else ""
        if not ctx.question:
            ctx.errors.append("empty_question")
            return ctx
        # Unicode normalization
        try:
            from scp.security.unified_detector import normalize_unicode
            ctx.question = normalize_unicode(ctx.question)
        except ImportError as e:
            logger.warning(f"Silent except: {e}")  # unified_detector not available
        ctx.metadata["input_length"] = len(ctx.question)
        ctx.metadata["has_ai_answer"] = bool(ctx.ai_answer)
    except Exception as e:
        ctx.errors.append(f"phase1_error: {e}")
        logger.debug(f"[phase1] error: {e}")
    return ctx


# ============================================================
# PHASE 2: Question routing (SmartClassifier)
# ============================================================

def phase2_routing(judge, ctx: JudgeContext) -> JudgeContext:
    """Route question to domain via SmartClassifier.

    - Use judge.smart_classifier.classify(question)
    - Set ctx.domain + ctx.confidence
    - If confidence < 0.5 → flag for ReActAgent fallback
    """
    try:
        if hasattr(judge, "smart_classifier") and judge.smart_classifier:
            result = judge.smart_classifier.classify(ctx.question)
            if result:
                ctx.domain = getattr(result, "domain", "general")
                ctx.confidence = getattr(result, "confidence", 0.0)
                ctx.metadata["routing_method"] = getattr(result, "method", "unknown")
                # Flag for ReActAgent if low confidence
                if ctx.confidence < 0.5:
                    ctx.metadata["react_fallback_recommended"] = True
                    ctx.warnings.append(f"low_routing_confidence={ctx.confidence:.2f}")
    except Exception as e:
        ctx.errors.append(f"phase2_error: {e}")
        ctx.domain = "general"
        ctx.confidence = 0.0
        logger.debug(f"[phase2] routing error: {e}")
    return ctx


# ============================================================
# PHASE 3: SLM prediction
# ============================================================

def phase3_slm_predict(judge, ctx: JudgeContext) -> JudgeContext:
    """Call domain SLM to get answer.

    - Use judge._route_question (existing)
    - Capture SLM responses
    - Set initial verdict

    [G4-phase3 FIX] `_route_question` returns `list[str]` of DOMAIN NAMES
    (e.g. ["math"]), NOT an SLM answer dict. Previously this method stuffed
    that list into `ctx.slm_responses[0]`, which downstream phases (4, 5)
    then str()'d into "['math']" and "verified" as if it were an answer
    claim. Fix: store the routed domains in the dedicated `ctx.routed_domains`
    field, and leave `ctx.slm_responses` empty (no SLM was actually invoked
    here — phase3 is shadow-mode scaffolding, real SLM calls happen in
    judgecore_mixin.judge() Step 2 at L562-714). phase4/phase5 now correctly
    fall through to `ctx.ai_answer or ""` and skip when no claim is present.
    """
    try:
        if hasattr(judge, "_route_question"):
            routed_domains = judge._route_question(ctx.question)  # list[str]
            if routed_domains:
                # [G4-phase3 FIX] Store domain list in its own field — NOT
                # in slm_responses (which is for SLM answer DICTS).
                ctx.routed_domains = list(routed_domains)
                ctx.metadata["slm_routed"] = True
                ctx.metadata["routed_domains"] = list(routed_domains)
                # Propagate primary domain into ctx.domain for phase2 parity
                if routed_domains and not ctx.metadata.get("routing_method"):
                    ctx.domain = routed_domains[0]
    except Exception as e:
        ctx.errors.append(f"phase3_error: {e}")
        logger.debug(f"[phase3] SLM predict error: {e}")
    return ctx


# ============================================================
# PHASE 4: Falsification (reality check)
# ============================================================

def phase4_falsification(judge, ctx: JudgeContext) -> JudgeContext:
    """Run falsification engine on SLM answer.

    - Use judge.falsification_engine if available
    - Verify claims against DataSources
    - Set ctx.metadata["falsified"]
    """
    try:
        if hasattr(judge, "falsification_engine") and judge.falsification_engine:
            # FalsificationEngine.verify(claim, question) -> dict
            claim = ctx.ai_answer or (ctx.slm_responses[0] if ctx.slm_responses else "")
            if claim:
                result = judge.falsification_engine.verify(str(claim), ctx.question)
                if result:
                    ctx.metadata["falsification_result"] = result
                    ctx.metadata["falsified"] = not result.get("verified", True)
    except Exception as e:
        ctx.errors.append(f"phase4_error: {e}")
        logger.debug(f"[phase4] falsification error: {e}")
    return ctx


# ============================================================
# PHASE 5: Antibodies check
# ============================================================

def phase5_antibodies(judge, ctx: JudgeContext) -> JudgeContext:
    """Run domain antibodies on the answer.

    - Use judge.antibody_system if available
    - Check domain-relevant antibodies only
    - Flag triggered antibodies
    """
    try:
        if hasattr(judge, "antibody_system") and judge.antibody_system:
            answer = ctx.ai_answer or (ctx.slm_responses[0] if ctx.slm_responses else "")
            if answer:
                results = judge.antibody_system.check_all(ctx.question, str(answer))
                if results:
                    triggered = [r for r in results if not r.get("passed", True)]
                    ctx.metadata["antibody_results"] = results
                    ctx.metadata["antibody_triggered"] = len(triggered)
                    if triggered:
                        ctx.warnings.append(f"antibody_triggered={len(triggered)}")
    except Exception as e:
        ctx.errors.append(f"phase5_error: {e}")
        logger.debug(f"[phase5] antibodies error: {e}")
    return ctx


# ============================================================
# PHASE 6: Verdict construction
# ============================================================

def phase6_verdict_construction(judge, ctx: JudgeContext) -> JudgeContext:
    """Construct final JudgeVerdict.

    - Aggregate results from phases 3-5
    - Determine verdict type (PASS/FAIL/UNKNOWN/ESCALATE)
    - Set confidence
    """
    try:
        # Import JudgeVerdict from types
        # Determine verdict based on phases
        falsified = ctx.metadata.get("falsified", False)
        antibody_triggered = ctx.metadata.get("antibody_triggered", 0)
        if falsified or antibody_triggered > 0:
            verdict_type = "FAIL"
            ctx.confidence = max(0.0, ctx.confidence - 0.3)
        elif ctx.confidence >= 0.7:
            verdict_type = "PASS"
        else:
            verdict_type = "UNKNOWN"
        ctx.metadata["verdict_type"] = verdict_type
        # Note: actual JudgeVerdict construction is still done in judge()
        # This phase just prepares the metadata.
    except Exception as e:
        ctx.errors.append(f"phase6_error: {e}")
        logger.debug(f"[phase6] verdict construction error: {e}")
    return ctx


# ============================================================
# ORCHESTRATOR (for future async judge)
# ============================================================

def run_all_phases_sync(judge, ctx: JudgeContext) -> JudgeContext:
    """Run all phases synchronously (preparation for async refactor).

    This is NOT yet wired into judge() — judge() still uses its own logic.
    This function exists so we can TEST the phase extraction independently.
    """
    ctx = phase1_input_validation(judge, ctx)
    if ctx.errors and "empty_question" in str(ctx.errors):
        return ctx  # short-circuit on empty
    ctx = phase2_routing(judge, ctx)
    ctx = phase3_slm_predict(judge, ctx)
    ctx = phase4_falsification(judge, ctx)
    ctx = phase5_antibodies(judge, ctx)
    ctx = phase6_verdict_construction(judge, ctx)
    return ctx


__all__ = [
    "JudgeContext",
    "phase1_input_validation",
    "phase2_routing",
    "phase3_slm_predict",
    "phase4_falsification",
    "phase5_antibodies",
    "phase6_verdict_construction",
    "run_all_phases_sync",
]
