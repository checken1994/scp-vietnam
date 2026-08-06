"""
LAYER 1: METAFALSIFIER — "Plan có thiếu kiểu phản bác nào?"

Meta-Falsification — kiểm tra chính VerificationPlan.

WHY Engine tạo plan → MetaFalsifier hỏi:
    "Plan này có thiếu kiểu phản bác nào không?"

VD: Plan cho "giá BTC" chỉ check "≥3 exchanges agree"
→ MetaFalsifier phát hiện thiếu:
    - Temporal check (price at what time?)
    - Exchange location check (US vs Asian exchanges?)
    - Volume-weighted check (low-volume exchange skews?)

Extracted from `meta/cognitive_engine.py` in Task 10-B (Modularity Refactor B).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetaFalsificationResult:
    """Result từ MetaFalsifier — kiểm tra VerificationPlan."""
    plan_complete: bool
    missing_attack_vectors: list[str]
    suggested_additions: list[str]
    reasoning: str
    revised_plan: dict | None = None


class MetaFalsifier:
    """
    Meta-Falsification — kiểm tra chính VerificationPlan.

    WHY Engine tạo plan → MetaFalsifier hỏi:
        "Plan này có thiếu kiểu phản bác nào không?"

    VD: Plan cho "giá BTC" chỉ check "≥3 exchanges agree"
    → MetaFalsifier phát hiện thiếu:
        - Temporal check (price at what time?)
        - Exchange location check (US vs Asian exchanges?)
        - Volume-weighted check (low-volume exchange skews?)
    """

    # Attack vectors mà mọi plan nên có
    REQUIRED_ATTACK_VECTORS = {
        "numeric_value": [
            "source_agreement",       # ≥N sources agree?
            "temporal_freshness",     # data stale?
            "tolerance_check",        # within tolerance?
        ],
        "string_value": [
            "source_agreement",
            "exact_match",            # exact string vs fuzzy?
            "language_check",         # correct language?
        ],
        "boolean_value": [
            "deterministic_check",    # reproducible?
            "boundary_check",         # edge cases?
        ],
        "entity_fact": [
            "source_agreement",
            "temporal_check",         # still true?
            "authority_check",        # source authoritative?
        ],
        # [V50] Domain-specific attack vectors for V46 domains
        "medical_fact": [
            "source_agreement",
            "medical_guideline_currency",  # hướng dẫn điều trị còn hiệu lực?
            "dosage_range_check",          # liều lượng trong ngưỡng an toàn?
            "authority_check",             # source is medical authority (WHO, FDA)?
        ],
        "legal_fact": [
            "source_agreement",
            "jurisdiction_check",          # luật áp dụng ở đâu (VN/US/EU)?
            "effective_date_check",        # luật còn hiệu lực?
            "authority_check",             # source is government/official?
        ],
        "art_attribution": [
            "source_agreement",
            "attribution_consensus",       # nhiều nguồn cùng công nhận tác giả?
            "period_consistency",          # phong cách khớp thời kỳ?
            "provenance_check",            # lịch sử sở hữu rõ ràng?
        ],
        "sports_record": [
            "source_agreement",
            "temporal_check",              # kỷ lục còn đứng không?
            "official_record_check",       # nguồn là governing body (FIFA, IOC)?
        ],
        "tech_fact": [
            "source_agreement",
            "version_check",               # phiên bản công nghệ còn current?
            "authority_check",             # source is official docs?
        ],
    }

    def falsify_plan(self, plan) -> MetaFalsificationResult:
        """
        Check if VerificationPlan is missing attack vectors.

        [V37 FIX] Skip irrelevant vectors for deterministic domains.
        Math/logic don't need source_agreement or temporal_freshness.
        """
        missing = []
        suggestions = []

        # Get required vectors for this answer type
        answer_type = getattr(plan, 'expected_answer_type', 'string')
        evidence_type = getattr(plan, 'evidence_type', '')

        # [V37 FIX] For deterministic domains, skip temporal/source checks
        is_deterministic = evidence_type in (
            "deterministic_calculation", "deterministic_evaluation",
            "codata_constants", "biological_database"
        )

        required = self.REQUIRED_ATTACK_VECTORS.get(
            f"{answer_type}_value",
            self.REQUIRED_ATTACK_VECTORS["entity_fact"]
        )

        # [V37 FIX] Filter out irrelevant vectors for deterministic
        if is_deterministic:
            required = [v for v in required if v in ("deterministic_check", "boundary_check")]
            if not required:
                required = ["deterministic_check"]  # At least check reproducibility

        # Check plan's proof_criteria + falsification_criteria
        proof = getattr(plan, 'proof_criteria', '').lower()
        falsify = getattr(plan, 'falsification_criteria', '').lower()
        combined = f"{proof} {falsify}"

        for vector in required:
            # Check if vector is covered
            if vector == "source_agreement" and "agree" not in combined and "source" not in combined:
                missing.append(vector)
                suggestions.append("Add: verify ≥2 independent sources agree")
            elif vector == "temporal_freshness" and "time" not in combined and "fresh" not in combined and "stale" not in combined:
                missing.append(vector)
                suggestions.append("Add: check data is not stale (timestamp < TTL)")
            elif vector == "tolerance_check" and "toleran" not in combined and "within" not in combined:
                missing.append(vector)
                suggestions.append("Add: define acceptable tolerance range")
            elif vector == "exact_match" and "exact" not in combined and "match" not in combined:
                missing.append(vector)
                suggestions.append("Add: require exact string match (not fuzzy)")
            elif vector == "language_check" and "language" not in combined:
                missing.append(vector)
                suggestions.append("Add: verify answer language matches question language")
            elif vector == "deterministic_check" and "deterministic" not in combined and "reproduc" not in combined:
                missing.append(vector)
                suggestions.append("Add: verify result is reproducible")
            elif vector == "boundary_check" and "boundary" not in combined and "edge" not in combined:
                missing.append(vector)
                suggestions.append("Add: test boundary/edge cases")
            elif vector == "temporal_check" and "still" not in combined and "current" not in combined:
                missing.append(vector)
                suggestions.append("Add: verify fact is still true (not historical)")
            elif vector == "authority_check" and "authorit" not in combined and "official" not in combined:
                missing.append(vector)
                suggestions.append("Add: verify source is authoritative for this domain")

        plan_complete = len(missing) == 0

        return MetaFalsificationResult(
            plan_complete=plan_complete,
            missing_attack_vectors=missing,
            suggested_additions=suggestions,
            reasoning=f"Plan covers {len(required) - len(missing)}/{len(required)} required attack vectors"
                      + (" — COMPLETE" if plan_complete else f" — MISSING: {missing}"),
        )
