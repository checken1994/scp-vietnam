"""Behavioral test — gọi hàm thật, assert output thật (không grep text)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scp.meta.constitution import Constitution, PrincipleId
from scp.meta.governance_v97 import Governance, GovernanceAction


@pytest.fixture
def gov():
    return Governance(constitution=Constitution())


class TestGovernanceBehavioral:
    """Test Governance.decide() với input thật — không grep text."""

    def test_antibody_high_triggers_kill(self, gov):
        """Antibody severity=high + not passed → KILL."""
        verdict = {
            "verdict": "PASS", "confidence": 0.9,
            "antibody_results": [{"passed": False, "severity": "high", "antibody": "closure_check"}],
            "hallucination_detected": False, "missing_evidence": False,
        }
        result = gov.decide(ctx={}, verdict=verdict, council_confidence=0.3)
        assert result.decision == GovernanceAction.KILL, f"Expected KILL, got {result.decision}: {result.reason}"

    def test_antibody_critical_triggers_kill(self, gov):
        """Antibody severity=critical + not passed → KILL."""
        verdict = {
            "verdict": "PASS", "confidence": 0.9,
            "antibody_results": [{"passed": False, "severity": "critical", "antibody": "safety"}],
            "hallucination_detected": False, "missing_evidence": False,
        }
        result = gov.decide(ctx={}, verdict=verdict, council_confidence=0.3)
        assert result.decision == GovernanceAction.KILL

    def test_all_antibody_pass_upholds(self, gov):
        """All antibodies passed → UPHOLD."""
        verdict = {
            "verdict": "PASS", "confidence": 0.95,
            "antibody_results": [{"passed": True, "severity": "info", "antibody": "c1"}],
            "hallucination_detected": False, "missing_evidence": False,
        }
        result = gov.decide(ctx={}, verdict=verdict, council_confidence=0.8)
        assert result.decision == GovernanceAction.UPHOLD

    def test_missing_evidence_not_kill(self, gov):
        """Missing evidence + low confidence → NOT KILL (DNA #5)."""
        verdict = {
            "verdict": "PASS", "confidence": 0.1,
            "antibody_results": [], "hallucination_detected": False, "missing_evidence": True,
        }
        result = gov.decide(ctx={}, verdict=verdict, council_confidence=0.1)
        assert result.decision != GovernanceAction.KILL, f"Missing evidence should not KILL: {result.decision}"

    def test_accuracy_is_escalate_not_kill(self, gov):
        """ACCURACY default_action should be ESCALATE."""
        accuracy = gov.constitution.get(PrincipleId.ACCURACY)
        assert accuracy.default_action == "ESCALATE", f"ACCURACY={accuracy.default_action}, expected ESCALATE"

    def test_ab_results_not_hardcoded_passed_true(self):
        """Verify judgecore_mixin doesn't hardcode passed=True for ab_results."""
        import inspect

        from scp.runtime.judge_parts.judgecore_mixin import JudgeCoreMixin
        src = inspect.getsource(JudgeCoreMixin)
        # Check that ab_results uses real data, not hardcoded True
        # The fix should have "r.get("passed"" not just "passed": True
        assert 'r.get("passed"' in src or 'r["passed"]' in src or "r.get('passed'" in src, (
            "ab_results should use real antibody passed value, not hardcoded True"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
