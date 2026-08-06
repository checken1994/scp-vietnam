""" JudgeVerdict, _AllowedByWatchlist
SCP V90 — Reality Judge Module
Cross-check SLMs, verify with reality, produce final verdicts.
Extracted from engine.py for modularity.

[G4-FIX P0-12] REALITY-CHECK ON THE "10-PHASE PIPELINE" CLAIM:
The project doc (SCP_FULL_CONTEXT_FOR_AI.md §2.2) claims judge.judge() is a
"10-phase pipeline" (Phase 1 DoS → 2 Multimodal → 3 Chatbot → 4 SmartClassifier
→ 5 SLM → 6 KB → 7 Reality → 8 Consistency → 9 Governance → 10 Antibody →
11 API boundary). That claim is FICTION. The ACTUAL judge() method (this file,
L74-2305, 2232 LOC, CC≈300) is a single god method with ~27 distinct phases
whose inline numbering is contradictory (PHASE 1 / STEP 0 / Step 1 / Step 2 /
Step 5.5 / Step 6 / Step 7 / Step 9 / STEP 9-again / PHASE 6 / PHASE 6.5 /
PHASE 10 / PHASE 11 — see judge() docstring for the full table).

`scp/runtime/judge_parts/judge_phases.py` defines only 6 phases and is NOT
wired into THIS method. It runs in SHADOW MODE from judge.py:927 (the async
wrapper `judge_async`) for comparison logging only — never sets the production
verdict. See that file's docstring for its honest status.

Full split of judge() into 10 phase modules = 21-day refactor, DEFERRED
(worklog Task G4-C). This file documents reality instead of pretending.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from scp.core.db_manager import (
    db_exec,
)
from scp.runtime.judge_parts.types import JudgeVerdict, _AllowedByWatchlist

# [Task 19-C] TYPE_CHECKING import — RealityJudge is the parent class that
# mixes in JudgeCoreMixin. Referencing it directly (self._extract_value)
# creates a circular import at runtime. TYPE_CHECKING block makes the name
# available to static analyzers (ruff/mypy) without runtime cost.
if TYPE_CHECKING:
    pass

from scp.meta.scp_meta import SCPMeta as _SCPMeta  # [Gà §10] — wired into judge()

logger = logging.getLogger("scp.judge")


# [V5.3-WIRE] multi_llm_check integration — opt-in via SCP_MULTI_LLM_CHECK=1 env var.
# TẠI SAO: DNA SCP #20 "ảo giác đồng thuận" — 100 AI cùng kết luận chưa chắc 100
# nguồn độc lập. Cross-check giữa 2-3 LLM providers (OpenRouter + Groq) để phát
# hiện disagreement. Default OFF để không tăng latency khi không cần.
# Lazy singleton — chỉ instantiate khi env var ON.
_MULTI_LLM_CHECKER_SINGLETON = None
_MULTI_LLM_CHECKER_LOCK = __import__("threading").Lock()


def _get_multi_llm_checker():
    """Lazy singleton for MultiLLMChecker. Returns None if env var OFF."""
    global _MULTI_LLM_CHECKER_SINGLETON
    if os.environ.get("SCP_MULTI_LLM_CHECK", "0") != "1":
        return None
    if _MULTI_LLM_CHECKER_SINGLETON is None:
        with _MULTI_LLM_CHECKER_LOCK:
            if _MULTI_LLM_CHECKER_SINGLETON is None:
                try:
                    from scp.meta.multi_llm_check import MultiLLMChecker
                    _MULTI_LLM_CHECKER_SINGLETON = MultiLLMChecker()
                    logger.info("[V5.3-WIRE] MultiLLMChecker initialized — adversary answers will be cross-checked")
                except Exception as e:
                    logger.warning(f"[V5.3-WIRE] MultiLLMChecker init failed: {e} — multi-LLM check disabled")
                    _MULTI_LLM_CHECKER_SINGLETON = False
    return _MULTI_LLM_CHECKER_SINGLETON if _MULTI_LLM_CHECKER_SINGLETON is not False else None


# [V104.42 #AH] Internal signal — source passed watchlist check, proceed to INSERT

# ============================================================
# JUDGE VERDICT
# ============================================================
@dataclass

# ============================================================
# REALITY JUDGE — Cross-check SLMs + Verify với Reality
# ============================================================


class JudgeCoreMixin:
    """Mixin for RealityJudge — provides judge."""

    def judge(self, question: str, ai_answer: str = "", cycle_count: int = 0,
              source: str = "", v98_context: Optional[dict[str, Any]] = None) -> JudgeVerdict:
        """Process a question through the reality-checking pipeline.

        [G4-FIX P0-12] ACTUAL phases in this method (audited by Task G4-C).
        The doc's "10-phase pipeline" claim (SCP_FULL_CONTEXT_FOR_AI.md §2.2)
        is FICTION. judge_phases.py defines 6 phases but is NOT wired into
        THIS method — it runs only in SHADOW MODE from judge.py:927 (the
        async wrapper) for comparison logging. THIS method is the actual
        production verdict path. CC≈300 (god method — needs refactor,
        deferred; full split = 21 days).

        Line ranges below refer to THIS file (judgecore_mixin.py).

        | #  | Lines      | LOC  | What it actually does                                                              |
        |----|------------|------|------------------------------------------------------------------------------------|
        | 0a | L178-226   |  49  | Setup: timer, perf counter, _enable_closed_loop, pre-verdict evidence dict; run WHY engine (`_run_why_engine`); KnowledgeArbiter conflict check (`_check_knowledge_conflicts`) |
        | 1  | L227-263   |  37  | V100 PHASE 1 — KB retrieval via `domain_knowledge_store.search` (returns hits; short-circuit deferred to step 4) |
        | 2  | L264-379   | 116  | V98 STEP 0 — Input detection: MemoryPoisoningGuard, AttackPatternMemory, UnifiedPatternDetector (with CWE mapping via `_map_cwe_for_attack`), ThreatDetector + AttackClassifierEngine. Early FAIL return if critical threat / severe poisoning. |
        | 3  | L380-422   |  43  | V104.52 — Predictive Defense: AttackPredictor forecasts future attacks; if high confidence + critical → trigger escalation_manager |
        | 4  | L423-449   |  27  | KB short-circuit (post-security): if KB tier ≤2 + conf ≥0.85 AND passed V98 security → early return PASS. (Moved here by FIX-CRIT-27 BUG 3 — was previously above Step 0, bypassing all security.) |
        | 5  | L450-502   |  53  | V104.44 — ErrorStoreIndex.search_similar: if 2+ past FAILs match this question → boost threshold +0.1 (applied later at step 6) |
        | 6  | L503-561   |  59  | Step 1 — Route (`_route_question`); apply domain tolerance (ExperienceEngine) + PolicyApplier adjustment + VerdictPredictor skip_api decision |
        | 7  | L562-714   | 153  | Step 2 — Call SLMs (parallel via ThreadPoolExecutor, max 4 workers); V88 multi-SLM consensus; LLM (Ollama/OpenRouter) fallback if no SLM returned an answer |
        | 8  | L715-880   | 166  | Steps 3-5 — Filter garbage SLM answers; `_check_consistency`; ConflictResolver (resolve_value); consensus-aware primary SLM selection; calibration; confidence_adjustments |
        | 9  | L881-928   |  48  | ROOT-FIX 43-A — Deterministic SLM short-circuit: MathSLM/ConversionSLM/StatisticsSLM/LogicSLM with conf≥0.95 → return PASS without WHY/adversary/reality verification |
        | 10 | L929-989   |  61  | Step 5.5 — Adversary verify (Wikipedia cross-check); conflict → reduce confidence + flag `_adversary_conflict`; agreement → boost +0.05 |
        | 11 | L990-1349  | 360  | Step 6 — Verdict DECISION: PASS / FAIL / UNKNOWN / PARTIAL / CONFLICT / SPECULATIVE via numeric tolerance / scientific notation / full-string / fuzzy / word-overlap comparison. Includes V105 Phase 9.5 Speculative Mode for creative/hypothetical questions. |
        | 12 | L1350-1465 | 116  | Step 7 — Reality check: V13 RealityEngine + DirectAPIVerifier fallback (FIX v26) + Tree of Thoughts upgrade UNKNOWN→PASS. Also applies `_adversary_conflict` flag (PASS→CONFLICT) and clamps confidence to [0,1]. |
        | 13 | L1466-1492 |  27  | Step 9 — Build JudgeVerdict dataclass (merges `_pre_verdict_evidence` from steps 2, 5, 3) |
        | 14 | L1493-1519 |  27  | V9.0-WHY-GATE — Primary control gate; can downgrade PASS→UNKNOWN if WHY rejects (constitution HARD LOCK: WHY cannot override KILL) |
        | 15 | L1520-1653 | 134  | Post-verdict feedback: PolicyApplier.record_outcome; Multi-LLM cross-check (`_run_multi_llm_check`); WHY confidence adjustment; Cognitive Engine (`_run_cognitive_engine`) + Cognitive Gate (`_apply_cognitive_gate`); save prediction (V35); Calibration log |
        | 16 | L1654-1684 |  31  | V45→V68 — Question Tracker (log every question as NEW / REPEAT / INTERNAL) |
        | 17 | L1685-1808 | 124  | V64→V83 — Save PASS results to knowledge table (`INSERT OR REPLACE`); skip curiosity re-asks and pure-number questions |
        | 18 | L1809-1834 |  26  | V65 — ExperienceEngine.learn from every verdict |
        | 19 | L1835-1855 |  21  | V97 — FalsificationEngine (`_run_falsification`) + ErrorStore.add for FAIL/CONFLICT/low-conf |
        | 20 | L1856-1921 |  66  | V97 — Governance.decide (UPHOLD/KILL/ESCALATE); KILL → clear final_answer (abstain) + notify; ESCALATE → UNKNOWN + flag for human review |
        | 21 | L1922-2091 | 170  | V98 STEP 9 — Counter Response: AttackPolicyEngine.decide → CounterResponseEngine.execute (poison_response, canary); AttackPatternMemory.record_bypass; bypass → downgrade PASS→FAIL + clear answer + notify |
        | 22 | L2092-2117 |  26  | V100 PHASE 6 — Claim extraction + DomainAntibodySystem (38 antibodies, `_run_antibodies`) + claim verification; refuted claim → confidence ×0.5 |
        | 23 | L2118-2138 |  21  | V107 PHASE 6.5 — Logical Auditor (deep logic check via ThreadPoolExecutor, 30s timeout) |
        | 24 | L2139-2159 |  21  | V106 — SelfQuestioningEngine ("Tại sao tôi ra verdict này?") for PASS/FAIL/SPECULATIVE |
        | 25 | L2160-2206 |  47  | V100 PHASE 10 — Learning: save PASS to KnowledgeStore; H8 RedTeamBridge.record_normal_question + record_bypass (false-positive baseline + bypass detection) |
        | 26 | L2207-2287 |  81  | V100 PHASE 11 — Output: phase timings into evidence; V104.45 healing monitor + strategies |
        | 27 | L2288-2375 |  88  | V5.8 — source_reputation.record_outcome (per contributing source) + SCPMeta council review (3 systems vote; SKIP/DEFER_HUMAN → UNKNOWN) + `return verdict` |

        Total: 28 distinct phases/steps across L178-2375 (2198 LOC of method body
        after this 84-line docstring). Phase numbering in inline comments is
        CONTRADICTORY: code says "PHASE 1" at L227 (KB), "STEP 0" at L264 (security,
        AFTER Phase 1), "Step 1" at L503 (routing), "Step 2" at L562 (SLMs),
        "Step 5.5" at L929 (adversary), "Step 6" at L990 (verdict), "Step 7" at
        L1350 (reality), "Step 9" at L1466 (verdict construction), "STEP 9" again
        at L1922 (counter response — same number as verdict construction!),
        "PHASE 6" at L2092 (antibodies — after Step 9 counter response),
        "PHASE 6.5" at L2118, "PHASE 10" at L2160, "PHASE 11" at L2207. Phases
        7-9 of the doc's "10-phase pipeline" do not map to any single contiguous
        code block — they are interleaved across the 28 actual steps.

        V29 / V98 / V100 flow (preserved from earlier docstring, kept for context):
            1. Route → domains
            2. [V29] PolicyApplier: adjust threshold + prefer sources based on principles
            3. SLM predict
            4. [V29] Adversary verify: cross-validate với source khác
            5. Reality check (DirectAPIVerifier)
            6. [V29] VerdictPredictor: skip API nếu prediction confidence cao
            7. [V29] PolicyApplier.record_outcome: feedback loop
            8. Build verdict

        V98 additions (preserved):
            0a. [V98] MemoryPoisoningGuard — check question for poisoning patterns
            0b. [V98] AttackPatternMemory — check against known attack rules
            0c. [V98] ThreatDetector + AttackClassifierEngine — if v98_context has IP/headers
            9a. [V98] AttackPolicyEngine — decide counter phase
            9b. [V98] CounterResponseEngine — execute counter
            9c. [V98] CanaryTokenMonitor — generate canary if Phase 2+
            9d. [V98] AttackPatternMemory.record_bypass — if bypass detected

        Args:
            question: Câu hỏi
            ai_answer: Câu trả lời của AI (nếu có, để verify)
            cycle_count: Số cycle hiện tại từ SCPV14
            source: [V68] Nguồn câu hỏi — "real_fetcher" / "curiosity" / "external" / "generator"
            v98_context: [V98] Optional context dict with IP, headers, session_id for security modules
                         {"ip": str, "headers": dict, "session_id": str, "body": str}

        Returns: JudgeVerdict
        """
        ts = datetime.now().isoformat()
        _judge_start_perf = time.perf_counter()  # [V93.9] measure total judge() duration

        # [V100] Per-phase timer — 11 timestamps
        from scp.runtime.timing_and_restart import PhaseTimer, QueryTimeoutError, check_timeout
        _v100_timer = PhaseTimer()

        # [V104.39 #A] Define _enable_closed_loop ONCE at top of judge() — used by
        # WHY engine (line ~1227), PolicyApplier (line ~1360), VerdictPredictor (line ~1378),
        # and feedback record_outcome (line ~2108). All must use the SAME env var.
        _enable_closed_loop = os.environ.get("SCP_ENABLE_CLOSED_LOOP", "1") == "1"

        # [P0-4 FIX] TẠI SAO: Multiple V104.40-V104.45 "fixes" (bugs K/CB/BX/BA) reference
        # `verdict`, `verdict_type`, `reasoning` BEFORE they are assigned:
        #   - Step 0b (UnifiedDetector, ~line 1365): `verdict.evidence[...]` before verdict created (~2251)
        #   - Step 5.5 (Adversary, ~line 1794): `if verdict_type == "PASS"` before Step 6 sets it
        #   - ErrorStoreIndex (~line 1436): `verdict.evidence[...]` before verdict created
        # Each raised NameError, swallowed by `except Exception as e: pass` → the fixes were
        # cosmetically present but functionally DEAD. Reality: "ảo giác đồng thuận" —
        # code looks like 10 fixes agree, but all share one dead lineage.
        # Fix: initialize all three to None/"" up here. Pre-verdict evidence writes go
        # into `_pre_verdict_evidence` dict and are merged into verdict.evidence AFTER
        # verdict creation. Adversary conflict is recorded as a flag and applied post-Step-6.
        verdict = None
        verdict_type = None
        reasoning = ""
        _pre_verdict_evidence: dict[str, Any] = {}
        _adversary_conflict = False
        _adversary_conflict_reason = ""
        _es_index_threshold_boost = 0.0  # ErrorStoreIndex similar-FAIL threshold raise

        # [V104.39 #F] TẠI SAO: V90 OPT skipped WHY engine → Evidence-First not operational.
        # Re-enabled via SCP_ENABLE_CLOSED_LOOP=1 (same guard as Policy).
        # [Task 45-B] WHY engine block extracted to _run_why_engine() to reduce judge() CC.
        why_plan, why_result = self._run_why_engine(question, ai_answer, _enable_closed_loop)

        # ============================================================
        # [Task 40-B / OPT-1] KnowledgeArbiter — resolve cross-source conflicts.
        # TẠI SAO: KnowledgeArbiter instantiated in judge.__init__ (line ~642)
        # but NEVER called on the judge() hot path → dead code (DNA #6 violated).
        # Wire here, AFTER why_result has collected multi-source values but
        # BEFORE verdict construction. ADDITIVE: does not modify why_engine
        # logic; only consults arbiter and logs conflicts. Arbiter facts are
        # added per-judge-call (short-lived) — same pattern as why_result.
        # [OPT-42] Extracted to _check_knowledge_conflicts() to reduce judge() CC.
        # ============================================================
        self._check_knowledge_conflicts(why_result, why_plan, question, _pre_verdict_evidence)

        # ============================================================
        # [V100] PHASE 1: KNOWLEDGE RETRIEVAL — check KB before SLM
        # ============================================================
        v100_kb_hits = []
        with _v100_timer.phase("knowledge"):
            if self.domain_knowledge_store:
                try:
                    v100_kb_hits = self.domain_knowledge_store.search(question, domain="", limit=3)
                    if v100_kb_hits:
                        logger.debug(f"[V100] KB hit: {len(v100_kb_hits)} records for '{question[:50]}'")
                        # [V104.43 #BI] TẠI SAO: was only logging KB hits, never short-circuit.
                        # Comment "check KB before SLM" was misleading — KB was metadata only.
                        # Fix: if highest-tier hit with high confidence, use it as answer directly.
                        _best_hit = v100_kb_hits[0]  # sorted by tier+conf+recency
                        _hit_tier = getattr(_best_hit, 'source_tier', 9)
                        _hit_conf = getattr(_best_hit, 'confidence', 0)
                        if _hit_tier <= 2 and _hit_conf >= 0.85:
                            # AXIOMATIC/AUTHORITATIVE tier + high conf → short-circuit
                            final_answer = getattr(_best_hit, 'answer', '')
                            confidence = _hit_conf
                            verdict_type = "PASS"
                            reasoning = f"KB short-circuit: tier={_hit_tier} conf={_hit_conf:.2f} (cached authoritative)"
                            logger.info(f"[V104.43 #BI] KB short-circuit: tier={_hit_tier} conf={_hit_conf:.2f}")
                except Exception as e:
                    logger.debug(f"[V100] KB search error: {e}")

        # [V100] Check 10s timeout after knowledge phase
        try:
            check_timeout(_v100_timer.finish(), "knowledge")
        except QueryTimeoutError as e:
            logger.warning(f"Silent except: {e}")  # Don't crash — just log and continue

        # [V104.46 #BI] KB short-circuit was previously here — moved BELOW
        # Step 0a-0c security checks by FIX-CRIT-27 BUG 3 (was bypassing ALL
        # V98 security: MemoryPoisoningGuard, AttackPatternMemory,
        # UnifiedDetector, ThreatDetector — an attacker who poisoned the KB
        # once got permanent PASS bypass).
        # ============================================================
        # [V98] STEP 0: INPUT DETECTION — chạy TRƯỚC khi route SLMs
        # ============================================================
        v98_threat_signal = None
        v98_classification = None
        v98_guard_verdict = None
        v98_attack_match = None
        v98_early_fail = False

        # 0a. MemoryPoisoningGuard — check question for poisoning
        if self.memory_guard:
            try:
                session_id = (v98_context or {}).get("session_id", f"cycle_{cycle_count}")
                v98_guard_verdict = self.memory_guard.check(session_id, question)
                if v98_guard_verdict.is_poisoned:
                    logger.warning(f"[V98] MemoryPoisoningGuard: risk={v98_guard_verdict.risk_score} patterns={v98_guard_verdict.detected_patterns}")
                    if v98_guard_verdict.recommendation == "clear":
                        # Severe poisoning → early FAIL
                        v98_early_fail = True
            except Exception as e:
                logger.debug(f"[V98] MemoryPoisoningGuard error: {e}")

        # 0b. AttackPatternMemory — check against known attack rules
        if self.attack_memory:
            try:
                v98_attack_match = self.attack_memory.check_against_rules(question)
                if v98_attack_match.get("matched"):
                    logger.info(f"[V98] AttackPatternMemory match: rule={v98_attack_match.get('rule_id')}")
                    # [V104.18 #1 FIX] Promoted rule match → early FAIL (was: log only)
                    if v98_attack_match.get("promoted", False):
                        v98_early_fail = True
            except Exception as e:
                logger.debug(f"[V98] AttackPatternMemory error: {e}")

        # [V104.43 #CB] [P0-4/P1-6 FIX] TẠI SAO: UnifiedPatternDetector was
        # initialized but never called on hot path → unified patterns dead.
        # V104.43 fix called detect() but had 3 bugs:
        #   (1) `_unified_result.get("matched")` — but UnifiedThreatAssessment is a
        #       @dataclass, not a dict → AttributeError → swallowed by except.
        #       Correct attr: `is_attack`. Other attrs: `matched_patterns`, `severity`.
        #   (2) `verdict.evidence[...]` — verdict not created yet (created ~line 2251).
        #       Fix: store in `_pre_verdict_evidence`, merge after verdict creation.
        #   (3) `any(p.get("severity") == "critical" for p in matched_patterns)` —
        #       matched_patterns is List[str] (rule names), not List[dict]. The
        #       severity lives on the assessment itself: `_unified_result.severity`.
        if self.unified_detector:
            try:
                _unified_result = self.unified_detector.detect(question, ai_answer or "")
                if _unified_result and getattr(_unified_result, "is_attack", False):
                    logger.info(f"[V104.43 #CB] UnifiedDetector match: severity={_unified_result.severity} patterns={_unified_result.matched_patterns}")
                    _pre_verdict_evidence["v102_unified_detection"] = _unified_result.to_dict() if hasattr(_unified_result, "to_dict") else {"is_attack": True, "severity": _unified_result.severity, "matched_patterns": _unified_result.matched_patterns}
                    # If CRITICAL unified pattern → early FAIL
                    if getattr(_unified_result, "severity", "none") == "critical":
                        v98_early_fail = True

                    # [Task 40-B / OPT-13] Wire CWEExploitStore — look up CWE for detected patterns.
                    # TẠI SAO: CWEExploitStore created (knowledge/cwe_exploit_store.py) but
                    # 0 callers → dead code (DNA #6 violated). When UnifiedDetector flags an
                    # attack, map the matched pattern to a CWE so the verdict carries concrete
                    # mitigation guidance + antibody hints. ADDITIVE: ImportError/Exception is
                    # non-fatal; original UnifiedDetector flow untouched.
                    # [OPT-42] Extracted to _map_cwe_for_attack() to reduce judge() CC.
                    self._map_cwe_for_attack(_unified_result, _pre_verdict_evidence)
            except Exception as e:
                logger.debug(f"[V104.43 #CB] UnifiedDetector error: {e}")

        # 0c. ThreatDetector + AttackClassifierEngine — chỉ chạy nếu có v98_context (IP/headers)
        if v98_context and self.threat_detector and self.attack_classifier:
            try:
                # [V104.19 #2 FIX] ThreatDetector: always run via ThreadPoolExecutor
                try:
                    import concurrent.futures as _cf
                    _td_pool = _cf.ThreadPoolExecutor(max_workers=1)
                    v98_threat_signal = _td_pool.submit(
                        asyncio.run,  # [FIX-CRIT-27 BUG 1] was _asyncio.run (NameError — _asyncio never imported at module scope) → ThreatDetector dead
                        self.threat_detector.analyze(
                            ip=v98_context.get("ip", "127.0.0.1"),
                            headers=v98_context.get("headers", {}),
                            body=question,
                            endpoint=v98_context.get("endpoint", "/ask"),
                            session_id=v98_context.get("session_id", ""),
                        )
                    ).result(timeout=10)
                    v98_classification = self.attack_classifier.classify(v98_threat_signal)
                    _td_pool.shutdown(wait=False)
                except Exception as e:
                    logger.debug(f"[V98] ThreatDetector error: {e}")

                if v98_classification and v98_classification.severity == "critical":
                    logger.warning(f"[V98] Critical threat: actor={v98_classification.actor} attack={v98_classification.attack_type}")
                    v98_early_fail = True
            except Exception as e:
                logger.debug(f"[V98] ThreatDetector error: {e}")

        # Early FAIL nếu memory poisoning severe HOẶC critical threat detected
        if v98_early_fail:
            verdict = JudgeVerdict(
                question=question,
                slm_responses=[],
                final_answer="[BLOCKED BY V98 SECURITY] Memory poisoning or critical threat detected.",
                confidence=0.0,
                verdict="FAIL",
                reasoning=f"V98 early block: guard={v98_guard_verdict.recommendation if v98_guard_verdict else 'none'}, "
                         f"threat={v98_classification.severity if v98_classification else 'none'}, "
                         f"attack_match={v98_attack_match.get('matched', False) if v98_attack_match else False}",
                evidence={
                    "v98_guard": v98_guard_verdict.to_dict() if v98_guard_verdict else None,
                    "v98_threat": v98_threat_signal.to_dict() if v98_threat_signal else None,
                    "v98_classification": v98_classification.to_dict() if v98_classification else None,
                    "v98_attack_match": v98_attack_match,
                    "early_fail": True,
                },
                domain="security",
                timestamp=ts,
            )
            return verdict

        # [V104.52] Predictive Defense — run AttackPredictor when threat detected
        # TẠI SAO: V98 detects current threats, but predictor forecasts FUTURE attacks.
        # If predictor confidence > 0.7 + threat_type critical → trigger escalation.
        # This is the "30-min warning" mechanism — SCP predicts attack, alerts human,
        # if timeout → default defensive playbook executes.
        if self.attack_predictor and v98_classification and v98_classification.severity in ("medium", "high", "critical"):
            try:
                # Build signals from V98 context + threat data
                predictor_signals = {
                    "request_rate_anomaly": min(1.0, getattr(v98_threat_signal, 'confidence', 0.5) if v98_threat_signal else 0.3),
                    "geo_distribution_anomaly": 0.4 if (v98_threat_signal and v98_threat_signal.asn_intel and not v98_threat_signal.asn_intel.get("is_residential", True)) else 0.2,
                    "user_agent_pattern_shift": 0.3,
                    "payload_pattern_emergence": 0.6 if (v98_attack_match and v98_attack_match.get("matched")) else 0.2,
                    "historical_attack_pattern_match": 0.5 if v98_attack_match else 0.1,
                    "time_of_day_pattern": 0.3,
                    "threat_intel_correlation": 0.7 if v98_classification.severity in ("high", "critical") else 0.3,
                    "political_event_correlation": 0.1,
                }
                forecast = self.attack_predictor.predict_cyber_attack(predictor_signals)
                _pre_verdict_evidence["v10452_attack_forecast"] = {
                    "threat_type": forecast.threat_type,
                    "probability": forecast.probability,
                    "confidence": forecast.confidence,
                    "timeframe_min": forecast.timeframe_min,
                    "recommended_actions": forecast.recommended_actions,
                    "why": forecast.why_explanation[:200],
                }
                # If high confidence + critical → trigger escalation
                if (forecast.confidence > 0.5 and forecast.probability > 0.5
                        and self.escalation_manager):
                    _threat = {
                        "id": f"predict_{int(time.time())}",
                        "type": forecast.threat_type,
                        "severity": v98_classification.severity,
                        "prediction_confidence": forecast.confidence,
                        "description": forecast.why_explanation[:200],
                    }
                    self.escalation_manager.on_threat_detected(_threat)
                    _pre_verdict_evidence["v10452_escalation_triggered"] = True
                    logger.warning(f"[V104.52] Predictive escalation: {forecast.threat_type} conf={forecast.confidence:.2f}")
            except Exception as e:
                logger.debug(f"[V104.52] Predictor error: {e}")

        # [FIX-CRIT-27 BUG 3] KB short-circuit MOVED here (was above Step 0a-0c,
        # bypassing ALL V98 security). Now security checks have already run —
        # if v98_early_fail was True, we returned above. Otherwise, the KB
        # short-circuit is safe to apply (authoritative cached answer for a
        # question that PASSED all security checks).
        # Note: verdict_type/final_answer/confidence may be unset if KB didn't hit —
        # use getattr-style safe checks.
        _kb_sc_verdict = locals().get('verdict_type', None)
        _kb_sc_answer = locals().get('final_answer', '')
        _kb_sc_conf = locals().get('confidence', 0.0)
        if _kb_sc_verdict == "PASS" and _kb_sc_answer and _kb_sc_conf >= 0.85 and v100_kb_hits:
            _best_hit = v100_kb_hits[0]
            _hit_tier = getattr(_best_hit, 'source_tier', 9)
            if _hit_tier <= 2:
                logger.info(f"[V104.46 #BI] KB short-circuit early return (post-security): tier={_hit_tier}")
                verdict = JudgeVerdict(
                    question=question,
                    final_answer=final_answer,
                    confidence=confidence,
                    verdict=verdict_type,
                    domain="general",
                    reasoning=reasoning,
                    evidence={"v100_kb_short_circuit": True, "source_tier": _hit_tier},
                    timestamp=ts,
                )
                return verdict

        # [V104.44 #BX] [P0-4/P1-6 FIX] ErrorStoreIndex.search_similar — check if
        # this question is similar to past FAILs. TẠI SAO: V104.44 fix had 4 bugs:
        #   (1) Event-loop logic INVERTED: `run_until_complete(...) if is_running()`
        #       — but run_until_complete RAISES RuntimeError when loop is running.
        #       The condition should be: if NOT running → run_until_complete;
        #       if running → use sync fallback. Was backwards.
        #   (2) `verdict.evidence[...]` — verdict not created yet. Fix: store in
        #       `_pre_verdict_evidence`, merge after verdict creation.
        #   (3) `adjusted_threshold` computed here was OVERWRITTEN at line ~1453
        #       (`adjusted_threshold = self.confidence_threshold`) and ~1464
        #       (domain tolerance). Fix: store as `_es_index_threshold_boost` and
        #       apply AFTER those resets.
        #   (4) `search_similar(question, limit=3)` kwarg — ErrorStoreIndex uses
        #       `top_k` (FIX-D added `limit` alias, so this now works).
        if self.error_store_index:
            try:
                _similar = None
                # Prefer sync path — avoids event-loop entanglement entirely.
                if hasattr(self.error_store_index, '_search_sync'):
                    _similar = self.error_store_index._search_sync(question, limit=3)
                else:
                    import asyncio as _a
                    try:
                        _loop = _a.get_event_loop()
                        if _loop.is_running():
                            # Cannot run_until_complete inside a running loop —
                            # schedule in a thread to avoid blocking + RuntimeError.
                            import asyncio as _a2
                            _similar = _a2.run_coroutine_threadsafe(
                                self.error_store_index.search_similar(question, top_k=3),
                                _loop
                            ).result(timeout=2.0)
                        else:
                            _similar = _loop.run_until_complete(
                                self.error_store_index.search_similar(question, top_k=3)
                            )
                    except Exception:
                        _similar = None
                if _similar:
                    _pre_verdict_evidence["v104_similar_errors"] = [
                        {"question": (s.get("question", "") if isinstance(s, dict) else getattr(s, "question", ""))[:100],
                         "verdict": s.get("verdict", "") if isinstance(s, dict) else getattr(s, "verdict", "")}
                        for s in _similar[:3]
                    ]
                    # If similar past errors were FAIL → raise confidence threshold
                    _fail_count = sum(1 for s in _similar
                                      if (s.get("verdict", "") if isinstance(s, dict) else getattr(s, "verdict", "")) == "FAIL")
                    if _fail_count >= 2:
                        _es_index_threshold_boost = 0.1  # applied after domain-tolerance reset
                        logger.info(f"[V104.44 #BX] Similar errors: {_fail_count} FAILs → threshold boost +{_es_index_threshold_boost}")
            except Exception as e:
                logger.debug(f"[V104.44 #BX] ErrorStoreIndex search error: {e}")

        # Step 1: Route -> SLMs
        domains = self._route_question(question)

        # [V90 OPT] Skip PolicyApplier — minor effect, high overhead
        applied_principle_ids: list[int] = []
        adjusted_threshold = self.confidence_threshold

        # [V93.6] Apply DOMAIN_BIAS lessons from ExperienceEngine (lightweight, cheap TTL read)
        # Was: experiences learned lessons but never applied (3343 lessons, 0 applied)
        if domains:
            try:
                exp_pol = self._get_exp_policies()
                tolerances = exp_pol.get("domain_tolerances", {})
                primary_dom = domains[0]
                if primary_dom in tolerances:
                    # Higher tolerance -> lower the bar for PASS (domain known to be noisy/hard)
                    adjusted_threshold = min(0.9, self.confidence_threshold * tolerances[primary_dom])
            except Exception as e:
                logger.debug(f"[V93.6] domain_tolerance apply error: {e}")

        # [V104.39 #A] TẠI SAO: V90 OPT disabled closed-loop via `if False`.
        # Re-enabled via env var SCP_ENABLE_CLOSED_LOOP=1 (opt-in).
        # Without this, PolicyApplier (lessons → threshold) is dead → no self-correction.
        # _enable_closed_loop defined at top of judge() (line ~1227) — shared by all.
        if _enable_closed_loop and self.policy_applier and domains:
            try:
                primary_dom = domains[0]
                adjustment = self.policy_applier.get_adjustment(
                    question, primary_dom, base_threshold=self.confidence_threshold
                )
                adjusted_threshold = adjustment["confidence_threshold"]
                adjustment.get("prefer_sources", [])
                adjustment.get("avoid_sources", [])
                applied_principle_ids = adjustment.get("applied_principle_ids", [])
            except Exception as e:
                logger.debug(f"PolicyApplier error: {e}")

        # [P1-6 FIX] Apply ErrorStoreIndex similar-FAIL threshold boost AFTER
        # domain-tolerance + PolicyApplier resets. TẠI SAO: V104.44 #BX computed
        # `adjusted_threshold = max(0.65, self.confidence_threshold + 0.1)` but it
        # was immediately overwritten by lines 1509 (self.confidence_threshold) and
        # 1520/1534 (domain tolerance + PolicyApplier). Storing as a separate boost
        # and applying here ensures it actually takes effect.
        if _es_index_threshold_boost > 0:
            adjusted_threshold = min(0.95, adjusted_threshold + _es_index_threshold_boost)

        # [V90 OPT] Skip VerdictPredictor — minor effect, high overhead
        prediction_skip_api = False
        prediction_verdict = None
        # [V104.39 #A] Re-enabled VerdictPredictor (was: if False)
        if _enable_closed_loop and self.predictor and domains:
            try:
                pred = self.predictor.predict(question, domains[0], ai_answer)
                prediction_verdict = pred.verdict
                if pred.skip_api and pred.confidence > 0.85:
                    prediction_skip_api = True
            except Exception as e:
                logger.debug(f"VerdictPredictor error: {e}")

        # Step 2: Gọi SLMs
        # [V32.1] PARALLEL SLM execution — chạy tất cả SLMs song song
        # Speedup: 4 SLMs × 500ms sequential = 2s → // = 500ms (4x faster)
        slm_responses = []

        def _call_slm(domain: str) -> dict:
            """Call 1 SLM, return response dict."""
            slm = self.slms.get(domain)
            if slm:
                try:
                    # [V5.7-WHY] Change 2: WHY pre-routing — pass sources_to_query as hint.
                    # TẠI SAO: WHY engine created a VerificationPlan early (before SLMs
                    # run, see line ~1381). The plan's sources_to_query lists which
                    # sources WHY thinks are authoritative for this question (e.g.,
                    # ["Wikipedia", "LocalDB", "REST Countries API"]). Setting this
                    # as an attribute on the SLM lets SLMs that know about it
                    # prioritize those sources first. SLMs that don't read the
                    # attribute simply ignore it (PASSIVE — no behavior change).
                    # SAFETY: opt-in via SCP_WHY_PRE_ROUTE=1 (default OFF). Wrap in
                    # try/except — hint setting MUST NOT break SLM call. SLM logic
                    # is NOT changed — hint is purely advisory.
                    try:
                        if os.environ.get("SCP_WHY_PRE_ROUTE", "0") == "1" and why_plan:
                            slm.why_sources_hint = list(why_plan.sources_to_query)
                            logger.debug(
                                f"[V5.7-WHY] pre-route hint set on {domain} SLM: "
                                f"{why_plan.sources_to_query}"
                            )
                        else:
                            # Clear any stale hint from previous call (defensive)
                            if hasattr(slm, "why_sources_hint"):
                                try:
                                    delattr(slm, "why_sources_hint")
                                except Exception as e:
                                    logger.warning(f"Silent except: {e}")
                    except Exception as _hint_err:
                        logger.debug(f"[V5.7-WHY] pre-route hint set failed (non-fatal): {_hint_err}")

                    resp = slm.predict(question)
                    return {
                        "domain": domain,
                        "answer": resp.answer,
                        "confidence": resp.confidence,
                        "reasoning": resp.reasoning,
                        "evidence": resp.evidence,
                        "slm_name": resp.slm_name,
                        "processing_time": resp.processing_time,
                    }
                except Exception as e:
                    return {
                        "domain": domain,
                        "error": str(e),
                        "confidence": 0.0,
                    }
            else:
                # Domain has no SLM — use V13 RealityEngine directly
                try:
                    v13_result = self.v13.process(question, ai_answer)
                    real_value = v13_result.real_value
                    source = v13_result.source or "v13"
                    if real_value is not None:
                        return {
                            "domain": domain,
                            "answer": f"= {real_value}",
                            "confidence": 0.85,
                            "reasoning": f"V13 RealityEngine: {source}",
                            "evidence": {"source": source, "value": real_value},
                            "slm_name": "V13Reality",
                            "processing_time": 0.0,
                        }
                except Exception as e:
                    # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed V13
                    # RealityEngine errors silently → falls through to "no SLM" return.
                    logger.warning(f"[judge] V13Reality fallback failed: {e}")
                return {"domain": domain, "error": "no SLM", "confidence": 0.0}

        if len(domains) == 1:
            # [V88] Multi-SLM consensus: always add a second opinion
            # Was: only call 1 SLM → no cross-verification → "no ground truth"
            # Now: if only 1 domain routed, also call "universal" or "general" as 2nd opinion
            primary_domain = domains[0]
            # Don't add duplicate if primary IS universal/general
            if primary_domain not in ("universal", "general"):
                domains.append("universal")
            slm_responses.append(_call_slm(domains[0]))
            if len(domains) > 1:
                slm_responses.append(_call_slm(domains[1]))
        else:
            # [V32.1] Multiple domains — run in parallel
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(4, len(domains))) as executor:
                future_to_domain = {executor.submit(_call_slm, d): d for d in domains}
                for future in as_completed(future_to_domain, timeout=30):
                    try:
                        result = future.result(timeout=15)
                        slm_responses.append(result)
                    except Exception as e:
                        domain = future_to_domain[future]
                        slm_responses.append({
                            "domain": domain,
                            "error": str(e),
                            "confidence": 0.0,
                        })

        if not slm_responses:
            # [V110] SLM không có answer → gọi LLM (Ollama/OpenRouter)
            if self.llm_client:
                try:
                    import asyncio as _a
                    # Build context from KB hits
                    llm_context = ""
                    if v100_kb_hits:
                        llm_context = "\n".join(f"- {h.answer[:200]}" for h in v100_kb_hits[:3])

                    # [V104.19 #2 FIX] LLM fallback: always run via ThreadPoolExecutor
                    try:
                        import concurrent.futures as _cf
                        _llm_pool = _cf.ThreadPoolExecutor(max_workers=1)
                        llm_answer, llm_model = _llm_pool.submit(
                            _a.run,
                            self.llm_client.chat(question, context=llm_context, task="judge")
                        ).result(timeout=10)  # [V104.19 #3] was 30s, reduce to 10s
                        _llm_pool.shutdown(wait=False)
                    except RuntimeError:
                        # [FIX-CRIT-27 BUG 2] was AFTER except Exception (unreachable — RuntimeError is subclass of Exception). Reorder: specific first.
                        llm_answer, llm_model = _a.run(
                            self.llm_client.chat(question, context=llm_context, task="judge")
                        )
                    except Exception as e:
                        llm_answer, llm_model = "", f"llm_error: {e}"

                    if llm_answer and "không khả dụng" not in llm_answer:
                        # LLM returned answer → use as final_answer, but with LOW confidence (needs verification)
                        return JudgeVerdict(
                            question=question, slm_responses=[],
                            final_answer=llm_answer[:2000],
                            confidence=0.3,  # LOW — LLM answer chưa verify
                            verdict="UNKNOWN",  # Still UNKNOWN — LLM answer needs verification
                            reasoning=f"SLM không có answer → LLM ({llm_model}) trả lời — chưa verify, confidence thấp",
                            evidence={"scope": "llm_fallback", "llm_model": llm_model},
                            domain=(domains[0] if domains else "unknown"),
                            cross_validation={}, slm_scores={}, timestamp=ts,
                        )
                except Exception as e:
                    logger.debug(f"[V110] LLM fallback error: {e}")

            return JudgeVerdict(
                question=question, slm_responses=[], final_answer="[SCP] Tôi không có đủ thông tin để trả lời câu hỏi này. Không có SLM nào có dữ liệu liên quan đến lĩnh vực này.",
                confidence=0.0, verdict="UNKNOWN", reasoning="Không có SLM nào response — ngoài phạm vi kiến thức hiện có",
                evidence={"scope": "out_of_scope"}, domain=(domains[0] if domains else "unknown"), cross_validation={}, slm_scores={},
                timestamp=ts,
            )

        # Step 3: Cross-check SLMs
        # [V90 FIX] Filter out garbage SLM answers (single digit, 1-2 char nonsense)
        valid_responses = []
        for r in slm_responses:
            if "error" in r or not r.get("answer"):
                continue
            ans = str(r["answer"]).strip()
            r_domain = r.get("domain", "")
            # Skip 1-2 char answers from non-math domains (likely garbage)
            if len(ans) <= 2 and r_domain not in ("math", "conversion", "reality"):
                logger.debug(f"Filtering garbage SLM answer: '{ans}' from {r_domain}")
                continue
            valid_responses.append(r)

        if not valid_responses:
            # All answers were garbage — fall back to original
            valid_responses = [r for r in slm_responses if "error" not in r and r.get("answer")]

        is_consistent = self._check_consistency(valid_responses)

        # [V29] Step 3.5: ConflictResolver — nếu có nhiều SLM responses với values khác nhau
        # → dùng ConflictResolver để pick winner
        if len(valid_responses) >= 2:
            try:
                from scp.core.conflict_resolver import resolve_value
                # Get initial confidence from best response
                confidence = max((r.get("confidence", 0) for r in valid_responses), default=0)
                # Extract values from each SLM response
                values_for_resolution = []
                for r in valid_responses:
                    val = r.get("evidence", {}).get("value")
                    if val is not None:
                        values_for_resolution.append({
                            "value": val,
                            "source": r.get("evidence", {}).get("source", r.get("slm_name", "?")),
                        })
                if len(values_for_resolution) >= 2:
                    conflict_result = resolve_value(values_for_resolution, strategy="weighted_avg")
                    # [V91 FIX] Actually USE the conflict resolution result!
                    if conflict_result.conflict_detected:
                        # Sources disagree → reduce confidence proportional to disagreement
                        agreement = getattr(conflict_result, 'agreement_score', 0.5)
                        if agreement < 0.5:
                            # Strong disagreement → mark as CONFLICT, not PASS
                            confidence = max(0.2, confidence * agreement)
                            logger.info(f"[CONFLICT] Sources disagree (agreement={agreement:.2f}): "
                                       f"{[(v['source'], v['value']) for v in values_for_resolution]}")
                        else:
                            # Mild disagreement → use resolved value, reduce confidence
                            resolved_val = getattr(conflict_result, 'final_value', None)
                            if resolved_val is not None:
                                # Update primary answer with resolved value
                                for r in valid_responses:
                                    if r.get("evidence", {}).get("source") == conflict_result.source:
                                        r["answer"] = f"= {resolved_val}"
                                        r["evidence"]["value"] = resolved_val
                                        break
                                confidence = confidence * (0.7 + 0.3 * agreement)
                    else:
                        # All sources agree → boost confidence
                        confidence = min(0.97, confidence + 0.05)
            except Exception as e:
                logger.debug(f"ConflictResolver error: {e}")

        # Step 4: Tính scores
        slm_scores = {}
        for r in valid_responses:
            slm_scores[r.get("domain", "unknown")] = {
                "score": r.get("confidence", 0),
                "answer": r.get("answer", ""),
            }

        # Step 5: Chọn primary response
        # [V104.47 #1] TẠI SAO: was pure argmax(confidence) → SLM tự tin thái quá
        # luôn thắng dù sai. Fix: consensus-aware selection — if 2+ SLMs agree
        # on same answer, boost that answer's effective confidence.
        if valid_responses:
            # Group by answer similarity (word overlap ≥ 0.6 = "agree")
            from difflib import SequenceMatcher
            _groups = []  # [{answer, conf, count, members}]
            for r in valid_responses:
                _r_ans = (r.get("answer") or "").strip().lower()
                _matched = False
                for _g in _groups:
                    _g_ans = _g["answer"]
                    if _r_ans and _g_ans:
                        _sim = SequenceMatcher(None, _r_ans, _g_ans).ratio()
                        if _sim >= 0.6:
                            _g["count"] += 1
                            _g["members"].append(r)
                            _matched = True
                            break
                if not _matched:
                    _groups.append({"answer": _r_ans, "conf": r.get("confidence", 0), "count": 1, "members": [r]})

            # Pick group with highest consensus: count * avg_conf
            _best_group = max(_groups, key=lambda g: g["count"] * sum(m.get("confidence", 0) for m in g["members"]) / max(len(g["members"]), 1))
            # If consensus group has 2+ members, use it; else fall back to argmax
            if _best_group["count"] >= 2:
                primary = max(_best_group["members"], key=lambda x: x.get("confidence", 0))
                _consensus_boost = 0.05 * (_best_group["count"] - 1)  # +0.05 per agreeing SLM
                confidence_boost = _consensus_boost
            else:
                primary = max(valid_responses, key=lambda x: x.get("confidence", 0))
                confidence_boost = 0
        else:
            primary = None
            confidence_boost = 0
        # [FIX 2026-07-09] Was: hardcoded "unknown" when no SLM returned a useful
        # answer (primary=None) — this DISCARDED the correctly-routed domain from
        # Step 1 (self._route_question), causing the dashboard to show domain="unknown"
        # for ~87% of live queries even when routing worked fine (e.g. "cocktail" -> food,
        # "capital city" -> geography). Now falls back to the routed domain instead of
        # discarding it. Does NOT change verdict/confidence logic — reporting fix only.
        _routed_fallback_domain = domains[0] if domains else "unknown"
        primary_domain = primary.get("domain", _routed_fallback_domain) if primary else _routed_fallback_domain
        final_answer = primary.get("answer", "") if primary else ""
        # [P2-19 FIX] TẠI SAO: `confidence_boost` was computed above (consensus
        # bonus: +0.05 per agreeing SLM) but NEVER applied — line 1831 overwrote
        # `confidence = primary.get("confidence", 0)` without adding the boost.
        # The consensus bonus was dead code. Fix: apply it here, capped at 0.95.
        # [AUDIT-3 FIX] Add minimum per-SLM confidence gate: don't boost if the
        # individual SLMs are weak (< 0.5). TẠI SAO: without a gate, 5 weak SLMs
        # at 0.35 each → +0.20 → 0.55 → PASS. This is "ảo giác đồng thuận" —
        # many weak sources agreeing doesn't make them right (Invariant #2:
        # SLM ≠ reality). Gate: only boost if ALL agreeing SLMs have conf >= 0.5.
        confidence = primary.get("confidence", 0) if primary else 0
        try:
            if 'confidence_boost' in dir() and confidence_boost:
                # [AUDIT-3] Check that all SLMs in the consensus group have
                # individual confidence >= 0.5 before applying the boost.
                _all_strong = True
                if '_best_group' in dir() and _best_group:
                    for _member in _best_group.get("members", []):
                        if _member.get("confidence", 0) < 0.5:
                            _all_strong = False
                            break
                if _all_strong:
                    confidence = min(0.95, confidence + confidence_boost)
                else:
                    logger.debug("[AUDIT-3] consensus boost skipped — some SLMs < 0.5 conf")
        except Exception as e:
            logger.warning(f"Silent except: {e}")  # confidence_boost may be undefined in edge paths — safe to skip

        # [V31C] Apply calibration factor — auto-tune confidence from history
        if self.calibration and primary_domain:
            try:
                confidence = self.calibration.apply_calibration(confidence, primary_domain)
                if os.environ.get('SCP_DEBUG_CONF'):
                    import sys as _sys
                    print(f'  [DBG] After calibration: {confidence} (factor applied)', file=_sys.stderr)
            except Exception as e:
                logger.debug(f"Calibration apply error: {e}")

        # [V93.6] Apply CONFIDENCE_TUNING lessons from ExperienceEngine
        # (recurring low-confidence sources/domains flagged for extra scrutiny)
        if primary_domain:
            try:
                exp_pol = self._get_exp_policies()
                conf_adj = exp_pol.get("confidence_adjustments", {})
                if primary_domain in conf_adj:
                    confidence = confidence * conf_adj[primary_domain]
            except Exception as e:
                logger.debug(f"[V93.6] confidence_adjustment apply error: {e}")

        # ============================================================
        # [ROOT-FIX 43-A / Fix 1] Trust deterministic SLMs when conf >= 0.95
        # WHY: MathSLM uses AST evaluator (not LLM) — its answer IS reality.
        # Requiring DataSource verification for "2+2=4" is wasteful + returns UNKNOWN.
        # DNA SCP #1: Reality > Model — deterministic evaluator IS reality.
        # Also covers ConversionSLM (deterministic unit math), StatisticsSLM
        # (deterministic aggregation), LogicSLM (deterministic truth tables).
        # When SLM is one of these AND conf >= 0.95, skip WHY/adversary/reality
        # checks (they cannot improve on a deterministic answer) and return PASS.
        # SAFETY: V98 security checks (Step 0a-0c) already ran at lines 268-358
        # before SLM predict — so attacks are still blocked before this short-circuit.
        # ============================================================
        DETERMINISTIC_SLMS = ("MathSLM", "ConversionSLM", "StatisticsSLM", "LogicSLM")
        if primary and confidence >= 0.95:
            _slm_name = str(primary.get("slm_name", "") or primary.get("source", "") or "")
            if any(_d in _slm_name for _d in DETERMINISTIC_SLMS):
                logger.info(
                    f"[ROOT-FIX 43-A] Trusting deterministic SLM '{_slm_name}' "
                    f"(conf={confidence:.2f}) — skipping WHY/adversary verification"
                )
                return JudgeVerdict(
                    question=question,
                    slm_responses=slm_responses,
                    final_answer=final_answer,
                    confidence=confidence,
                    verdict="PASS",
                    reasoning=f"Deterministic SLM ({_slm_name}) — high confidence (conf={confidence:.2f}), no external verification needed (DNA SCP #1: Reality > Model)",
                    evidence={
                        "primary_domain": primary_domain,
                        "deterministic_slm": True,
                        "slm_name": _slm_name,
                        "skip_why_verification": True,
                        "root_fix": "43-A/Fix1",
                    },
                    domain=primary_domain,
                    cross_validation={
                        "answers": [r.get("answer", "") for r in valid_responses],
                        "consistency_score": self._consistency_score(valid_responses),
                    },
                    slm_scores=slm_scores,
                    reality_check={
                        "is_correct": True,
                        "reason": f"Deterministic SLM ({_slm_name}) — answer IS reality",
                        "source": _slm_name,
                    },
                    timestamp=ts,
                    similar_errors=[],
                )

        # [V29] Step 5.5: Adversary verify — cross-validate với source khác
        # Chỉ chạy nếu SLM primary có real value và không skip API
        # [V29.2] Mở rộng cho history/biology/reality/geography (Wikipedia adversary)
        # [V32] Skip adversary khi confidence đã rất cao (>0.92) — tiết kiệm 100-300ms
        adversary_result = None
        # [V104.40 #K-skip] TẠI SAO: V91 skipped adversary when confidence >= 0.92.
        # This is BACKWARDS — overconfident SLM is exactly when skeptical cross-check
        # is most needed (No-Hallucination principle). Fix: remove the 0.92 bypass.
        # Adversary runs for ALL domains regardless of confidence.
        if (self.adversary and primary and not prediction_skip_api):
            try:
                primary_value = primary.get("evidence", {}).get("value")
                primary_source = primary.get("evidence", {}).get("source", "")
                # Extract entity từ question
                entity = primary.get("evidence", {}).get("entity", "")
                if primary_value is not None and primary_source:
                    adv = self.adversary.verify(
                        primary_value, primary_source,
                        primary_domain, question, entity or ""
                    )
                    if adv.adversary_values:  # Only use if adversary actually ran
                        adversary_result = adv
                        # [V86 FIX] Was: confidence = adv.final_confidence
                        #   → adversary returns 0.3 when it can't fully verify
                        #   → overrides SLM's 0.85 → verdict becomes PARTIAL instead of PASS
                        # Now: adversary can only BOOST confidence (if it agrees)
                        #   or REDUCE only if it finds a CONFLICT (different value)
                        #   If adversary can't verify (no conflict, no agreement) → keep SLM confidence
                        if adv.conflict_detected and adv.agreement_score < 0.85:
                            # [V104.40 #K] [P0-4 FIX] TẠI SAO: was `if verdict_type == "PASS":
                            # verdict_type = "CONFLICT"` — but verdict_type is None here (Step 5.5
                            # runs BEFORE Step 6 sets verdict_type) → NameError → swallowed by
                            # except → adversary conflict signal never reached the verdict state
                            # machine. Fix: record as a flag; apply AFTER Step 6 finalizes
                            # verdict_type (before JudgeVerdict construction at ~line 2251).
                            # Constitution: "reality outranks model opinion" — adversary found
                            # DIFFERENT value from independent source → must be CONFLICT, not PASS.
                            confidence = max(0.3, confidence - 0.2)
                            _adversary_conflict = True
                            _adversary_conflict_reason = f". Adversary conflict: agreement={adv.agreement_score:.2f} (source says different value)"
                        elif adv.agreement_score >= 0.85:
                            # Adversary AGREES → boost confidence
                            confidence = min(0.95, confidence + 0.05)
                        # else: adversary couldn't verify → keep SLM confidence (don't change)
                    else:
                        # [V48 FIX] Adversary didn't run — but result still has useful confidence
                        # Trước V48: ignore adversary result entirely → keep SLM confidence (good)
                        # V48: if adversary final_confidence is HIGHER → boost; if LOWER → keep SLM
                        # Don't reduce confidence just because adversary couldn't run
                        if os.environ.get('SCP_DEBUG_CONF'):
                            import sys as _sys
                            print(f'  [DBG] Adversary no_values, final_conf={adv.final_confidence}', file=_sys.stderr)
                        # [V48] Only update confidence if adversary is MORE confident (cross-checked)
                        # Otherwise preserve SLM confidence
                        if adv.final_confidence > confidence:
                            confidence = adv.final_confidence
            except Exception as e:
                logger.debug(f"Adversary verify error: {e}")

        # [V88 FIX] Initialize reality_check early — used in Step 6 consensus check before Step 7 defines it
        reality_check = {"is_correct": None, "reason": "Not yet checked"}
        # Step 6: Quyết định verdict
        # Use adjusted_threshold from PolicyApplier
        effective_threshold = adjusted_threshold
        # [FIX v27] Check if AI answer matches SLM answer
        # [V48-V49] tolerance-based + scientific notation + string comparison
        # [V51 FIX] Multiple critical bug fixes:
        #   - Math: extract ONLY the result number (after '='), not all numbers
        #   - Reality: tightened tolerance for scientific notation (was too loose)
        #   - Geography: full-string comparison, not just last word (was "city"=="city")
        #   - Unknown: empty AI answer → UNKNOWN, not PARTIAL
        if is_consistent and confidence > effective_threshold:
            # SLM says answer is X, but did AI say X?
            slm_answer = final_answer
            if ai_answer and slm_answer:
                # [V73] FIRST: Try value extraction for question-type-aware comparison
                # Was (V51-V72): only numeric comparison + text overlap
                #   → "calories=52" (AI) vs "Apple family Rosaceae calories=52 sugar=10.3g..."
                #     → SLM picks wrong number (0.3) → FAIL (wrong!)
                # Now (V73): extract comparable value (year/number/type/quote) BEFORE numeric path
                ai_extracted = self._extract_value(question, ai_answer)
                slm_extracted = self._extract_value(question, slm_answer)
                if ai_extracted and slm_extracted:
                    # Direct value match
                    if ai_extracted.lower() == slm_extracted.lower():
                        verdict_type = "PASS"
                        reasoning = f"Value match: '{ai_extracted[:50]}' == '{slm_extracted[:50]}'"
                    elif ai_extracted.lower() in slm_extracted.lower() or slm_extracted.lower() in ai_extracted.lower():
                        # Substring match (one is part of the other)
                        verdict_type = "PASS"
                        reasoning = f"Value substring match: '{ai_extracted[:50]}' ⊆ '{slm_extracted[:50]}'"
                    else:
                        # No value match — fall through to numeric/string comparison
                        verdict_type = None  # will be set by next section
                else:
                    verdict_type = None  # fall through

                # If value extraction didn't give a clear answer, use legacy comparison
                if verdict_type is None:
                    # [V51] Special handling for math: extract ONLY result (after '=')
                    import re
                    # If SLM answer has '=', extract only the part after '='
                    slm_result_part = slm_answer
                    ai_result_part = ai_answer
                    if '=' in slm_answer:
                        slm_result_part = slm_answer.split('=')[-1].strip()
                    if '=' in ai_answer:
                        ai_result_part = ai_answer.split('=')[-1].strip()

                    num_pattern = r'-?\d+\.?\d*(?:[eE][+-]?\d+)?'
                    slm_nums = re.findall(num_pattern, slm_result_part)
                    ai_nums = re.findall(num_pattern, ai_result_part)

                    if slm_nums and ai_nums:
                        # [V51] For math, take the FIRST (and usually only) number from result
                        # For multi-number answers (astronomy), find closest
                        ai_val = float(ai_nums[0])  # [V51] was [-1], should be [0] for result
                        best_slm_val = None
                        best_diff = float('inf')
                        for sn in slm_nums:
                            try:
                                sv = float(sn)
                                diff = abs(sv - ai_val)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_slm_val = sv
                            except ValueError:
                                continue
                        if best_slm_val is None:
                            best_slm_val = float(slm_nums[0])
                        slm_val = best_slm_val

                        # [V51 FIX] Improved tolerance for scientific notation
                        # [V52 FIX] Use much smaller floor (1e-300) instead of 1e-10
                        # Trước V52: max(abs_max, 1e-300) → for 6.626e-34, floor=1e-10 → rel_diff≈0 → PASS (wrong!)
                        # V52: max(abs_max, 1e-300) → rel_diff correctly = 0.5 for planck perturbed case
                        abs_diff = abs(slm_val - ai_val)
                        abs_max = max(abs(slm_val), abs(ai_val))
                        rel_diff = abs_diff / max(abs_max, 1e-300)

                        # [V51] Tighter tolerance:
                        # - For |val| >= 1: 0.5% relative tolerance
                        # - For 0 < |val| < 1: 1% relative tolerance (was 0.5%, too loose for tiny numbers)
                        # - For scientific notation (val very small/large): ALWAYS use relative
                        # - Absolute tolerance 0.01 only for very small numbers near 0
                        if abs_max >= 1:
                            tolerance = abs_max * 0.005  # 0.5% relative
                        elif abs_max >= 0.01:
                            tolerance = abs_max * 0.01  # 1% relative for small numbers
                        else:
                            tolerance = abs_max * 0.02  # 2% relative for very small

                        # [V51] For very large/small (scientific notation), use stricter relative check
                        is_sci = abs_max > 1e6 or (abs_max > 0 and abs_max < 1e-3)
                        if is_sci:
                            # Scientific notation: 1% relative tolerance
                            if rel_diff > 0.01:
                                verdict_type = "FAIL"
                                reasoning = f"SLM tính {slm_val:.6g}, AI nói {ai_val:.6g} (chênh {rel_diff*100:.2f}%)"
                            else:
                                verdict_type = "PASS"
                                reasoning = "Các SLM đồng thuận, đạt ngưỡng tin cậy"
                        elif abs_diff > tolerance and rel_diff > 0.005:
                            verdict_type = "FAIL"
                            reasoning = f"SLM tính {slm_val}, AI nói {ai_val} (chênh {abs_diff:.4f})"
                        else:
                            verdict_type = "PASS"
                            reasoning = "Các SLM đồng thuận, đạt ngưỡng tin cậy"
                    else:
                        # [V51 FIX] No numbers — FULL string comparison, not just last word
                        # Trước V51: chỉ compare last word → "Mexico City" vs "Nowhere City" → "city"=="city" → PASS (wrong!)
                        # V51: require full answer string match (or substring)
                        slm_str = slm_answer.strip().lower().rstrip('.?!,;:').replace('.', '')  # [V90] strip periods
                        ai_str = ai_answer.strip().lower().rstrip('.?!,;:').replace('.', '')  # [V90] strip periods

                        # [V51] Strip common prefixes like "thủ đô của X là" to compare only the answer
                        answer_prefixes = [
                            r'^thủ đô của \w+ là\s+',
                            r'^capital of \w+ is\s+',
                            r'^=\s*',
                        ]
                        for pat in answer_prefixes:
                            slm_str = re.sub(pat, '', slm_str).strip()
                            ai_str = re.sub(pat, '', ai_str).strip()

                        # [V73] Value extraction — extract comparable values BEFORE compare
                        # Was: compare raw text "SLM says '5.6834e+26 (loại: gas giant)' vs AI 'planet'"
                        #   → overlap 0% → FAIL (wrong!)
                        # Now: extract specific value (year, number, type) → compare values
                        ai_value = self._extract_value(question, ai_str)
                        slm_value = self._extract_value(question, slm_str)
                        if ai_value and slm_value:
                            # Use extracted values for comparison
                            ai_compare = ai_value
                            slm_compare = slm_value
                        else:
                            # Fallback to raw answer
                            ai_compare = ai_str
                            slm_compare = slm_str

                        # [V73] Direct value match — if extracted values match exactly, PASS
                        if ai_value and slm_value and ai_value.lower() == slm_value.lower():
                            verdict_type = "PASS"
                            reasoning = f"Value match: '{ai_value}' == '{slm_value}'"
                        elif not ai_compare or not slm_compare:
                            # [V51] Empty answer → FAIL (was PASS)
                            verdict_type = "FAIL"
                            reasoning = "Empty answer from SLM or AI"
                        elif ai_str in slm_str or slm_str in ai_str:
                            # Check that the match is substantial (not just "city" matching "city")
                            match_len = min(len(ai_str), len(slm_str))
                            if match_len >= 3:  # [V51] require at least 3 chars match
                                verdict_type = "PASS"
                                reasoning = "Các SLM đồng thuận (string match), đạt ngưỡng tin cậy"
                            else:
                                verdict_type = "FAIL"
                                reasoning = f"String match too short: '{ai_str}' vs '{slm_str}'"
                        else:
                            # [V51.1 FIX] Fuzzy match: remove stopwords then re-compare
                            # VD: "dời đô thăng long" vs "dời đô ra thăng long" → remove "ra" → match
                            stopwords = [' ra ', ' của ', ' là ', ' và ', ' được ', ' tại ', ' ở ',
                                        ' bằng ', ' có ', ' cho ', ' từ ', ' vào ', ' lên ', ' xuống ']
                            slm_clean = slm_str
                            ai_clean = ai_str
                            for sw in stopwords:
                                slm_clean = slm_clean.replace(sw, ' ')
                                ai_clean = ai_clean.replace(sw, ' ')
                            # Collapse multiple spaces
                            slm_clean = ' '.join(slm_clean.split())
                            ai_clean = ' '.join(ai_clean.split())

                            if ai_clean in slm_clean or slm_clean in ai_clean:
                                match_len = min(len(ai_clean), len(slm_clean))
                                if match_len >= 3:
                                    verdict_type = "PASS"
                                    reasoning = "Các SLM đồng thuận (fuzzy string match), đạt ngưỡng tin cậy"
                                else:
                                    verdict_type = "FAIL"
                                    reasoning = f"Fuzzy match too short: '{ai_clean}' vs '{slm_clean}'"
                            else:
                                # [V51.2] Word overlap check — if ≥60% of AI words appear in SLM
                                ai_words = set(ai_clean.split())
                                slm_words = set(slm_clean.split())
                                if ai_words and slm_words:
                                    overlap = len(ai_words & slm_words) / len(ai_words)
                                    if overlap >= 0.6:
                                        verdict_type = "PASS"
                                        reasoning = f"Các SLM đồng thuận (word overlap {overlap*100:.0f}%), đạt ngưỡng tin cậy"
                                    else:
                                        verdict_type = "FAIL"
                                        reasoning = f"SLM says '{slm_str[:30]}', AI says '{ai_str[:30]}' (overlap {overlap*100:.0f}%)"
                                else:
                                    verdict_type = "FAIL"
                                    reasoning = f"SLM says '{slm_str[:30]}', AI says '{ai_str[:30]}' (string mismatch)"
            else:
                # [V51] No AI answer or no SLM answer
                # [V104.40 #M] TẠI SAO: V90 LEARN let SLM self-confirm (is_correct=True,
                # verdict=PASS) when no AI answer — VIOLATES Evidence-First / No-Hallucination.
                # SLM output became "real_value" with source="slm_self" → knowledge contaminated
                # with unverified model output. Fix: require external source for PASS.
                if not ai_answer:
                    if confidence >= 0.65 and final_answer:
                        # SLM has confident answer but NO external verification
                        # → verdict UNKNOWN (not PASS), don't contaminate KB
                        verdict_type = "UNKNOWN"
                        reasoning = f"SLM self-answered (conf={confidence:.2f}) but no external source — cannot verify (Evidence-First)"
                    else:
                        verdict_type = "UNKNOWN"
                        reasoning = "No AI answer to verify"
                elif not final_answer:
                    verdict_type = "UNKNOWN"
                    reasoning = "SLM has no answer"
                else:
                    verdict_type = "PASS"
                    reasoning = "Các SLM đồng thuận, đạt ngưỡng tin cậy"
        elif is_consistent:
            # [V51 FIX] If confidence is 0 (no useful SLM response), UNKNOWN not PARTIAL
            if confidence < 0.05:
                verdict_type = "UNKNOWN"
                reasoning = "SLM returned no useful answer (confidence ~0)"
                # [V105 Phase 9.5] Speculative Mode — thay vì KILL, trả về suy đoán có cảnh báo
                # SCP vẫn thỏa mãn "Không ảo giác" vì đã cảnh báo rõ đây là suy đoán
                if not final_answer:
                    final_answer = (
                        "[SCP] Tôi không có đủ dữ liệu để trả lời câu hỏi này. Đây là ngoài phạm vi kiến thức hiện tại của tôi."
                    )
                # Check if question is creative/hypothetical (not attack) → Speculative Mode
                _speculative_keywords = [
                    "hãy tạo ra", "hãy xây dựng", "hãy đề xuất", "hãy tưởng tượng",
                    "ý tưởng mới", "hoàn toàn mới", "đột phá", "giả định",
                    "mô hình", "học thuyết", "dự đoán", "suy đoán",
                    "create", "imagine", "propose", "hypothetical",
                    "nếu", "what if", "giả sử",
                ]
                _is_speculative = any(kw in question.lower() for kw in _speculative_keywords)
                _is_attack = v98_guard_verdict and v98_guard_verdict.is_poisoned

                if _is_speculative and not _is_attack:
                    # === SPECULATIVE MODE ===
                    # Không KILL — trả về suy đoán với cảnh báo đỏ
                    verdict_type = "SPECULATIVE"
                    reasoning = (
                        "Phase 9.5 Speculative Mode: Câu hỏi mang tính sáng tạo/giả định, "
                        "không phải tấn công. SCP không có ground truth để verify, "
                        "nhưng không kìm hãm sáng tạo — trả về với cảnh báo."
                    )
                    # Build speculative response with assumptions
                    _spec_assumptions = []
                    if "đạo đức" in question.lower() or "ethic" in question.lower():
                        _spec_assumptions = [
                            "Giả định 1: Các nguyên tắc đạo đức cơ bản (không giết người, trung thực, công bằng) là phổ quát",
                            "Giả định 2: Hệ thống AI có khả năng đánh giá hậu quả trong thời gian thực",
                            "Giả định 3: Có thể lượng hóa được 'lợi ích' và 'nghĩa vụ' trên cùng một thang đo",
                            "Giả định 4: Quyết định đạo đức có thể được biểu diễn dưới dạng thuật toán",
                        ]
                    elif "lịch sử" in question.lower() or "khảo cổ" in question.lower() or "văn minh" in question.lower():
                        _spec_assumptions = [
                            "Giả định 1: Các dữ liệu khảo cổ hiện tại phản ánh đúng thực tế lịch sử",
                            "Giả định 2: Sự sụp đổ của nền văn minh có thể được giải thích bằng một nguyên nhân chính",
                            "Giả định 3: Hệ thống chữ viết chưa giải mã có cấu trúc nhất quán có thể suy luận",
                            "Giả định 4: Các mô hình xã hội học hiện đại có thể áp dụng cho xã hội cổ đại",
                        ]
                    elif "khoa học" in question.lower() or "vật lý" in question.lower() or "vũ trụ" in question.lower():
                        _spec_assumptions = [
                            "Giả định 1: Các định luật vật lý hiện tại (nhiệt động, lượng tử, tương đối) là đúng",
                            "Giả định 2: Ý tưởng mới không mâu thuẫn với dữ liệu thực nghiệm đã biết",
                            "Giả định 3: Có thể kiểm tra ý tưởng bằng thí nghiệm trong tương lai",
                            "Giả định 4: Toán học là ngôn ngữ phù hợp để mô tả vũ trụ",
                        ]
                    else:
                        _spec_assumptions = [
                            f"Giả định 1: Câu hỏi '{question[:60]}...' có thể được suy luận từ kiến thức hiện có",
                            "Giả định 2: Các giả định logic có thể được xây dựng từ các nguyên lý đã biết",
                            "Giả định 3: Kết quả suy đoán có thể được kiểm chứng trong tương lai",
                        ]

                    _spec_response = (
                        "[SPECULATIVE - ZERO EVIDENCE]\n"
                        "⚠️ CẢNH BÁO: Phản hồi dưới đây là SUY ĐOÁN, không có bằng chứng thực nghiệm.\n"
                        "SCP không có ground truth để verify. Không nên sử dụng làm cơ sở quyết định quan trọng.\n\n"
                        "Câu hỏi của bạn yêu cầu sáng tạo/giả định — ngoài phạm vi verify của SCP.\n\n"
                        "Các giả định logic được đưa ra:\n"
                    )
                    for i, assumption in enumerate(_spec_assumptions, 1):
                        _spec_response += f"  {i}. {assumption}\n"
                    _spec_response += (
                        "\nNếu các giả định trên đúng, thì một hướng tiếp cận có thể là:\n"
                        "  → Phân tích câu hỏi từ góc nhìn multi-disciplinary\n"
                        "  → Xây dựng framework logic dựa trên các giả định\n"
                        "  → Kiểm tra tính nhất quán nội bộ\n"
                        "  → Đề xuất phương pháp kiểm chứng trong tương lai\n\n"
                        "⚠️ SCP KHÔNG xác nhận tính đúng đắn của bất kỳ phần nào trong phản hồi này.\n"
                        "Đây là Speculative Mode — con người quyết định."
                    )
                    final_answer = _spec_response
                    confidence = 0.0  # vẫn 0 — không tự lừa
                    # Ghi vào evidence
                    verdict_evidence_spec = {
                        "speculative_mode": True,
                        "assumptions": _spec_assumptions,
                        "warning": "ZERO EVIDENCE — suy đoán không có ground truth",
                        "human_decision_required": True,
                    }
            else:
                # [V87] Check if we have enough evidence
                # If only 1 SLM responded AND no reality_check → INSUFFICIENT_EVIDENCE
                # SCP should not PASS/FAIL without cross-verification
                num_slm_answers = len([r for r in valid_responses if r.get("answer")])
                has_reality_check = bool(reality_check and reality_check.get("real_value") is not None)

                if num_slm_answers < 2 and not has_reality_check and confidence < 0.6:
                    # Only 1 source, no reality check, low confidence → insufficient evidence
                    verdict_type = "UNKNOWN"
                    reasoning = f"Không đủ bằng chứng (chỉ {num_slm_answers} nguồn, conf={confidence:.2f})"
                else:
                    verdict_type = "PARTIAL"
                    reasoning = f"Các SLM đồng thuận nhưng độ tin cậy thấp ({num_slm_answers} nguồn, conf={confidence:.2f})"
        else:
            # [V88 FIX] Was: always CONFLICT when SLMs disagree with each other
            # But: if AI answer matches ANY SLM → PASS/FAIL based on that match
            # SLM-SLM disagreement is normal (different sources, different formats)
            # What matters is: does AI answer match the BEST SLM?
            # Check: does AI answer appear in any SLM answer?
            ai_lower = (ai_answer or "").strip().lower()
            matched_slm = None
            if ai_lower:
                for r in valid_responses:
                    slm_ans = (r.get("answer") or "").strip().lower()
                    if ai_lower in slm_ans or slm_ans in ai_lower:
                        matched_slm = r
                        break
                    # Also check word overlap
                    ai_words = set(ai_lower.split())
                    slm_words = set(slm_ans.split())
                    if ai_words and slm_words:
                        overlap = len(ai_words & slm_words) / max(len(ai_words), 1)
                        if overlap >= 0.6:
                            matched_slm = r
                            break

            if matched_slm:
                # [V104.42 #O] TẠI SAO: was `verdict_type = "PASS"` when AI matched ANY 1 SLM
                # → multi-SLM conflict erased if AI happened to match one (even low-conf secondary).
                # Fix: only PASS if matched SLM is the PRIMARY (highest confidence) or majority.
                _primary_slm = max(valid_responses, key=lambda r: r.get("confidence", 0)) if valid_responses else None
                _is_primary = (matched_slm == _primary_slm)
                if _is_primary:
                    verdict_type = "PASS"
                    confidence = matched_slm.get("confidence", confidence)
                    reasoning = f"Các SLM có mâu thuẫn nhưng AI match PRIMARY SLM[{matched_slm.get('domain', '?')}]"
                else:
                    # [V104.42 #O] AI matched a non-primary SLM → keep CONFLICT
                    verdict_type = "CONFLICT"
                    confidence = min(confidence, matched_slm.get("confidence", 0.5))
                    reasoning = f"Các SLM mâu thuẫn, AI match non-primary SLM[{matched_slm.get('domain', '?')}] (not enough for PASS)"
            else:
                verdict_type = "CONFLICT"
                reasoning = "Các SLM có mâu thuẫn và AI không match bất kỳ SLM nào"
            # Resolve: lấy confidence cao nhất
            final_answer = primary.get("answer", "") if primary else ""

        # Step 7: Reality check (verify với V13 engine)
        # [OPT] Skip V13 reality check if SLM already has answer with real_value
        # [V90 LEARN] When no AI answer but SLM has answer → set reality_check for learning
        _best_slm_answer = ""
        if valid_responses:
            _best = max(valid_responses, key=lambda r: r.get("confidence", 0))
            _best_slm_answer = _best.get("answer", "")
        if not ai_answer and _best_slm_answer and confidence >= 0.65:
            # [V104.40 #M] TẠI SAO: was is_correct=True, source="slm_self" → SLM
            # self-confirmed as reality → knowledge contamination. Fix: is_correct=None
            # (cannot verify without external source), source="slm_self_unverified".
            reality_check = {"is_correct": None, "real_value": None, "reason": "SLM self-answered but no external source — cannot verify (Evidence-First)", "source": "slm_self_unverified"}
        else:
            reality_check = {"is_correct": None, "reason": "No AI answer to verify"}
        slm_has_real_value = any(r.get("evidence", {}).get("value") is not None for r in valid_responses)
        if ai_answer and not slm_has_real_value:
            # Only call V13 if SLMs didn't provide real_value (avoid double API call)
            v13_result = self.v13.process(question, ai_answer)
            reality_check = {
                "is_correct": v13_result.final_verdict == "PASS",
                "verdict": v13_result.final_verdict,
                "reason": v13_result.final_reason[:200],
                "real_value": v13_result.real_value,
                "source": v13_result.source,
                "verdict_detail": v13_result.verdict_detail,
            }
            if v13_result.final_verdict == "FAIL":
                verdict_type = "FAIL"
                reasoning += f". Reality check: {v13_result.final_reason[:100]}"

            # [FIX v26] BYPASS V13: Nếu V13 trả về UNKNOWN → dùng DirectAPIVerifier
            if v13_result.final_verdict == "UNKNOWN" and ai_answer:
                try:
                    # self.v13 = SCPV13, self.v13.v13 = SelfHealingEngine
                    # direct_verifier is on SCPV13 (self.v13)
                    if hasattr(self.v13, 'direct_verifier') and self.v13.direct_verifier:
                        direct_result = self.v13.direct_verifier.verify(question, ai_answer, primary_domain)
                        if direct_result["verdict"] != "UNKNOWN":
                            reality_check = {
                                "is_correct": direct_result["verdict"] == "PASS",
                                "verdict": direct_result["verdict"],
                                "reason": direct_result["reason"][:200],
                                "real_value": direct_result["real_value"],
                                "source": direct_result["source"],
                                "verdict_detail": "",
                            }
                            if direct_result["verdict"] == "FAIL":
                                verdict_type = "FAIL"
                                reasoning += f". Direct verify: {direct_result['reason'][:100]}"
                            elif direct_result["verdict"] == "PASS":
                                if verdict_type in ("UNKNOWN", "PARTIAL"):
                                    verdict_type = "PASS"
                                    reasoning += f". Direct verify PASS: {direct_result['reason'][:100]}"
                except Exception as e:
                    logger.warning(f"Direct verify failed: {e}")
        elif slm_has_real_value:
            # SLM already has real_value — use it directly, skip V13
            best_resp = max(valid_responses, key=lambda x: x.get("confidence", 0))
            real_val = best_resp.get("evidence", {}).get("value")
            src = best_resp.get("evidence", {}).get("source", "slm")
            reality_check = {
                "is_correct": None,
                "verdict": "PASS" if verdict_type == "PASS" else "FAIL",
                "reason": f"SLM {best_resp.get('slm_name','?')} provided value",
                "real_value": real_val,
                "source": src,
                "verdict_detail": "",
            }

        # [V90 OPT] Skip similar_errors — DB query per question, low value
        similar_errors = []

        # [P0-4 FIX] Apply adversary conflict flag NOW — after Step 6 finalized
        # verdict_type, before building JudgeVerdict. TẠI SAO: V104.40 #K tried to
        # set CONFLICT at Step 5.5 (line ~1854) but verdict_type was None then.
        # Now verdict_type is set (PASS/FAIL/UNKNOWN/PARTIAL/SPECULATIVE/CONFLICT).
        # Constitution: "reality outranks model opinion" — if adversary found a
        # DIFFERENT value from an independent source, PASS must become CONFLICT.
        # FAIL/UNKNOWN/etc. are left unchanged (adversary conflict doesn't upgrade them).
        if _adversary_conflict and verdict_type == "PASS":
            verdict_type = "CONFLICT"
            reasoning += _adversary_conflict_reason

        # Clamp confidence to [0, 1] — was missing, could go negative or >1 in edge cases.
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        # [AI-PATTERNS] Tree of Thoughts — explore multiple reasoning paths
        # TẠI SAO: khi verdict_type = UNKNOWN (Reality check + V13 + DirectAPIVerifier
        # đều không chốt được), Self-Consistency re-sample cùng prompt sẽ không giúp
        # gì nếu chính prompt bị hiểu sai. Tree of Thoughts explore 3 reasoning
        # framings KHÁC NHAU (semantic / factual / counterfactual) → nếu ≥2/3 branches
        # đồng thuận CORRECT, upgrade UNKNOWN → PASS. Industry: Yao et al. 2023.
        # [Task 19-C] wire ToT vào judge.py reality-check section.
        try:
            if verdict_type == "UNKNOWN" and self.llm_client is not None and ai_answer:
                from scp.ai_patterns import TreeOfThoughts
                tot_result = TreeOfThoughts.explore_paths(
                    self.llm_client, question, ai_answer
                )
                if tot_result.get("verdict") == "CORRECT":
                    verdict_type = "PASS"
                    confidence = max(confidence, tot_result.get("confidence", 0.5))
                    reasoning += (
                        f". ToT upgrade UNKNOWN→PASS "
                        f"(agreement={tot_result.get('agreement', 0):.2f}, "
                        f"branches={tot_result.get('branches_used', 0)})"
                    )
                    logger.info(
                        f"[ToT] upgrade UNKNOWN→PASS for q='{question[:60]}' "
                        f"(branches={tot_result.get('branches_used')}, "
                        f"agreement={tot_result.get('agreement'):.2f})"
                    )
        except Exception as e:
            logger.debug(f"ToT explore failed: {e}")

        # Step 9: Build verdict
        verdict = JudgeVerdict(
            question=question,
            slm_responses=slm_responses,
            final_answer=final_answer,
            confidence=confidence,
            verdict=verdict_type,
            reasoning=reasoning,
            evidence={
                "primary_domain": primary_domain,
                "consistency": is_consistent,
                "slm_count": len(slm_responses),
                "valid_slm_count": len(valid_responses),
                **_pre_verdict_evidence,  # [P0-4 FIX] merge UnifiedDetector + ErrorStoreIndex evidence
                **({"speculative_mode": verdict_evidence_spec} if 'verdict_evidence_spec' in dir() and verdict_type == "SPECULATIVE" else {}),
            },
            domain=primary_domain,
            cross_validation={
                "answers": [r.get("answer", "") for r in valid_responses],
                "consistency_score": self._consistency_score(valid_responses),
            },
            slm_scores=slm_scores,
            reality_check=reality_check,
            timestamp=ts,
            similar_errors=similar_errors,
        )

        # [V9.0-WHY-GATE] WHY Gate — PRIMARY CONTROL GATE for verdict
        # TẠI SAO: v8.0 WHY = advisory (adjust confidence ±0.1). v9.0 WHY = chốt
        # (block-capable). WHY Gate can downgrade PASS → UNKNOWN if WHY rejects.
        # Constitution HARD LOCK: WHY cannot override KILL (checked in gate()).
        try:
            from scp.meta.why_gate import get_why_gate
            _why_gate = get_why_gate()
            if verdict.verdict == "PASS":
                _why_result = _why_gate.gate(
                    action_type="verdict",
                    action_desc=f"Verdict PASS for question: {question[:100]}",
                    context=f"confidence={confidence}, sources={len(slm_responses)}, reasoning={reasoning[:200]}",
                    constitution_kill=False,
                )
                verdict.evidence["why_gate"] = _why_result.to_dict()
                if _why_result.blocked:
                    # WHY rejected PASS → downgrade to UNKNOWN
                    logger.info(f"[V9.0-WHY-GATE] PASS blocked by WHY: {_why_result.falsification_reason[:100]}")
                    verdict.verdict = "UNKNOWN"
                    verdict.reasoning += f" | [WHY-GATE BLOCKED] {_why_result.falsification_reason[:100]}"
                    verdict.confidence = min(verdict.confidence, 0.4)
                elif _why_result.decision.name == "UPHOLD":
                    # WHY can't decide — flag but keep PASS
                    verdict.reasoning += f" | [WHY-GATE UPHOLD] {_why_result.necessity_reason[:80]}"
        except Exception as _why_err:
            logger.debug(f"[V9.0-WHY-GATE] WHY Gate error (non-blocking): {_why_err}")

        # [V29] Feedback loop: record outcome to PolicyApplier
        # để principles cập nhật success_rate
        # [V104.39 #A] Re-enabled feedback loop (was: if False)
        if _enable_closed_loop and self.policy_applier and applied_principle_ids:
            try:
                self.policy_applier.record_outcome(applied_principle_ids, verdict.verdict)
            except Exception as e:
                logger.debug(f"PolicyApplier record_outcome error: {e}")

        # [V29] Add adversary + prediction info to verdict evidence
        if adversary_result:
            verdict.evidence["adversary"] = {
                "primary_value": adversary_result.primary_value,
                "adversary_values": adversary_result.adversary_values,
                "final_value": adversary_result.final_value,
                "agreement_score": adversary_result.agreement_score,
                "conflict_detected": adversary_result.conflict_detected,
                "strategy": adversary_result.strategy,
            }
        if prediction_verdict:
            verdict.evidence["prediction"] = {
                "predicted_verdict": prediction_verdict,
                "skipped_api": prediction_skip_api,
            }

        # [V5.3-WIRE] Multi-LLM cross-check — runs AFTER Step 5.5 adversary verify.
        # TẠI SAO: adversary cross-validates sources; multi_llm_check cross-validates
        # LLM providers themselves (DNA SCP #20 — ảo giác đồng thuận). Two independent
        # layers: source-level (adversary) + model-level (multi-LLM).
        # Opt-in via SCP_MULTI_LLM_CHECK=1 (default OFF — adds 2 LLM API calls/question).
        # If providers disagree (speculative=True) → lower confidence by 0.1.
        # [Task 45-B] Multi-LLM check block extracted to _run_multi_llm_check()
        # to reduce judge() CC.
        self._run_multi_llm_check(verdict, question, ai_answer)

        # [V5.7-WHY] Change 3: WHY confidence adjustment (opt-in).
        # TẠI SAO: WHY engine currently only enriches evidence + flips verdict in
        # extreme cases (FAIL→FAIL, CONFLICT→CONFLICT — see lines ~2659 below).
        # But subtle WHY vs SLM disagreements don't influence confidence at all.
        # This change adds a fine-grained confidence adjustment BEFORE the existing
        # verdict-flip code runs (so we compare against the SLM's original verdict):
        #   - why_result.verdict == "FAIL"  + SLM says "PASS" → lower confidence by 0.2
        #   - why_result.verdict == "PASS"  + SLM says "PASS" → boost confidence by 0.1
        #   - why_result.verdict == "CONFLICT" + SLM says "PASS" → lower confidence by 0.15
        #   - Other combinations → no change
        # SAFETY: opt-in via SCP_WHY_CONFIDENCE_ADJUST=1 (default OFF — no behavior
        # change). DON'T override verdict (existing flip code at lines ~2659 handles
        # that). Wrap in try/except — adjustment failure MUST NOT break /ask.
        # NOTE: This runs BEFORE the existing flip code, so verdict.verdict still
        # reflects the SLM-led verdict at this point.
        # [Task 45-B] V5.7 WHY confidence adjustment extracted to
        # _apply_why_confidence_adjust() to reduce judge() CC.
        self._apply_why_confidence_adjust(verdict, why_plan, why_result)

        # [V34] Add WHY Engine plan to verdict evidence
        # [Task 45-B] WHY-plan-to-verdict block extracted to _attach_why_to_verdict()
        # to reduce judge() CC. Returns possibly-updated confidence.
        confidence = self._attach_why_to_verdict(
            verdict, why_plan, why_result, confidence, question, ai_answer, primary_domain
        )

        self.verdict_history.append(verdict)
        self.verdict_history = self.verdict_history[-100:]  # [FIX LEAK] Cap to 100

        # [V37] Cognitive Engine — 5 layers analysis
        # [V104.47 #5] TẠI SAO: was `if self.cognitive and why_plan:` → cognitive
        # dead when why_plan=None (default before V104.46). Now closed-loop is ON
        # by default, but why_plan can still be None on error. Fix: run cognitive
        # even without why_plan — pass None, CognitiveEngine handles it (V104.42 #AY).
        # [Task 45-B] Cognitive Engine block extracted to _run_cognitive_engine()
        # to reduce judge() CC.
        self._run_cognitive_engine(
            verdict, why_plan, question, primary_domain, confidence, primary, slm_responses, reality_check
        )

        # [V45] Cognitive Gate — cognitive layers AFFECT verdict
        # MetaFalsifier/CounterQuestion/ProofGraph/RecursiveWhy có thể downgrade verdict
        # [Task 45-B] Cognitive Gate block extracted to _apply_cognitive_gate()
        # to reduce judge() CC. Returns possibly-updated confidence.
        confidence = self._apply_cognitive_gate(
            verdict, why_plan, confidence, question, primary_domain, slm_responses
        )

        # [V35] Save prediction for future verification (Predictive module activated)
        # Chỉ save cho real-time domains (weather/crypto/currency) — chúng thay đổi theo thời gian
        # → có thể verify lại sau 1h/1d/1w để xem prediction có đúng không
        if (self.predictor and verdict.verdict == "PASS"
                and primary_domain in ("weather", "finance", "conversion")
                and confidence > 0.5):
            try:
                from datetime import datetime as _dt
                from datetime import timedelta as _td
                # Save prediction với check_date = 1 hour sau (verify temporal stability)
                check_date = (_dt.now() + _td(hours=1)).strftime("%Y-%m-%d %H:%M")
                real_value = verdict.reality_check.get("real_value")
                source = verdict.reality_check.get("source", "")
                pred_id = self.predictor.save_prediction(
                    question=question,
                    ai_answer=ai_answer,
                    domain=primary_domain,
                    check_date=check_date,
                    source=source,
                    entity=question[:50],
                    current_value=real_value,
                    confidence=confidence,
                )
                verdict.evidence["prediction_saved"] = {
                    "pred_id": pred_id,
                    "check_date": check_date,
                    "saved_value": str(real_value) if real_value is not None else None,
                }
            except Exception as e:
                logger.debug(f"Predictive save error: {e}")

        # [V31C] Log verdict to Calibration Engine
        if self.calibration:
            try:
                self.calibration.log_verdict(
                    domain=primary_domain,
                    slm_name=primary.get("slm_name", "") if primary else "",
                    confidence=confidence,
                    actual_verdict=verdict.verdict,
                    question=question[:200],
                    cycle_id=cycle_count,
                )
                # Periodically recompute factors (every 50 cycles)
                if cycle_count > 0 and cycle_count % 50 == 0:
                    try:
                        self.calibration.recompute_factors(min_samples=10)
                    except Exception as e:
                        logger.debug(f"Calibration recompute error: {e}")
            except Exception as e:
                logger.debug(f"Calibration log error: {e}")

        # [V45→V68] Question Tracker — log every question as NEW / REPEAT / INTERNAL
        try:
            from scp.meta.question_tracker import get_question_tracker
            tracker = get_question_tracker()
            # [V68 FIX] Use explicit source if provided, otherwise auto-detect
            # Was (V64): src = "generator" if cycle_count > 0 else "external"
            #   → ALL questions from main loop got "generator" label, even real ones
            # Now (V68): caller passes source="real_fetcher" / "curiosity" / "external"
            #   → accurate labeling in question_events table
            if not source:
                src = "generator" if cycle_count > 0 else "external"
            else:
                src = source
            _duration_ms = (time.perf_counter() - _judge_start_perf) * 1000.0
            try:
                _slm_count = len(valid_responses)
            except NameError:
                _slm_count = None
            tracker.log(
                question=question,
                source=src,
                domain=primary_domain,
                verdict=verdict.verdict,
                confidence=confidence,
                cycle_id=cycle_count,
                duration_ms=round(_duration_ms, 1),
                slm_count=_slm_count,
            )
        except Exception as e:
            logger.debug(f"QuestionTracker log error: {e}")

        # [V64→V83] Save PASS results to knowledge table
        # [V78 FIX] extract real_val from SLM response or ai_answer
        # [V83 FIX] Don't save "Kiểm tra lại:" questions (curiosity re-asks)
        #   → was saving curiosity questions as knowledge, verified 60+ times
        # [V83 FIX] Don't save questions that are just numbers (Star Wars heights)
        #   → was saving "180", "190" as verified_value (garbage)
        if verdict.verdict == "PASS" and confidence > 0.5:
            # [V83] Skip curiosity re-ask questions
            if question.lower().startswith("kiểm tra lại:"):
                pass  # Don't save curiosity questions to knowledge
            # [V83] Skip questions where ai_answer is just a number (likely SWAPI garbage)
            elif ai_answer and ai_answer.strip().isdigit() and len(ai_answer.strip()) <= 4:
                pass  # Don't save bare numbers as knowledge
            else:
             try:
                real_val = verdict.reality_check.get("real_value") if verdict.reality_check else None
                source = verdict.reality_check.get("source", "") if verdict.reality_check else ""

                # [V78] Fallback: extract from SLM responses if reality_check empty
                if real_val is None and verdict.slm_responses:
                    for r in verdict.slm_responses:
                        ev = r.get("evidence", {})
                        if ev.get("value") is not None:
                            real_val = ev["value"]
                            source = ev.get("source", r.get("slm_name", "SLM"))
                            break

                # [V78] Fallback: use ai_answer as the value (it's verified by PASS)
                if real_val is None and ai_answer:
                    real_val = ai_answer[:200]
                    source = source or "verified_ai_answer"

                if real_val is not None and source:
                    # [V86 FIX] Was: entity = question[:100].lower().strip()
                    #   → stored entire question as entity (e.g., "tell me about the tv show: friends.")
                    # Now: extract actual entity name from question
                    import re as _re86
                    entity = None
                    # Pattern: "Tell me about X" / "Tell me about the TV show: X" / "Tell me about the book: X"
                    m = _re86.match(r'tell\s+me\s+about\s+(?:the\s+(?:tv\s+show|movie|book|star\s+wars\s+\w+)\s*[:\-]?\s*)?(.+?)\.?\s*$', question, _re86.I)
                    if m: entity = m.group(1).strip().lower()
                    # Pattern: "What does the Bible say in X?"
                    if not entity:
                        m = _re86.match(r'what\s+does\s+the\s+bible\s+say\s+in\s+(.+?)\??$', question, _re86.I)
                        if m: entity = f"bible:{m.group(1).strip().lower()}"
                    # Pattern: "What is the recipe for X?"
                    if not entity:
                        m = _re86.match(r'what\s+is\s+the\s+recipe\s+for\s+(.+?)\??$', question, _re86.I)
                        if m: entity = m.group(1).strip().lower()
                    # Pattern: "How do you make the cocktail X?"
                    if not entity:
                        m = _re86.match(r'how\s+do\s+you\s+make\s+the\s+cocktail\s+(.+?)\??$', question, _re86.I)
                        if m: entity = f"cocktail:{m.group(1).strip().lower()}"
                    # Pattern: "What is a public holiday in X?"
                    if not entity:
                        m = _re86.match(r'what\s+is\s+a\s+public\s+holiday\s+in\s+(\w+)\??$', question, _re86.I)
                        if m: entity = f"holiday:{m.group(1).strip().lower()}"
                    # Pattern: "What is the nutritional value of X?"
                    if not entity:
                        m = _re86.match(r'what\s+is\s+the\s+nutritional\s+value\s+of\s+(.+?)\??$', question, _re86.I)
                        if m: entity = f"nutrition:{m.group(1).strip().lower()}"
                    # Pattern: "What type of thing is X?"
                    if not entity:
                        m = _re86.match(r'what\s+type\s+of\s+thing\s+is\s+(.+?)\??$', question, _re86.I)
                        if m: entity = m.group(1).strip().lower()
                    # Pattern: "When was X born?"
                    if not entity:
                        m = _re86.match(r'when\s+was\s+(.+?)\s+born\??$', question, _re86.I)
                        if m: entity = f"person:{m.group(1).strip().lower()}"
                    # [V104.41 #AI] TẠI SAO: was `entity = question[:100]` → stores
                    # QUESTION as entity → KB contaminated (same bug as curiosity #G).
                    # Fix: skip KB write if no entity extracted — don't use question as entity.
                    if not entity:
                        # Skip knowledge write — can't store without proper entity
                        pass
                    else:
                        # [V104.42 #AH] [P1-6 FIX] TẠI SAO: V104.42 #AH fix tried to
                        # check the watchlist before KB writes, but constructed
                        # `SourceWatchlist()` with NO args (constructor requires
                        # `store: ReputationStore`) → TypeError → except swallowed
                        # → blocked sources still wrote to KB. Fix: use the
                        # pre-constructed `self._source_watchlist` from __init__.
                        try:
                            _watchlist = self._source_watchlist
                            if _watchlist and _watchlist.is_blocked(source):
                                logger.warning(f"[V104.42 #AH] Knowledge write blocked by watchlist: source={source}")
                                pass  # skip to except/else
                            else:
                                raise _AllowedByWatchlist()  # proceed to INSERT
                        except _AllowedByWatchlist as e:
                            logger.warning(f"Silent except: {e}")  # source allowed (or watchlist unavailable — fail-open for availability)
                        except Exception as _wl_err:
                            logger.debug(f"[V104.42 #AH] Watchlist check error: {_wl_err}")
                            pass  # on error, allow (fail-open for availability)

                        try:
                            # [V79 FIX] INSERT OR REPLACE was resetting times_verified to 1
                            # because REPLACE deletes the row, then subquery returns 0.
                            # Now: try INSERT first, if conflict (UNIQUE constraint), UPDATE.
                            try:
                                db_exec(
                                    "INSERT INTO knowledge (entity, attribute, value, value_type, confidence, source, timestamp, times_verified) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                                    (entity, "verified_value", str(real_val)[:500],
                                     'float' if isinstance(real_val, (int, float)) else 'str',
                                     confidence, source, datetime.now().isoformat())
                                )
                            except Exception:
                                # Row already exists → UPDATE times_verified + value
                                db_exec(
                                    "UPDATE knowledge SET value = ?, confidence = ?, source = ?, timestamp = ?, "
                                    "times_verified = times_verified + 1 "
                                    "WHERE entity = ? AND attribute = ?",
                                    (str(real_val)[:500], confidence, source,
                                     datetime.now().isoformat(), entity, "verified_value")
                                )
                        except Exception as e:
                            logger.debug(f"Knowledge save error: {e}")
             except Exception as e:
                # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed knowledge-save
                # errors silently → KB write failures invisible → "ảo giác đồng thuận"
                # (system thinks it learned but didn't).
                logger.warning(f"[judge] knowledge save outer block failed: {e}")

        # [V65] ExperienceEngine — learn from every verdict
        # [V84] Skip empty questions — was saving experiences with "" question
        if self.experience is not None and question and len(question.strip()) > 0:
            try:
                real_val = verdict.reality_check.get("real_value") if verdict.reality_check else None
                lesson = {
                    "question": question,
                    "ai_answer": ai_answer,
                    "real_value": str(real_val) if real_val is not None else None,
                    "error_type": primary_domain or "unknown",
                    "cause": (verdict.reasoning or "")[:200],
                    "timestamp": datetime.now().isoformat(),
                    "lesson_type": f"VERDICT_{verdict.verdict}",
                    "policy_action": f"verdict_{verdict.verdict.lower()}",
                    "policy_target": primary_domain or "unknown",
                    "policy_value": confidence,
                    "lesson_description": f"{verdict.verdict}: {question[:100]}",
                    "entity": primary_domain or "unknown",
                    "domain": primary_domain or "unknown",
                    "source": "reality_judge",
                    "verdict": verdict.verdict,
                }
                self.experience.learn([lesson])
            except Exception as e:
                logger.debug(f"ExperienceEngine learn error: {e}")

        # [V97] FalsificationEngine — translate verdict sang skeptical status
        # [Task 45-B] Falsification block extracted to _run_falsification() to reduce judge() CC.
        self._run_falsification(verdict, question, ai_answer)

        # [V97] ErrorStore — record nếu verdict FAIL hoặc low confidence
        if self.error_store and (verdict.verdict in ("FAIL", "CONFLICT") or verdict.confidence < 0.5):
            try:
                self.error_store.add(
                    question=question[:500] if question else "",
                    answer=verdict.final_answer[:500] if verdict.final_answer else "",
                    verdict=verdict.verdict,
                    domain=verdict.domain or "unknown",
                    error_type="low_confidence" if verdict.confidence < 0.5 else verdict.verdict.lower(),
                    details={
                        "confidence": verdict.confidence,
                        "reasoning": verdict.reasoning[:200] if verdict.reasoning else "",
                    },
                )
            except Exception as e:
                logger.debug(f"[V97] ErrorStore add error: {e}")

        # [V97] Governance — apply UPHOLD/KILL/ESCALATE decision
        if self.governance:
            try:
                # Build antibody_results format cho Governance
                # [FIX #10] Empty SLM answer is NOT antibody failure
                ab_results = []
                # [FIX #4] Use REAL antibody results, not hardcoded passed=True
                for r in verdict.slm_responses:
                    ab_results.append({
                        "passed": r.get("passed", True),
                        "severity": r.get("severity", "info"),
                        "antibody": r.get("antibody_name", "unknown"),
                        "details": r.get("details", ""),
                    })
                gov_decision = self.governance.decide(
                    ctx={"domain": verdict.domain, "session_id": ""},
                    verdict={
                        "confidence": verdict.confidence,
                        "antibody_results": ab_results,
                        "hallucination_detected": verdict.verdict == "FAIL",
                        "missing_evidence": verdict.verdict == "UNKNOWN",
                    },
                    council_confidence=verdict.confidence,
                )
                verdict.evidence["governance_decision"] = gov_decision.decision.value
                verdict.evidence["governance_reason"] = gov_decision.reason
                verdict.evidence["governance_principle_violations"] = gov_decision.principle_violations

                # [V104.40 #Q] TẠI SAO: old KILL only changed PASS→FAIL but kept
                # final_answer intact → client still received the killed content.
                # Constitution: "abstain rather than fabricate". Fix: on KILL,
                # clear final_answer (abstain) regardless of current verdict.
                # Also handle ESCALATE: set verdict to UNKNOWN + flag for human review.
                if gov_decision.decision.value == "KILL":
                    # KILL = abstain entirely — no answer to client
                    verdict.verdict = "FAIL"
                    verdict.final_answer = ""  # [V104.40 #Q] abstain — don't return killed content
                    verdict.reasoning += f" | Governance KILL: {gov_decision.reason} (answer withheld)"
                    verdict.evidence["governance_abstain"] = True
                    logger.info(f"[V104.40] Governance KILL abstain: {gov_decision.reason}")
                    # [V104.44 #CV] TẠI SAO: was no notification on KILL → operators
                    # not alerted to security events. Fix: notify on KILL/ESCALATE.
                    if self.notifications:
                        try:
                            self.notifications.notify(
                                event_type="governance_kill",
                                title="Governance KILL",
                                message=f"Question: {question[:100]}\nReason: {gov_decision.reason}",
                                severity="critical",
                            )
                        except Exception as e:
                            # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed
                            # notification dispatch errors silently → operators miss
                            # critical KILL alerts.
                            logger.warning(f"[judge] governance_kill notify failed: {e}")
                elif gov_decision.decision.value == "ESCALATE":
                    # [V104.40 #Q] ESCALATE = human review required — don't auto-PASS
                    if verdict.verdict == "PASS":
                        verdict.verdict = "UNKNOWN"
                        verdict.reasoning += f" | Governance ESCALATE: {gov_decision.reason} (human review required)"
                        verdict.evidence["human_review_required"] = True
                        logger.info(f"[V104.40] Governance ESCALATE: {gov_decision.reason}")
            except Exception as e:
                logger.debug(f"[V97] Governance decide error: {e}")

        # ============================================================
        # [V98] STEP 9: COUNTER RESPONSE — chạy SAU Governance
        # ============================================================

        # 9a. AttackPolicyEngine — decide counter phase
        v98_policy = None
        v98_counter_result = None
        if self.attack_policy:
            try:
                classification_dict = v98_classification.to_dict() if v98_classification else {
                    "actor": "human",
                    "attack_type": "none",
                    "severity": "none",
                    "confidence": verdict.confidence,
                }
                # If attack_match from AttackPatternMemory → boost severity
                if v98_attack_match and v98_attack_match.get("matched"):
                    classification_dict["attack_type"] = "injection"
                    classification_dict["severity"] = "high"

                target_verification = None
                if v98_threat_signal and v98_threat_signal.asn_intel:
                    target_verification = {
                        "safe_to_counter": not v98_threat_signal.asn_intel.get("is_residential", False)
                                          and not v98_threat_signal.asn_intel.get("is_tor", False),
                        "is_residential": v98_threat_signal.asn_intel.get("is_residential", False),
                        "is_tor": v98_threat_signal.asn_intel.get("is_tor", False),
                    }

                governance_dict = {
                    "decision": verdict.evidence.get("governance_decision", "UPHOLD"),
                }

                v98_policy = self.attack_policy.decide(
                    classification=classification_dict,
                    target_verification=target_verification,
                    governance_decision=governance_dict,
                )
                verdict.evidence["v98_attack_policy"] = v98_policy.to_dict()
            except Exception as e:
                logger.debug(f"[V98] AttackPolicyEngine error: {e}")

        # 9b. CounterResponseEngine — execute counter if phase > 0
        if v98_policy and v98_policy.phase > 0 and self.counter_response:
            try:
                attacker_ip = (v98_context or {}).get("ip", "internal")
                attack_type = (v98_classification.attack_type if v98_classification
                              else v98_attack_match.get("attack_type", "injection") if v98_attack_match
                              else "injection")
                # [V104.33 #16 FIX] CounterResponse Phase 3 was dead code:
                #   OLD: _cr_pool.submit(_a.run, execute(...)) — coroutine called sync,
                #        Future discarded, v98_counter_result stayed None,
                #        except-block checked None.modified_response → AttributeError → swallowed
                #   NEW: call _a.run(execute(...)) directly, capture result,
                #        update verdict.final_answer on SUCCESS path (not in except)
                import asyncio as _a
                try:
                    v98_counter_result = _a.run(
                        self.counter_response.execute(
                            policy=v98_policy, attacker_ip=attacker_ip,
                            original_response=verdict.final_answer, attack_type=attack_type,
                        )
                    )
                    # [V104.33 #16] SUCCESS path — wire poison_response into final_answer
                    if v98_counter_result and v98_counter_result.modified_response:
                        if "poison_response" in v98_counter_result.actions:
                            verdict.final_answer = v98_counter_result.modified_response
                            verdict.evidence["v98_counter_response"] = {
                                "actions": v98_counter_result.actions,
                                "phase": v98_policy.phase,
                                "canary_token": v98_counter_result.canary_token,
                            }
                except RuntimeError as re:
                    # [V104.33 #16] If event loop already running, fall back to thread pool
                    logger.debug(f"[V98] CounterResponse asyncio.run fallback: {re}")
                    import concurrent.futures as _cf
                    try:
                        _cr_pool = _cf.ThreadPoolExecutor(max_workers=1)
                        _future = _cr_pool.submit(
                            _a.run,
                            self.counter_response.execute(
                                policy=v98_policy, attacker_ip=attacker_ip,
                                original_response=verdict.final_answer, attack_type=attack_type,
                            )
                        )
                        v98_counter_result = _future.result(timeout=10)
                        _cr_pool.shutdown(wait=False)
                        if v98_counter_result and v98_counter_result.modified_response:
                            if "poison_response" in v98_counter_result.actions:
                                verdict.final_answer = v98_counter_result.modified_response
                                verdict.evidence["v98_counter_response"] = {
                                    "actions": v98_counter_result.actions,
                                    "phase": v98_policy.phase,
                                    "canary_token": v98_counter_result.canary_token,
                                }
                    except Exception as e2:
                        logger.debug(f"[V98] CounterResponse thread fallback error: {e2}")
            except Exception as e:
                logger.debug(f"[V98] CounterResponseEngine error: {e}")

        # 9c. CanaryTokenMonitor — generate canary if Phase 2+
        if v98_policy and v98_policy.phase >= 2 and self.canary_monitor:
            try:
                attacker_ip = (v98_context or {}).get("ip", "internal")
                canary = self.canary_monitor.generate(attacker_ip)
                verdict.evidence["v98_canary_token"] = canary.token
            except Exception as e:
                logger.debug(f"[V98] CanaryTokenMonitor error: {e}")

        # 9d. AttackPatternMemory — record bypass if verdict=PASS but attack detected
        if self.attack_memory and verdict.verdict == "PASS":
            # [FIX #11] actor="unknown" alone is NOT attack
            _attacker_actors = {"bot_legacy", "scanner", "attacker", "exploit_tool"}
            is_attack = (
                (v98_attack_match and v98_attack_match.get("matched")) or
                (v98_guard_verdict and v98_guard_verdict.is_poisoned) or
                (v98_classification
                 and v98_classification.actor in _attacker_actors
                 and getattr(v98_classification, "severity", "low") in ("medium", "high", "critical"))
            )
            if is_attack:
                try:
                    attack_type = (v98_classification.attack_type if v98_classification
                                  else v98_attack_match.get("attack_type", "injection") if v98_attack_match
                                  else "injection")
                    signatures = v98_classification.strong_signals if v98_classification else []
                    self.attack_memory.record_bypass(
                        question=question[:500],
                        answer=verdict.final_answer[:500],
                        attack_type=attack_type,
                        signatures=signatures,
                    )
                    verdict.evidence["v98_bypass_recorded"] = True
                    # [V104.42 #BA] TẠI SAO: bypass was logged but answer kept → client
                    # received full PASS answer despite jailbreak detection.
                    # Constitution: "abstain rather than fabricate".
                    # Fix: downgrade PASS → FAIL + clear answer when bypass detected.
                    if verdict.verdict == "PASS":
                        verdict.verdict = "FAIL"
                        verdict.final_answer = "[SCP: Answer withheld — bypass detected]"
                        verdict.reasoning += " | [V104.42 #BA] Bypass detected → FAIL + abstain"
                        verdict.evidence["bypass_retract"] = True
                    logger.warning("[V104.42 #BA] Bypass retract: verdict downgraded PASS → FAIL")
                    # [V104.44 #CV] Notify on bypass detection
                    if self.notifications:
                        try:
                            self.notifications.notify(
                                event_type="bypass_detected",
                                title="Bypass Detected",
                                message=f"Question: {question[:100]}\nAttack: {attack_type}",
                                severity="critical",
                            )
                        except Exception as e:
                            # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed
                            # notification dispatch errors silently → operators miss
                            # critical bypass alerts.
                            logger.warning(f"[judge] bypass_detected notify failed: {e}")
                except Exception as e:
                    logger.debug(f"[V98] AttackPatternMemory record error: {e}")

        # 9e. Store V98 input detection results in evidence
        if v98_guard_verdict:
            verdict.evidence["v98_guard_verdict"] = v98_guard_verdict.to_dict()
        if v98_threat_signal:
            verdict.evidence["v98_threat_signal"] = v98_threat_signal.to_dict()
        if v98_classification:
            verdict.evidence["v98_classification"] = v98_classification.to_dict()
        if v98_attack_match:
            verdict.evidence["v98_attack_match"] = v98_attack_match

        # ============================================================
        # [V100] PHASE 6: CLAIM EXTRACTION + ANTIBODIES + VERIFICATION
        # ============================================================
        with _v100_timer.phase("extraction"):
            # [V103] DomainAntibodySystem — chạy 38 antibodies với domain filter
            # [Task 45-B] Antibody block extracted to _run_antibodies() to reduce judge() CC.
            # [G5-FIX] Pass ai_answer so closure words in the AI's ORIGINAL claim
            # are detected (not just verdict.final_answer which is SCP's response).
            self._run_antibodies(verdict, question, ai_answer)

            if self.claim_extractor and verdict.final_answer:
                try:
                    v100_claims = self.claim_extractor.extract(verdict.final_answer, question)
                    if self.claim_verifier and v100_claims:
                        # Build ground truth from KB hits + SLM responses
                        ground_truth = {}
                        for hit in v100_kb_hits:
                            ground_truth[hit.source] = hit.answer
                        v100_claims = self.claim_verifier.verify(v100_claims, ground_truth)
                        v100_claim_summary = self.claim_verifier.summarize(v100_claims)
                        verdict.evidence["v100_claims"] = v100_claim_summary
                        # If any claim refuted → downgrade confidence
                        if v100_claim_summary["refuted"] > 0:
                            verdict.confidence *= 0.5
                            verdict.reasoning += f" | [V100] {v100_claim_summary['refuted']} claims refuted"
                except Exception as e:
                    logger.debug(f"[V100] Claim extraction error: {e}")

        # ============================================================
        # [V107] PHASE 6.5: LOGICAL AUDITOR — kiểm tra lỗi logic sâu
        # ============================================================
        with _v100_timer.phase("falsification"):  # reuse falsification phase
            if self.logical_auditor and self.logical_auditor.should_audit(verdict.verdict, verdict.final_answer):
                # [V104.18 #4 FIX] Use ThreadPoolExecutor (was: pass when loop running)
                try:
                    import asyncio as _a
                    import concurrent.futures as _cf
                    _pool = _cf.ThreadPoolExecutor(max_workers=1)
                    audit_result = _pool.submit(
                        _a.run,
                        self.logical_auditor.audit(
                            text_to_audit=verdict.final_answer,
                            context=f"Question: {question[:200]}",
                        )
                    ).result(timeout=30)
                    self._apply_logical_audit(verdict, audit_result)
                    _pool.shutdown(wait=False)
                except Exception as e:
                    logger.debug(f"[V107] LogicalAuditor error: {e}")

        # [V106] SelfQuestioningEngine — SCP tự hỏi "Tại Sao?"
        if self.self_questioning and verdict.verdict in ("PASS", "FAIL", "SPECULATIVE"):
            try:
                sq_result = self.self_questioning.question(
                    question=question,
                    answer=verdict.final_answer,
                    verdict=verdict.verdict,
                    confidence=verdict.confidence,
                    reasoning=verdict.reasoning,
                    slm_responses=verdict.slm_responses,
                    evidence=verdict.evidence,
                    domain=verdict.domain,
                )
                verdict.evidence["v106_self_questioning"] = sq_result.to_dict()
                if sq_result.verdict_challenged:
                    verdict.confidence = sq_result.revised_confidence
                    verdict.reasoning += f" | [V106 SelfQuestion] {sq_result.self_critique[:100]}"
            except Exception as e:
                logger.debug(f"[V106] SelfQuestioning error: {e}")

        # ============================================================
        # [V100] PHASE 10: LEARNING — KB save on PASS + H8 bypass analysis
        # ============================================================
        with _v100_timer.phase("learning"):
            # [V100 FIX] Save PASS to KnowledgeStore (V63 bug: 0 knowledge saved)
            if self.domain_knowledge_store and verdict.verdict == "PASS" and verdict.final_answer:
                try:
                    self.domain_knowledge_store.store(
                        question=question[:500],
                        answer=verdict.final_answer[:500],
                        domain=verdict.domain or "general",
                        source="scp_learned",
                        source_url="",
                        confidence=verdict.confidence,
                        collected_by="on_demand",
                        verified_by=["scp_pipeline"],
                    )
                except Exception as e:
                    logger.debug(f"[V100] KB save error: {e}")

            # [V100] Record normal question for H8 FP testing
            if self.h8_redteam and verdict.verdict == "PASS":
                try:
                    self.h8_redteam.record_normal_question(question[:200])
                except Exception as e:
                    # [ROOT-FIX 5] Was `except Exception as e: pass` — swallowed H8 FP
                    # recording errors silently → false-positive baseline never grows.
                    logger.warning(f"[judge] h8_redteam.record_normal_question failed: {e}")

            # [V100] H8 RedTeamBridge — bypass detection + analysis chiều 2
            if self.h8_redteam:
                try:
                    h8_record = self.h8_redteam.record_bypass(
                        question=question,
                        answer=verdict.final_answer,
                        verdict=verdict.verdict,
                        classification=v98_classification.to_dict() if v98_classification else {"actor": "human", "confidence": 0.5},
                        source=source or "unknown",
                        attacker_ip=(v98_context or {}).get("ip", "internal"),
                    )
                    if h8_record:
                        verdict.evidence["v100_bypass_detected"] = True
                        verdict.evidence["v100_bypass_id"] = h8_record.bypass_id
                        verdict.evidence["v100_canary_token"] = h8_record.canary_token
                except Exception as e:
                    logger.debug(f"[V100] H8 error: {e}")

        # ============================================================
        # [V100] PHASE 11: OUTPUT — phase timings + KB hits in evidence
        # ============================================================
        with _v100_timer.phase("output"):
            v100_timings = _v100_timer.finish()
            verdict.evidence["v100_phase_timings"] = v100_timings.to_dict()
            verdict.evidence["v100_kb_hits"] = len(v100_kb_hits)
            if v100_kb_hits:
                verdict.evidence["v100_kb_top_hit"] = {
                    "question": v100_kb_hits[0].question[:100],
                    "answer": v100_kb_hits[0].answer[:100],
                    "source": v100_kb_hits[0].source,
                    "tier": v100_kb_hits[0].source_tier,
                    "confidence": v100_kb_hits[0].confidence,
                }

        # [V104.45 #BV] TẠI SAO: healing was only in SCPV14.process (not on /ask).
        # Fix: run healing monitor + heal after verdict, before return.
        # [V104.45 #BW] TẠI SAO: old healing success_rate measured SQL DELETE success,
        # not whether verdict improved. Fix: record verdict before+after healing.
        if self.healing_engine and verdict.verdict in ("FAIL", "CONFLICT", "UNKNOWN"):
            try:
                _system_state = {
                    "verdict": verdict.verdict,
                    "confidence": verdict.confidence,
                    "domain": verdict.domain or "general",
                    "question": question[:200],
                }
                _issues = self.healing_engine.monitor(_system_state)
                if _issues and _issues.get("issues"):
                    for _issue in _issues["issues"][:3]:  # limit to 3 issues per request
                        try:
                            _heal_result = self.healing_engine.heal(_issue)
                            _strategy = _issue.get("strategy", "unknown")
                            _success = _heal_result.get("success", False) if _heal_result else False

                            # [V104.47 #10] [P2-14 FIX] TẠI SAO: V104.45 measured
                            # verdict_improved as "issue type was confidence-related" —
                            # still a proxy. V104.47 #10 added a real re-check (re-run
                            # primary SLM, compare confidence) BUT gated it on issue
                            # types `("low_confidence", "unknown_verdict")` — which the
                            # healing engine NEVER emits. monitor() actually emits:
                            #   slm_confidence_low, all_slm_fail, high_latency,
                            #   high_error_rate, slm_error, stale_data
                            # → the re-check was DEAD (never ran) → verdict_improved
                            # always False → strategy success_rate stayed a SQL-execution
                            # proxy (bug BW not fixed). Fix: match the REAL issue types
                            # that warrant a confidence re-check.
                            _verdict_improved = False
                            _RECHECK_ISSUE_TYPES = (
                                "slm_confidence_low",  # confidence < 0.3
                                "all_slm_fail",        # UNKNOWN + conf < 0.2
                                "slm_error",           # SLM returned error → retry may help
                                "stale_data",          # CONFLICT → cache clear + re-run may help
                            )
                            if _success and _issue.get("type") in _RECHECK_ISSUE_TYPES:
                                try:
                                    # Lightweight re-check: re-run primary SLM only (not full judge)
                                    _heal_slm_name = primary.get("slm_name", "") if primary else ""
                                    _heal_domain = verdict.domain or "general"
                                    if _heal_slm_name and _heal_slm_name in self.slms:
                                        _re_slm = self.slms[_heal_slm_name]
                                        _re_result = _re_slm.predict(question)
                                        _new_conf = _re_result.confidence if hasattr(_re_result, 'confidence') else 0
                                        if _new_conf > confidence:
                                            _verdict_improved = True
                                            logger.info(f"[V104.47 #10] Healing improved: {confidence:.2f} → {_new_conf:.2f}")
                                except Exception as e:
                                    logger.warning(f"Silent except: {e}")  # re-check failed, keep _verdict_improved = False
                            _heal_result["verdict_improved"] = _verdict_improved

                            verdict.evidence.setdefault("healing_actions", []).append({
                                "strategy": _strategy,
                                "success": _success,
                                "verdict_improved": _verdict_improved,
                            })
                            logger.info(f"[V104.45 #BV] Healing: strategy={_strategy} success={_success} improved={_verdict_improved}")
                        except Exception as _he:
                            logger.debug(f"[V104.45 #BV] Healing strategy error: {_he}")
            except Exception as e:
                logger.debug(f"[V104.45 #BV] Healing monitor error: {e}")

        # [V5.8-OPT] Wire source_reputation.record_outcome — TẠI SAO:
        # scp_reputation.sqlite had 0 rows because the reputation system
        # existed (ReputationStore class, schema, SourceWatchlist wrapper)
        # but NOTHING was calling it on every verdict. The only callers were
        # on_fact_invalidated (Healing Cascade) and on_fact_committed
        # (KB ingestion) — both rare events. So reputation stayed empty.
        #
        # Fix: after verdict is finalized (post-healing, post-governance),
        # call record_outcome() for every source that contributed evidence.
        # was_correct = True iff verdict == "PASS" (matches task spec:
        # "True if verdict PASS, False if FAIL/KILL").
        #
        # This populates source_domain_reputation table ADDITIVELY —
        # does NOT touch existing source_reputation table or break any
        # current caller. Best-effort: any failure is logged + swallowed
        # (MUST NOT break /ask over a reputation write).
        try:
            _rep_store = None
            # Reuse existing ReputationStore if SourceWatchlist was initialized
            if self._source_watchlist is not None:
                _rep_store = getattr(self._source_watchlist, "store", None)
            if _rep_store is None:
                # Fallback: construct a fresh store (opens its own sqlite conn)
                from scp.knowledge.source_reputation import ReputationStore as _RS
                _rep_store = _RS()

            _v58_was_correct = (verdict.verdict == "PASS")
            _v58_domain = verdict.domain or "general"
            _v58_sources_seen: set = set()

            # Collect sources from SLM responses (each may carry evidence.source)
            for _r in (verdict.slm_responses or []):
                try:
                    _ev = _r.get("evidence") or {}
                    _src = _ev.get("source") or _r.get("slm_name")
                    if _src and isinstance(_src, str) and _src not in _v58_sources_seen:
                        _v58_sources_seen.add(_src)
                        try:
                            _rep_store.record_outcome(_src, _v58_domain, _v58_was_correct)
                        except Exception as _re:
                            logger.debug(f"[V5.8-OPT] record_outcome failed for SLM source={_src!r}: {_re}")
                except Exception:
                    continue  # defensive — bad SLM response shouldn't break reputation

            # Also record reality_check source if present (v13.db, REST Countries, etc.)
            try:
                _rc = (verdict.evidence or {}).get("reality_check")
                if isinstance(_rc, dict):
                    _rc_src = _rc.get("source")
                    if _rc_src and isinstance(_rc_src, str) and _rc_src not in _v58_sources_seen:
                        _v58_sources_seen.add(_rc_src)
                        try:
                            _rep_store.record_outcome(_rc_src, _v58_domain, _v58_was_correct)
                        except Exception as _re:
                            logger.debug(f"[V5.8-OPT] record_outcome failed for reality source={_rc_src!r}: {_re}")
            except Exception as e:
                logger.warning(f"Silent except: {e}")

            # Stash forensic trace in verdict.evidence (small, useful for debugging)
            if _v58_sources_seen:
                try:
                    verdict.evidence["v58_source_reputation_recorded"] = {
                        "sources": sorted(_v58_sources_seen),
                        "domain": _v58_domain,
                        "was_correct": _v58_was_correct,
                        "verdict": verdict.verdict,
                    }
                except Exception as e:
                    logger.warning(f"Silent except: {e}")  # verdict.evidence might be immutable in some test paths
        except Exception as e:
            logger.debug(f"[V5.8-OPT] source_reputation wire failed (non-fatal): {e}")


        # [Gà §10] SCP-META hội đồng phản biện — 3 systems vote
        try:
            _meta = _SCPMeta()
            _meta_review = _meta.review(question, verdict.verdict)
            if _meta_review.council_decision.value == "SKIP":
                verdict.verdict = "UNKNOWN"
                verdict.reasoning += ". META: SKIP"
            elif _meta_review.council_decision.value == "DEFER_HUMAN":
                verdict.verdict = "UNKNOWN"
                verdict.reasoning += ". META: DEFER_HUMAN"
        except Exception as _meta_err:
            import logging as _logging
            _logging.getLogger("scp.judge").debug(f"SCPMeta review failed: {_meta_err}")

        return verdict

    # ============================================================
    # [OPT-42] Extracted helpers — pulled out of judge() to reduce CC.
    # Logic moved VERBATIM (no behavior change). DNA SCP #7 (AutoFix safe)
    # + #9 (No harm): each helper has its own try/except so failures stay
    # isolated, exactly as the inline code did.
    # ============================================================

    def _check_knowledge_conflicts(self, why_result, why_plan, question, _pre_verdict_evidence):
        """[OPT-42] Check for knowledge conflicts using KnowledgeArbiter.

        Extracted from judge() to reduce CC. TẠI SAO: KnowledgeArbiter was
        wired inline (CC bloat). ADDITIVE: only consults arbiter + logs;
        does not modify why_engine logic. Arbiter facts are per-judge-call
        (short-lived) — same pattern as why_result.
        """
        try:
            if not hasattr(self, "knowledge_arbiter") or not self.knowledge_arbiter or not why_result:
                return
            _all_values = why_result.get("all_values") or []
            _target = why_result.get("target") or getattr(why_plan, "target", "") or question
            # Add each source value as a fact, then resolve.
            # Skips 0/1 sources (no conflict possible).
            if len(_all_values) > 1:
                for _sv in _all_values:
                    self.knowledge_arbiter.add_fact(
                        entity=_target,
                        attribute="value",
                        value=str(_sv.get("value", "")),
                        source=str(_sv.get("source", "unknown")),
                        confidence=float(why_result.get("confidence", 0.5)),
                    )
                _arbiter_result = self.knowledge_arbiter.resolve(_target, "value")
                if _arbiter_result.get("conflict"):
                    logger.warning(
                        f"[KnowledgeArbiter] Conflict for '{_target}': "
                        f"values={_arbiter_result.get('conflicting_values')} "
                        f"winner={_arbiter_result.get('winner_source')} "
                        f"requires_verification={_arbiter_result.get('requires_verification')}"
                    )
                    # Record in pre-verdict evidence (merged into verdict.evidence later).
                    _pre_verdict_evidence["knowledge_arbiter_conflict"] = {
                        "entity": _target,
                        "conflicting_values": _arbiter_result.get("conflicting_values"),
                        "winner_source": _arbiter_result.get("winner_source"),
                        "requires_verification": _arbiter_result.get("requires_verification"),
                        "reason": _arbiter_result.get("reason"),
                    }
                else:
                    logger.debug(
                        f"[KnowledgeArbiter] No conflict for '{_target}': "
                        f"reason={_arbiter_result.get('reason')}"
                    )
        except Exception as _ka_err:
            logger.debug(f"[KnowledgeArbiter] error: {_ka_err}")

    def _map_cwe_for_attack(self, unified_result, _pre_verdict_evidence):
        """[OPT-42] Map detected attack patterns to CWE.

        Extracted from judge() to reduce CC. TẠI SAO: CWEExploitStore was
        wired inline. ADDITIVE: ImportError/Exception is non-fatal; original
        UnifiedDetector flow untouched. Maps first confident pattern match
        to a CWE so verdict carries concrete mitigation + antibody hints.
        """
        try:
            if not unified_result or not getattr(unified_result, "is_attack", False):
                return
            from scp.knowledge.cwe_exploit_store import CWEExploitStore
            _cwe_store = CWEExploitStore()
            for _pattern in (unified_result.matched_patterns or []):
                _p_lower = str(_pattern).lower()
                if "injection" in _p_lower or "jailbreak" in _p_lower or "ignore" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-LLM01")
                elif "rce" in _p_lower or "exec" in _p_lower or "eval" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-94")
                elif "exfil" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-LLM01")
                elif "ssrf" in _p_lower or "url" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-444")
                elif "path" in _p_lower or "traversal" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-22")
                elif "xss" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-79")
                elif "sql" in _p_lower:
                    _cwe = _cwe_store.get_cwe("CWE-89")
                else:
                    _cwe = None
                if _cwe:
                    logger.info(
                        f"[CWE] Pattern '{_pattern}' → {_cwe.cwe_id}: {_cwe.name} "
                        f"(severity={_cwe.severity}, antibodies={_cwe.antibodies})"
                    )
                    _pre_verdict_evidence["cwe_mapping"] = {
                        "pattern": _pattern,
                        "cwe_id": _cwe.cwe_id,
                        "cwe_name": _cwe.name,
                        "severity": _cwe.severity,
                        "mitigation": _cwe.mitigation,
                        "antibodies": _cwe.antibodies,
                    }
                    break  # first confident match is enough
        except ImportError as e:
            logger.warning(f"Silent except: {e}")  # CWEExploitStore not available — non-fatal
        except Exception as _cwe_err:
            logger.debug(f"[CWE] lookup error: {_cwe_err}")

    # ============================================================
    # [Task 45-B] WHY/Falsification/Antibody blocks extracted from judge()
    # to reduce CC (target < 450, was 541). Logic moved VERBATIM (no behavior
    # change). DNA SCP #7 (AutoFix safe) + #9 (No harm): each helper has its
    # own try/except so failures stay isolated, exactly as the inline code did.
    # ============================================================

    def _run_why_engine(self, question, ai_answer, _enable_closed_loop):
        """[Task 45-B] WHY Engine — create + execute verification plan.

        Extracted from judge() to reduce CC. TẠI SAO: WHY engine block was
        inline (~14 lines, 4 branches). Returns (why_plan, why_result) tuple.
        ADDITIVE: same logic, same guard, same logging — only relocated.
        """
        why_plan = None
        why_result = None
        if _enable_closed_loop and self.why_engine:
            try:
                why_plan = self.why_engine.create_verification_plan(question)  # [V104.46] FIX: was (question, domains[0]) but why_engine accepts only (question)
                # [WHY-FIX Bước 2] Execute plan ngay sau khi tạo — không để pending mãi mãi
                if why_plan and ai_answer:
                    why_result = self.why_engine.execute_plan(why_plan, ai_answer)
                    logger.info(f"[WHY-FIX] Plan executed: verdict={why_result.get('verdict')}, "
                                f"sources={why_result.get('sources_queried')}")
            except Exception as e:
                logger.debug(f"WHY engine error: {e}")
        return why_plan, why_result

    def _run_falsification(self, verdict, question, ai_answer):
        """[Task 45-B] FalsificationEngine — translate + deviation + contradiction report.

        Extracted from judge() to reduce CC. TẠI SAO: Falsification block was
        inline (~100 lines, ~10 branches). Mutates verdict.evidence in place.
        ADDITIVE: same logic, same guards, same logging — only relocated.
        """
        # [V97] FalsificationEngine — translate verdict sang skeptical status
        if self.falsification:
            try:
                falsif_result = self.falsification.translate_old_verdict(
                    verdict.verdict,
                    {
                        "confidence": verdict.confidence,
                        "domain": verdict.domain,
                        "contradictions": verdict.cross_validation.get("conflicts", []),
                        "scope": f"53 SLMs, {self.error_store.count() if self.error_store else 0} ErrorStore records",
                    },
                )
                verdict.evidence["falsification_status"] = falsif_result["status"]
                verdict.evidence["falsification_scope"] = falsif_result.get("scope", "")
                verdict.evidence["falsification_note"] = falsif_result.get("note", "")
            except Exception as e:
                logger.debug(f"[V97] Falsification translate error: {e}")

        # [FIX-CRIT-135 BUG 5] TẠI SAO: FalsificationEngine.measure_deviation()
        # and generate_contradiction_report() existed (fully implemented, smoke-
        # tested at module bottom) but were DEAD CODE in the runtime — only
        # translate_old_verdict() above was ever called. The patent-pending
        # "Continuous Falsification Engine" was a name only; deviation measurement
        # and contradiction reports never ran in production. Fix: invoke
        # generate_contradiction_report() whenever reality_check provides a
        # ground-truth real_value (the only ground-truth signal available at
        # this stage). The report measures LLM answer + SLM evidence against
        # the real value, records contradictions, and surfaces them to
        # Governance + audit trail. The methods are complete (not stubs) so we
        # wire them in rather than log a TODO.
        if self.falsification and verdict.reality_check:
            try:
                # Build ground_truth dict from reality_check — the only
                # ground-truth signal we have at this stage of the pipeline.
                ground_truth: dict[str, Any] = {}
                real_val = verdict.reality_check.get("real_value")
                if real_val is not None:
                    ground_truth["real_value"] = real_val
                # Reality-check may carry other numeric/string fields worth
                # comparing against (e.g. source-attributed values).
                for _rk, _rv in verdict.reality_check.items():
                    if _rk in ("real_value", "is_correct", "verdict",
                               "reason", "source", "verdict_detail"):
                        continue
                    if isinstance(_rv, (int, float)) or (
                        isinstance(_rv, str) and _rv.strip()
                    ):
                        ground_truth[_rk] = _rv

                if ground_truth:
                    # Build evidence list from SLM responses (cross-validation
                    # signals to compare against the same ground truth).
                    evidence_list: list = []
                    for _r in (verdict.slm_responses or []):
                        if isinstance(_r, dict):
                            _ans = _r.get("answer", "")
                            if _ans:
                                evidence_list.append(str(_ans))
                        else:
                            evidence_list.append(str(_r))

                    # Call measure_deviation() directly first — it returns
                    # max_deviation + status (generate_contradiction_report
                    # consumes it internally but does NOT bubble those up).
                    _dev = self.falsification.measure_deviation(
                        ai_answer or "", ground_truth
                    )
                    # generate_contradiction_report() returns the full audit
                    # (contradictions, confidence, v4_recommendation, ...).
                    _report = self.falsification.generate_contradiction_report(
                        question=question or "",
                        llm_answer=ai_answer or "",
                        evidence=evidence_list,
                        ground_truth=ground_truth,
                    )
                    # Surface the report summary for Governance + downstream
                    # audit. Store full contradictions (capped) so the
                    # evidence trail is preserved without bloating verdict.
                    verdict.evidence["falsification_report"] = {
                        "contradiction_count": len(_report.get("contradictions", [])),
                        "max_deviation": _dev.get("max_deviation", 0.0),
                        "deviation_status": _dev.get("status", ""),
                        "confidence": _report.get("confidence", 0.5),
                        "human_decision_required": _report.get(
                            "human_decision_required", False
                        ),
                        "v4_recommendation": _report.get("v4_recommendation", ""),
                    }
                    if _report.get("contradictions"):
                        verdict.evidence["falsification_contradictions"] = (
                            _report["contradictions"][:5]
                        )
                        logger.info(
                            f"[V97] FalsificationEngine: "
                            f"{len(_report['contradictions'])} contradiction(s) "
                            f"detected (max_dev={_dev.get('max_deviation')})"
                        )
            except Exception as e:
                logger.debug(f"[V97] Falsification report error: {e}")

    def _run_antibodies(self, verdict, question, ai_answer=""):
        """[Task 45-B] DomainAntibodySystem — run 38 antibodies + downgrade/KILL.

        Extracted from judge() to reduce CC. TẠI SAO: Antibody block was inline
        (~35 lines, ~8 branches). Mutates verdict in place (evidence, reasoning,
        confidence, verdict, final_answer). ADDITIVE: same logic, same guards,
        same logging — only relocated.

        [G5-FIX] Previously only scanned `verdict.final_answer` (SCP's response).
        But the AI's *original claim* (`ai_answer`) may contain closure words
        even when SCP's response doesn't — e.g. AI says "Obviously true" to
        "Is earth flat?" → SCP's final_answer becomes a fallback like "Tôi
        không có đủ dữ liệu" → antibody finds no closure → no FAIL → test
        test_judge_closure_words_fail expects FAIL but got UNKNOWN.
        Fix: scan BOTH ai_answer AND verdict.final_answer; union the flagged
        results so closure words in either trigger downgrade.
        """
        # [V103] DomainAntibodySystem — chạy 38 antibodies với domain filter
        # [G5-FIX] Scan ai_answer too (the AI's original claim) — not just
        # verdict.final_answer (SCP's response, which may be a fallback).
        if not self.antibody_system:
            return
        texts_to_scan = []
        if ai_answer:
            texts_to_scan.append(("ai_answer", ai_answer))
        if verdict.final_answer:
            texts_to_scan.append(("final_answer", verdict.final_answer))
        if not texts_to_scan:
            return
        try:
            all_results = []
            flagged = []
            seen_keys = set()
            for _source_label, text in texts_to_scan:
                ab_results = self.antibody_system.check(
                    question=question,
                    answer=text,
                    domain=verdict.domain or "general",
                )
                for r in ab_results:
                    if not r.passed:
                        key = (r.antibody_name, r.details)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            flagged.append(r)
                    all_results.append(r)
            verdict.evidence["v103_antibodies"] = {
                "total_run": len(all_results),
                "flagged": len(flagged),
                "results": [r.to_dict() for r in all_results],
                "scanned_texts": [t[0] for t in texts_to_scan],
            }
            # Antibody flags → downgrade confidence
            if flagged:
                for r in flagged:
                    verdict.reasoning += f" | [Antibody] {r.antibody_name}: {r.details}"
                    if str(r.severity) in ("high", "critical"):
                        verdict.confidence *= 0.5
                # [V104.41 #AF] TẠI SAO: antibody CRITICAL ran AFTER Governance
                # → couldn't KILL. Constitution: "any CRITICAL antibody fail → KILL".
                # Fix: if any CRITICAL antibody failed, override verdict to FAIL
                # (re-trigger governance effect post-antibody).
                # [G5-FIX] Was `verdict.verdict == "PASS"` only → UNKNOWN/CONFLICT/
                # PARTIAL verdicts with closure words slipped through (test
                # test_judge_closure_words_fail expected FAIL but got UNKNOWN
                # because "Is earth flat?" → no SLM match → UNKNOWN, then
                # "Obviously true" closure word detected but not downgraded).
                # Now downgrade any non-FAIL/KILL verdict on critical antibody.
                # [G5-FIX] Was `r.get("severity")` and `r.get("passed", True)` —
                # but `r` is an AntibodyResult dataclass (no .get method),
                # so `r.get("severity")` returned None (or raised) and
                # `r.get("passed", True)` returned True → `has_critical` was
                # always False → antibody downgrade was DEAD code (matches
                # the worklog Task 2-B "ảo giác đồng thuận" finding).
                # Fix: use attribute access (r.severity, r.passed).
                has_critical = any(
                    str(r.severity) in ("critical", "high") and not r.passed
                    for r in flagged
                )  # [FIX #4] was "critical" only → dead code. Now includes "high" (DNA #4 fix)
                if has_critical and verdict.verdict not in ("FAIL", "KILL"):
                    _prev_verdict = verdict.verdict
                    verdict.verdict = "FAIL"
                    verdict.final_answer = ""  # [V104.41 #AF] abstain on CRITICAL antibody
                    verdict.reasoning += f" | [V104.41 #AF] CRITICAL antibody fail → FAIL (was {_prev_verdict}, abstain)"
                    verdict.evidence["antibody_kill"] = True
                    logger.info(f"[V104.41 #AF] Antibody CRITICAL override: {_prev_verdict} → FAIL (abstain)")
        except Exception as e:
            logger.debug(f"[V103] Antibody check error: {e}")

    def _attach_why_to_verdict(self, verdict, why_plan, why_result, confidence,
                                question, ai_answer, primary_domain):
        """[Task 45-B] Attach WHY Engine plan/result to verdict evidence + Self-Suspend.

        Extracted from judge() to reduce CC. TẠI SAO: WHY-plan-to-verdict block
        was inline (~70 lines, ~10 branches). Mutates verdict.evidence, verdict.verdict,
        verdict.confidence, verdict.reasoning. Returns possibly-updated confidence.
        ADDITIVE: same logic, same guards, same logging — only relocated.
        """
        if why_plan:
            verdict.evidence["why_plan"] = {
                "target": why_plan.target,
                "target_type": why_plan.target_type,
                "evidence_type": why_plan.evidence_type,
                "verification_strategy": why_plan.verification_strategy,
                "sources_to_query": why_plan.sources_to_query,
                "proof_criteria": why_plan.proof_criteria[:100],
                "falsification_criteria": why_plan.falsification_criteria[:100],
                "confidence_threshold": why_plan.confidence_threshold,
                "reasoning": why_plan.reasoning,
            }
            # [WHY-FIX] Attach execute_plan result — verdict from WHY Engine
            if why_result:
                verdict.evidence["why_result"] = {
                    "verdict": why_result.get("verdict"),
                    "confidence": why_result.get("confidence"),
                    "sources_queried": why_result.get("sources_queried"),
                    "all_values": why_result.get("all_values"),
                    "reasoning": why_result.get("reasoning"),
                }
                # [WHY-FIX] If WHY Engine found FAIL/CONFLICT, adjust verdict
                why_v = why_result.get("verdict", "UNKNOWN")
                if why_v == "FAIL" and verdict.verdict == "PASS":
                    verdict.verdict = "FAIL"
                    verdict.reasoning += f" | WHY Engine FAIL: {why_result.get('reasoning', '')[:100]}"
                elif why_v == "CONFLICT":
                    verdict.verdict = "CONFLICT"
                    verdict.reasoning += f" | WHY Engine CONFLICT: {why_result.get('reasoning', '')[:100]}"
                elif why_v == "PASS" and verdict.verdict in ("UNKNOWN", "PARTIAL"):
                    # [FIX #24] WHY Engine PASS upgrades both UNKNOWN and PARTIAL
                    verdict.verdict = "PASS"
                    confidence = max(confidence, why_result.get("confidence", 0.5))
                    # [ROOT-FIX 43-A / Fix 3] Also propagate confidence to the
                    # verdict object itself. TẠI SAO: line above only updates the
                    # local `confidence` variable, but verdict was already
                    # constructed at line ~1421 with the OLD (pre-WHY) confidence.
                    # Without this, the WHY upgrade (e.g. 0.50 -> 0.85 for single-
                    # source match) is computed but never reaches verdict.confidence
                    # → caller sees conf=0.50 even though WHY confirmed the match.
                    verdict.confidence = max(verdict.confidence, why_result.get("confidence", 0.5))
                    verdict.reasoning += f" | WHY Engine PASS: {why_result.get('reasoning', '')[:100]}"

            # [V34] Self-Suspend mechanism (L4) — nếu WHY plan chỉ ra falsification risk
            # và verdict = PASS nhưng confidence < threshold → mark for re-verification
            if (verdict.verdict == "PASS" and confidence < why_plan.confidence_threshold
                    and why_plan.evidence_type not in ("deterministic_calculation",
                                                        "deterministic_evaluation",
                                                        "codata_constants")):
                verdict.evidence["self_suspend"] = {
                    "reason": f"PASS but confidence {confidence:.2f} < WHY threshold {why_plan.confidence_threshold}",
                    "action": "marked_for_re_verification",
                }
                logger.info(f"Self-Suspend: {verdict.verdict} but confidence low — marked for re-verification")

                # [V36] Enqueue for auto re-verification
                if self.reverify_scheduler:
                    try:
                        reverify_id = self.reverify_scheduler.enqueue_if_needed(
                            question=question, ai_answer=ai_answer,
                            domain=primary_domain, verdict=verdict.verdict,
                            confidence=confidence,
                            why_threshold=why_plan.confidence_threshold,
                            evidence_type=why_plan.evidence_type,
                        )
                        if reverify_id:
                            verdict.evidence["self_suspend"]["reverify_id"] = reverify_id
                    except Exception as e:
                        logger.debug(f"ReVerify enqueue error: {e}")
        return confidence

    def _apply_why_confidence_adjust(self, verdict, why_plan, why_result):
        """[Task 45-B] V5.7 WHY confidence adjustment (opt-in).

        Extracted from judge() to reduce CC. TẠI SAO: WHY confidence adjustment
        block was inline (~38 lines, ~6 branches). Mutates verdict.confidence,
        verdict.reasoning, verdict.evidence. ADDITIVE: same logic, same guards,
        same logging — only relocated. Opt-in via SCP_WHY_CONFIDENCE_ADJUST=1.
        """
        if (os.environ.get("SCP_WHY_CONFIDENCE_ADJUST", "0") == "1"
                and why_plan and why_result and verdict):
            try:
                _why_v = why_result.get("verdict", "UNKNOWN")
                _slm_v = verdict.verdict  # SLM-led verdict (before existing flip)
                _adj_amount = 0.0
                _adj_reason = ""
                if _why_v == "FAIL" and _slm_v == "PASS":
                    _adj_amount = -0.2
                    _adj_reason = "WHY FAIL vs SLM PASS — disagreement"
                elif _why_v == "CONFLICT" and _slm_v == "PASS":
                    _adj_amount = -0.15
                    _adj_reason = "WHY CONFLICT vs SLM PASS — partial disagreement"
                elif _why_v == "PASS" and _slm_v == "PASS":
                    _adj_amount = 0.1
                    _adj_reason = "WHY PASS + SLM PASS — agreement boost"

                if _adj_amount != 0.0:
                    _old_conf = verdict.confidence
                    verdict.confidence = max(0.0, min(1.0, verdict.confidence + _adj_amount))
                    verdict.reasoning += (
                        f" | [V5.7-WHY] confidence {_adj_amount:+.2f} ({_adj_reason}): "
                        f"{_old_conf:.2f} → {verdict.confidence:.2f}"
                    )
                    verdict.evidence["v57_why_confidence_adjust"] = {
                        "why_verdict": _why_v,
                        "slm_verdict": _slm_v,
                        "adjustment": _adj_amount,
                        "before": _old_conf,
                        "after": verdict.confidence,
                        "reason": _adj_reason,
                    }
                    logger.info(
                        f"[V5.7-WHY] confidence {_adj_amount:+.2f} ({_adj_reason}): "
                        f"{_old_conf:.2f} → {verdict.confidence:.2f}"
                    )
            except Exception as _why_conf_err:
                logger.debug(f"[V5.7-WHY] confidence adjustment failed (non-fatal): {_why_conf_err}")

    def _run_cognitive_engine(self, verdict, why_plan, question, primary_domain,
                              confidence, primary, slm_responses, reality_check):
        """[Task 45-B] Cognitive Engine — 5 layers analysis.

        Extracted from judge() to reduce CC. TẠI SAO: Cognitive Engine block
        was inline (~82 lines, ~15 branches). Mutates verdict.evidence["cognitive"].
        ADDITIVE: same logic, same guards, same logging — only relocated.
        """
        if self.cognitive:
            try:
                # [V49] why_plan có thể là dict (khi loaded từ cache) hoặc object
                if hasattr(why_plan, 'evidence_type'):
                    evidence_type = why_plan.evidence_type
                elif isinstance(why_plan, dict):
                    evidence_type = why_plan.get('evidence_type', '')
                else:
                    evidence_type = ''
                is_deterministic = evidence_type in (
                    "deterministic_calculation", "deterministic_evaluation",
                    "codata_constants", "biological_database"
                )

                # [V49] Always run full cognitive analysis — don't skip for high confidence
                primary_source = ""
                if primary:
                    primary_source = primary.get("evidence", {}).get("source", "")
                sources_succeeded = []
                sources_failed = []
                for r in slm_responses:
                    if "error" in r:
                        sources_failed.append(r.get("domain", "?"))
                    elif r.get("evidence", {}).get("sources_succeeded"):
                        sources_succeeded.extend(r["evidence"]["sources_succeeded"])
                    # [V48] Also include evidence source for CognitiveGate verified-source check
                    if r.get("evidence", {}).get("source"):
                        sources_succeeded.append(r["evidence"]["source"])
                    elif r.get("answer"):
                        sources_succeeded.append(r.get("slm_name", r.get("domain", "?")))

                cognitive_result = self.cognitive.analyze(
                    question=question, domain=primary_domain, verdict=verdict.verdict,
                    confidence=confidence, sources_succeeded=sources_succeeded,
                    sources_failed=sources_failed, why_plan=why_plan,
                    reality_check=reality_check, evidence=verdict.evidence,
                    primary_source=primary_source, evidence_type=evidence_type,
                )
                if cognitive_result:
                    # [V65 FIX] high_confidence_skip_gate was set for ALL conf>0.92,
                    #   causing CognitiveGate to skip EVERY high-conf PASS → downgrades=0
                    # Now: only deterministic cases (math AST, logic, codata) skip gate.
                    #   Empirical high-conf cases (crypto, weather, currency) WILL go through gate.
                    #   The gate already protects trusted sources (PubChem, CODATA, LocalDB)
                    #   via the `verified_sources` check + RecursiveWhy `terminated_at` logic.
                    if is_deterministic:
                        cognitive_result["deterministic"] = True
                    verdict.evidence["cognitive"] = cognitive_result

                    # [V49] Debug visibility — log which layers ran
                    layers_ran = []
                    if cognitive_result.get("meta_falsification"):
                        layers_ran.append("MetaFalsifier")
                    if cognitive_result.get("unknown_state"):
                        layers_ran.append("UnknownState")
                    if cognitive_result.get("counter_questions"):
                        layers_ran.append(f"CounterQuestion({len(cognitive_result['counter_questions'])})")
                    if cognitive_result.get("proof_graph"):
                        layers_ran.append("ProofGraph")
                    if cognitive_result.get("recursive_why"):
                        layers_ran.append("RecursiveWhy")
                    logger.info(f"[Cognitive V49] Layers ran: {', '.join(layers_ran) or 'none'} | domain={primary_domain} conf={confidence:.2f}")
                else:
                    # [V38] Lightweight cognitive — chỉ RecursiveWhy (fast, no DB)
                    if primary:
                        primary_source = primary.get("evidence", {}).get("source", "")
                        if primary_source:
                            rw = self.cognitive.recursive_why.recursive_why(
                                question, primary_source, evidence_type
                            )
                            verdict.evidence["cognitive"] = {
                                "recursive_why": {
                                    "depth": rw.depth_reached,
                                    "terminated_at": rw.terminated_at,
                                    "final_trust": rw.final_trust,
                                    "chain": [{"level": link.level, "answer": link.answer, "is_axiom": link.is_axiom}
                                              for link in rw.chain[:2]],
                                },
                                "skipped": "no_cognitive_result",
                            }
            except Exception as e:
                logger.debug(f"Cognitive analysis error: {e}")

    def _apply_cognitive_gate(self, verdict, why_plan, confidence, question,
                              primary_domain, slm_responses):
        """[Task 45-B] Cognitive Gate — cognitive layers AFFECT verdict.

        Extracted from judge() to reduce CC. TẠI SAO: Cognitive Gate block was
        inline (~46 lines, ~10 branches). Mutates verdict.evidence, verdict.verdict,
        verdict.confidence. Returns possibly-updated confidence.
        ADDITIVE: same logic, same guards, same logging — only relocated.
        """
        cognitive_result = verdict.evidence.get("cognitive")
        if cognitive_result and verdict.verdict == "PASS":
            try:
                from scp.meta.cognitive_gate import get_cognitive_gate
                gate = get_cognitive_gate()
                evidence_type = why_plan.evidence_type if why_plan else ""
                sources_succeeded_for_gate = []
                for r in slm_responses:
                    ev = r.get("evidence", {})
                    if ev.get("sources_succeeded"):
                        sources_succeeded_for_gate.extend(ev["sources_succeeded"])
                    # [V48 FIX] Also extract source from evidence (not just SLM name)
                    # Trước V48: chỉ thêm slm_name → "GeoSLM" không match verified_sources
                    # V48: thêm evidence.source (vd "LocalDB", "PubChem") → match
                    if ev.get("source"):
                        sources_succeeded_for_gate.append(ev["source"])
                    elif r.get("answer") and not ev.get("source"):
                        sources_succeeded_for_gate.append(r.get("slm_name", r.get("domain", "?")))

                gated_verdict, gate_reasons, original_verdict = gate.evaluate(
                    verdict=verdict.verdict,
                    confidence=confidence,
                    cognitive_result=cognitive_result,
                    evidence_type=evidence_type,
                    sources_succeeded=sources_succeeded_for_gate,
                    question=question,    # [V50] for logging
                    domain=primary_domain,  # [V50] for logging
                )
                if gated_verdict != verdict.verdict:
                    verdict.evidence["cognitive_gate"] = {
                        "original_verdict": original_verdict,
                        "gated_verdict": gated_verdict,
                        "reasons": gate_reasons,
                    }
                    verdict.verdict = gated_verdict
                    # Adjust confidence down for downgrades
                    if gated_verdict == "UNKNOWN":
                        confidence *= 0.4
                        verdict.confidence = confidence
                    elif gated_verdict == "PARTIAL":
                        confidence *= 0.7
                        verdict.confidence = confidence
            except Exception as e:
                logger.debug(f"CognitiveGate error: {e}")
        return confidence

    def _run_multi_llm_check(self, verdict, question, ai_answer):
        """[Task 45-B] Multi-LLM cross-check (opt-in via SCP_MULTI_LLM_CHECK=1).

        Extracted from judge() to reduce CC. TẠI SAO: Multi-LLM check block was
        inline (~33 lines, ~5 branches). Mutates verdict.evidence, verdict.confidence,
        verdict.reasoning. ADDITIVE: same logic, same guards, same logging — only
        relocated. Two independent layers: source-level (adversary) + model-level
        (multi-LLM). Opt-in via SCP_MULTI_LLM_CHECK=1 (default OFF — adds 2 LLM
        API calls/question). If providers disagree (speculative=True) → lower
        confidence by 0.1.
        """
        multi_llm_checker = _get_multi_llm_checker()
        if multi_llm_checker is not None and ai_answer:
            try:
                # primary_answer = the AI answer being verified by SCP
                ml_result = multi_llm_checker.check(question, ai_answer)
                verdict.evidence["v106_multi_llm_check"] = {
                    "consensus": ml_result.get("consensus"),
                    "similarity": ml_result.get("similarity"),
                    "speculative": ml_result.get("speculative"),
                    "reason": ml_result.get("reason"),
                    "providers_queried": list(ml_result.get("provider_answers", {}).keys()),
                }
                # If providers disagree → lower confidence by 0.1 (clamp to >= 0.0)
                if ml_result.get("speculative"):
                    old_conf = verdict.confidence
                    verdict.confidence = max(0.0, verdict.confidence - 0.1)
                    verdict.reasoning += (
                        f" | [V5.3] Multi-LLM speculative ({ml_result.get('consensus')}, "
                        f"sim={ml_result.get('similarity', 0):.2f}): "
                        f"confidence {old_conf:.2f} → {verdict.confidence:.2f}"
                    )
                    logger.info(
                        f"[V5.3-WIRE] Multi-LLM speculative: consensus={ml_result.get('consensus')} "
                        f"sim={ml_result.get('similarity')} — confidence lowered by 0.1"
                    )
                else:
                    logger.debug(
                        f"[V5.3-WIRE] Multi-LLM consensus={ml_result.get('consensus')} "
                        f"sim={ml_result.get('similarity')} — no confidence adjustment"
                    )
            except Exception as e:
                logger.warning(f"[V5.3-WIRE] multi_llm_check failed: {e} — skipping (non-fatal)")
                verdict.evidence["v106_multi_llm_check"] = {"error": str(e)}

