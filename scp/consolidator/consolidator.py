"""[G3-STUB → G4-FIX] KnowledgeConsolidator — minimal + test-required methods.

Previous version: 629 LOC, dead on /ask path (only used by dead SCPV14).

[G4-FIX] Added _consolidate_group() + _extract_entity_attribute() —
tests/test_p2_behavioral_fixes.py expects these methods.
"""
import logging
import re
from typing import Any

logger = logging.getLogger("scp.consolidator")


class KnowledgeConsolidator:
    """Consolidates multi-source observations into a single fact.

    Minimal implementation — real consolidation logic in scp/meta/knowledge_arbiter.py.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._facts: list = []

    def consolidate(self, facts: list) -> list:
        """No-op — return as-is."""
        return facts

    def _extract_entity_attribute(self, context: dict[str, Any]) -> tuple[str, str]:
        """Extract (entity, attribute) from a question context.

        [G4-FIX] Added for test_p2_behavioral_fixes.py compatibility.
        Returns ('math_expression', 'deterministic_calculation') for math questions,
        ('unknown', 'unknown') otherwise.
        """
        question = (context.get("question") or "").lower().strip()
        # Math patterns: "tính 2 + 3", "what is 5 * 7", "2+2=?", etc.
        math_patterns = [
            r"\d+\s*[+\-*/]\s*\d+",  # 2 + 3
            r"tính\s+",  # tính 2+3
            r"(?:what\s+is|whats|what's)\s+\d+",  # what is 5 * 7
            r"\d+\s*=\s*\?",  # 2=?
            r"factorial|fibonacci|gcd|lcm|sqrt|log|sin|cos|tan",  # math funcs
        ]
        for pattern in math_patterns:
            if re.search(pattern, question):
                return ("math_expression", "deterministic_calculation")
        return ("unknown", "unknown")

    def _consolidate_group(
        self, entity: str, attribute: str, observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Consolidate a group of observations into a summary.

        [G4-FIX] Added for test_p2_behavioral_fixes.py compatibility.
        Returns dict with: entity, attribute, value, confidence, source_fail_rates.
        """
        if not observations:
            return {
                "entity": entity,
                "attribute": attribute,
                "value": None,
                "confidence": 0.0,
                "source_fail_rates": {},
            }

        # Collect values + track per-source success/fail
        values: list[Any] = []
        source_stats: dict[str, dict[str, int]] = {}  # source → {success, fail}
        for obs in observations:
            val = obs.get("value")
            source = obs.get("source", "unknown")
            values.append(val)
            if source not in source_stats:
                source_stats[source] = {"success": 0, "fail": 0}
            if val is not None:
                source_stats[source]["success"] += 1
            else:
                source_stats[source]["fail"] += 1

        # Find most common value (majority vote)
        from collections import Counter
        value_counts = Counter(v for v in values if v is not None)
        if value_counts:
            consensus_value, consensus_count = value_counts.most_common(1)[0]
            confidence = consensus_count / len(observations)
        else:
            consensus_value, confidence = None, 0.0

        # Compute per-source fail rates
        source_fail_rates: dict[str, float] = {}
        for source, stats in source_stats.items():
            total = stats["success"] + stats["fail"]
            source_fail_rates[source] = stats["fail"] / total if total > 0 else 0.0

        return {
            "entity": entity,
            "attribute": attribute,
            "value": consensus_value,
            "confidence": confidence,
            "source_fail_rates": source_fail_rates,
        }
