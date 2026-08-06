"""
SCP V106 — SelfQuestioningEngine
=================================
SCP tự hỏi "TẠI SAO?" sau mỗi verdict — phản biện chính mình.

3 câu hỏi tự phản biện:
  1. "TẠI SAO tôi ra verdict này thay vì verdict khác?"
     → Phân tích reasoning chain → tìm giả định ẩn
  2. "TẠI SAO confidence là X thay vì Y?"
     → Truy nguồn confidence → tìm yếu tố không chắc chắn
  3. "NẾU giả định ẩn sai, verdict có thay đổi không?"
     → What-if analysis → stress test verdict

Output: SelfQuestioningResult
  ├── verdict_challenged: bool (verdict có bị thách thức không)
  ├── hidden_assumptions: List[str] (giả định ẩn phát hiện)
  ├── confidence_factors: List[Dict] (yếu tố ảnh hưởng confidence)
  ├── what_if_scenarios: List[Dict] (kịch bản thay thế)
  ├── revised_confidence: float (confidence điều chỉnh)
  └── self_critique: str (tự phê bình)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.meta.self_questioning")


@dataclass
class HiddenAssumption:
    """Giả định ẩn phát hiện bởi SelfQuestioningEngine."""
    assumption: str
    risk_if_wrong: str  # "verdict changes" | "confidence drops" | "no impact"
    confidence_impact: float  # 0.0-1.0 — how much confidence should drop if assumption is wrong


@dataclass
class ConfidenceFactor:
    """Yếu tố ảnh hưởng confidence."""
    factor: str
    current_value: float
    ideal_value: float
    gap: float  # ideal - current
    source: str  # "slm" | "reality_check" | "cross_validation" | "antibody" | "governance"


@dataclass
class WhatIfScenario:
    """Kịch bản what-if — nếu giả định sai thì sao."""
    scenario: str
    original_verdict: str
    alternative_verdict: str
    confidence_change: float
    triggered: bool


@dataclass
class SelfQuestioningResult:
    """Kết quả tự phản biện."""
    verdict_challenged: bool = False
    hidden_assumptions: list[HiddenAssumption] = field(default_factory=list)
    confidence_factors: list[ConfidenceFactor] = field(default_factory=list)
    what_if_scenarios: list[WhatIfScenario] = field(default_factory=list)
    revised_confidence: float = 0.0
    self_critique: str = ""
    questions_asked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_challenged": self.verdict_challenged,
            "hidden_assumptions": [
                {"assumption": a.assumption, "risk_if_wrong": a.risk_if_wrong, "confidence_impact": a.confidence_impact}
                for a in self.hidden_assumptions
            ],
            "confidence_factors": [
                {"factor": f.factor, "current": f.current_value, "ideal": f.ideal_value, "gap": f.gap, "source": f.source}
                for f in self.confidence_factors
            ],
            "what_if_scenarios": [
                {"scenario": s.scenario, "original": s.original_verdict, "alternative": s.alternative_verdict,
                 "confidence_change": s.confidence_change, "triggered": s.triggered}
                for s in self.what_if_scenarios
            ],
            "revised_confidence": round(self.revised_confidence, 3),
            "self_critique": self.self_critique,
            "questions_asked": self.questions_asked,
        }


class SelfQuestioningEngine:
    """SCP tự hỏi "TẠI SAO?" sau mỗi verdict — phản biện chính mình.

    Naming convention: <Purpose>Engine (world standard).

    Chạy SAU Governance, TRƯỚC output:
      Governance.decide() → SelfQuestioningEngine.question() → output

    3 câu hỏi:
      1. "Tại sao tôi ra verdict này?" → tìm giả định ẩn
      2. "Tại sao confidence là X?" → truy nguồn
      3. "Nếu giả định sai thì sao?" → what-if
    """

    def __init__(self):
        self._stats = {
            "total_questions": 0,
            "total_challenged": 0,
            "total_assumptions_found": 0,
            "total_verdicts_revised": 0,
        }

    def question(
        self,
        question: str,
        answer: str,
        verdict: str,
        confidence: float,
        reasoning: str,
        slm_responses: list[dict[str, Any]],
        evidence: dict[str, Any],
        domain: str = "general",
    ) -> SelfQuestioningResult:
        """Tự hỏi "Tại sao?" sau khi ra verdict.

        Args:
            question: Câu hỏi gốc
            answer: Câu trả lời
            verdict: Verdict (PASS/FAIL/UNKNOWN/SPECULATIVE)
            confidence: Confidence (0-1)
            reasoning: Reasoning chain
            slm_responses: SLM responses
            evidence: Evidence dict
            domain: Domain

        Returns:
            SelfQuestioningResult
        """
        self._stats["total_questions"] += 1
        result = SelfQuestioningResult(revised_confidence=confidence)

        # === CÂU HỎI 1: "Tại sao tôi ra verdict này?" ===
        result.questions_asked.append(f"Tại sao SCP ra verdict={verdict} thay vì {'FAIL' if verdict == 'PASS' else 'PASS'}?")
        assumptions = self._find_hidden_assumptions(
            question, answer, verdict, reasoning, slm_responses, {**evidence, "_confidence": confidence}, domain
        )
        result.hidden_assumptions = assumptions
        self._stats["total_assumptions_found"] += len(assumptions)

        # === CÂU HỜI 2: "Tại sao confidence là X?" ===
        result.questions_asked.append(f"Tại sao confidence={confidence:.2f} thay vì cao hơn/thấp hơn?")
        factors = self._analyze_confidence_factors(confidence, slm_responses, evidence)
        result.confidence_factors = factors

        # === CÂU HỎI 3: "Nếu giả định sai thì sao?" ===
        result.questions_asked.append("Nếu giả định ẩn sai, verdict có thay đổi không?")
        scenarios = self._what_if_analysis(verdict, confidence, assumptions, evidence)
        result.what_if_scenarios = scenarios

        # === ĐIỀU CHỈNH CONFIDENCE ===
        total_impact = sum(a.confidence_impact for a in assumptions if a.risk_if_wrong == "verdict changes")
        if total_impact > 0:
            result.revised_confidence = max(0.0, confidence - total_impact * 0.1)
            if result.revised_confidence < confidence:
                result.verdict_challenged = True
                self._stats["total_challenged"] += 1
        else:
            result.revised_confidence = confidence

        # === TỰ PHÊ BÌNH ===
        result.self_critique = self._generate_self_critique(
            verdict, confidence, result.revised_confidence, assumptions, factors, scenarios
        )

        # If verdict challenged → log
        if result.verdict_challenged:
            self._stats["total_verdicts_revised"] += 1
            logger.info(
                f"[SelfQuestion] Verdict CHALLENGED: {verdict} conf={confidence:.2f}→{result.revised_confidence:.2f} "
                f"assumptions={len(assumptions)} what_if={sum(1 for s in scenarios if s.triggered)}"
            )

        return result

    def _find_hidden_assumptions(
        self, question, answer, verdict, reasoning, slm_responses, evidence, domain
    ) -> list[HiddenAssumption]:
        """Tìm giả định ẩn trong reasoning chain."""
        assumptions = []
        # Get confidence from the question() method's parameter
        # We need to pass it through — use evidence dict as fallback
        confidence = evidence.get("_confidence", 0.5) if isinstance(evidence, dict) else 0.5

        # Assumption 1: SLM answer is correct
        if verdict == "PASS" and slm_responses:
            num_slm = len([r for r in slm_responses if r.get("answer")])
            if num_slm < 2:
                assumptions.append(HiddenAssumption(
                    assumption=f"SCP assume SLM answer is correct — nhưng chỉ {num_slm} SLM trả lời (thiếu cross-verify)",
                    risk_if_wrong="verdict changes",
                    confidence_impact=0.15,
                ))

        # Assumption 2: DataSource is current
        reality_check = evidence.get("reality_check", {}) if isinstance(evidence, dict) else {}
        if reality_check and reality_check.get("real_value"):
            assumptions.append(HiddenAssumption(
                assumption="SCP assume DataSource trả về giá trị hiện tại — nhưng data có thể đã cũ",
                risk_if_wrong="confidence drops",
                confidence_impact=0.05,
            ))

        # Assumption 3: No adversarial input
        if not evidence.get("v98_guard_verdict"):
            assumptions.append(HiddenAssumption(
                assumption="SCP assume input không phải adversarial — nhưng ThreatDetector không chạy (không có IP/headers)",
                risk_if_wrong="verdict changes",
                confidence_impact=0.10,
            ))

        # Assumption 4: Domain routing is correct
        if domain == "general" and verdict == "UNKNOWN":
            assumptions.append(HiddenAssumption(
                assumption="SCP assume domain='general' là đúng routing — nhưng có thể câu hỏi thuộc domain khác chưa cover",
                risk_if_wrong="verdict changes",
                confidence_impact=0.20,
            ))

        # Assumption 5: Speculative mode — không có evidence
        if verdict == "SPECULATIVE":
            assumptions.append(HiddenAssumption(
                assumption="SCP assume câu hỏi mang tính sáng tạo — nhưng có thể là câu hỏi factual mà SCP chưa có data",
                risk_if_wrong="verdict changes",
                confidence_impact=0.15,
            ))

        # Assumption 6: Confidence threshold is appropriate
        if confidence > 0.8 and verdict == "PASS":
            assumptions.append(HiddenAssumption(
                assumption=f"SCP assume confidence={confidence:.2f} đủ cao cho PASS — nhưng threshold có thể quá thấp cho domain '{domain}'",
                risk_if_wrong="confidence drops",
                confidence_impact=0.05,
            ))

        return assumptions

    def _analyze_confidence_factors(
        self, confidence, slm_responses, evidence
    ) -> list[ConfidenceFactor]:
        """Phân tích yếu tố ảnh hưởng confidence."""
        factors = []

        # Factor 1: SLM confidence
        slm_confs = [r.get("confidence", 0) for r in slm_responses if r.get("answer")]
        if slm_confs:
            avg_slm = sum(slm_confs) / len(slm_confs)
            factors.append(ConfidenceFactor(
                factor="avg_slm_confidence",
                current_value=avg_slm,
                ideal_value=0.9,
                gap=0.9 - avg_slm,
                source="slm",
            ))

        # Factor 2: Number of SLMs
        num_slm = len([r for r in slm_responses if r.get("answer")])
        factors.append(ConfidenceFactor(
            factor="num_slm_responses",
            current_value=float(num_slm),
            ideal_value=3.0,
            gap=max(0, 3.0 - num_slm),
            source="slm",
        ))

        # Factor 3: Reality check
        reality_check = evidence.get("reality_check", {}) if isinstance(evidence, dict) else {}
        has_reality = 1.0 if reality_check.get("real_value") else 0.0
        factors.append(ConfidenceFactor(
            factor="reality_check_present",
            current_value=has_reality,
            ideal_value=1.0,
            gap=1.0 - has_reality,
            source="reality_check",
        ))

        # Factor 4: Cross-validation
        cross_val = evidence.get("cross_validation", {}) if isinstance(evidence, dict) else {}
        consistency = cross_val.get("consistency_score", 0) if isinstance(cross_val, dict) else 0
        factors.append(ConfidenceFactor(
            factor="consistency_score",
            current_value=float(consistency),
            ideal_value=0.9,
            gap=0.9 - consistency,
            source="cross_validation",
        ))

        # Factor 5: Antibody checks
        antibodies = evidence.get("v103_antibodies", {}) if isinstance(evidence, dict) else {}
        if isinstance(antibodies, dict):
            ab_flagged = antibodies.get("flagged", 0)
            factors.append(ConfidenceFactor(
                factor="antibody_flags",
                current_value=float(ab_flagged),
                ideal_value=0.0,
                gap=float(ab_flagged),
                source="antibody",
            ))

        return factors

    def _what_if_analysis(
        self, verdict, confidence, assumptions, evidence
    ) -> list[WhatIfScenario]:
        """What-if analysis — nếu giả định sai thì sao."""
        scenarios = []

        for _i, assumption in enumerate(assumptions):
            if assumption.risk_if_wrong == "verdict changes":
                alt_verdict = "FAIL" if verdict == "PASS" else "PASS"
                if verdict == "UNKNOWN":
                    alt_verdict = "PASS"  # if assumption wrong, maybe it's actually answerable
                if verdict == "SPECULATIVE":
                    alt_verdict = "UNKNOWN"  # if not creative, just unknown

                scenarios.append(WhatIfScenario(
                    scenario=f"Nếu giả định sai: '{assumption.assumption[:60]}...'",
                    original_verdict=verdict,
                    alternative_verdict=alt_verdict,
                    confidence_change=-assumption.confidence_impact,
                    triggered=True,
                ))
            elif assumption.risk_if_wrong == "confidence drops":
                scenarios.append(WhatIfScenario(
                    scenario=f"Nếu giả định sai: '{assumption.assumption[:60]}...'",
                    original_verdict=verdict,
                    alternative_verdict=verdict,
                    confidence_change=-assumption.confidence_impact,
                    triggered=False,
                ))

        return scenarios

    def _generate_self_critique(
        self, verdict, confidence, revised_confidence, assumptions, factors, scenarios
    ) -> str:
        """Tạo tự phê bình — SCP tự đánh giá."""
        critique_parts = []

        if revised_confidence < confidence:
            critique_parts.append(
                f"SCP đã điều chỉnh confidence từ {confidence:.2f} xuống {revised_confidence:.2f} "
                f"do phát hiện {len(assumptions)} giả định ẩn."
            )

        high_risk = [a for a in assumptions if a.risk_if_wrong == "verdict changes"]
        if high_risk:
            critique_parts.append(
                f"CẢNH BÁO: {len(high_risk)} giả định có thể thay đổi verdict. "
                f"Verdict {verdict} có thể không đáng tin cậy."
            )

        large_gaps = [f for f in factors if f.gap > 0.3]
        if large_gaps:
            critique_parts.append(
                f"{len(large_gaps)} yếu tố confidence có gap lớn (>0.3): "
                f"{', '.join(f.factor for f in large_gaps)}. "
                f"Confidence có thể được đánh giá quá cao."
            )

        triggered_scenarios = [s for s in scenarios if s.triggered]
        if triggered_scenarios:
            critique_parts.append(
                f"{len(triggered_scenarios)} kịch bản what-if có thể thay đổi verdict. "
                f"Nên xem xét verdict thay thế."
            )

        if not critique_parts:
            critique_parts.append(
                f"Verdict {verdict} (conf={confidence:.2f}) ổn định — không phát hiện giả định rủi ro cao."
            )

        return " | ".join(critique_parts)

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "challenge_rate": self._stats["total_challenged"] / max(1, self._stats["total_questions"]),
        }


__all__ = [
    "HiddenAssumption", "ConfidenceFactor", "WhatIfScenario",
    "SelfQuestioningResult", "SelfQuestioningEngine",
]
