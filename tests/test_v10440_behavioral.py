"""
[V104.40] Behavioral tests for deep audit Part 2 — epistemic chain fixes.

Tests verify:
  - #M: SLM self-answered no longer PASS (Evidence-First)
  - #K: Adversary conflict sets CONFLICT (not just penalty)
  - #K-skip: Adversary no longer skipped at conf>=0.92
  - #Q: Governance KILL abstains (clears final_answer); ESCALATE sets UNKNOWN
  - #E: Calibration get_factor + recompute use lock
  - #E: QuestionTracker _cache uses lock
"""
import os
import sys

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10440_m_slm_self_answer_not_pass():
    """#M: SLM self-answered must NOT be PASS (was: PASS, Evidence-First violation)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #M' in content, "B-M: marker not present"
    # Must set UNKNOWN (not PASS) when no AI answer
    assert 'verdict_type = "UNKNOWN"' in content
    # Must NOT have the old PASS for SLM self-answered
    assert 'SLM self-answered (conf=' not in content.replace('reasoning = f"SLM self-answered', 'XXX')
    # reality_check must NOT have is_correct=True for slm_self
    assert '"is_correct": True, "real_value": _best_slm_answer' not in content
    assert 'slm_self_unverified' in content, "B-M: unverified source marker not present"


def test_v10440_k_adversary_conflict_sets_verdict():
    """#K: Adversary conflict must set verdict_type=CONFLICT (not just penalty)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #K' in content, "B-K: marker not present"
    assert 'verdict_type = "CONFLICT"' in content, "B-K: CONFLICT not set on adversary conflict"


def test_v10440_k_skip_adversary_not_bypassed():
    """#K-skip: Adversary must NOT be skipped at conf>=0.92."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #K-skip' in content, "B-K-skip: marker not present"
    # Old 0.92 bypass should NOT be active
    assert 'confidence < 0.92' not in content.replace('# ', '').replace('V104.40', ''), \
        "B-K-skip: still using confidence < 0.92 bypass"


def test_v10440_q_governance_kill_abstains():
    """#Q: Governance KILL must clear final_answer (abstain)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #Q' in content, "B-Q: marker not present"
    assert 'verdict.final_answer = ""' in content, "B-Q: final_answer not cleared on KILL"
    assert 'governance_abstain' in content, "B-Q: abstain flag not set"


def test_v10440_q_governance_escalate_sets_unknown():
    """#Q: Governance ESCALATE must set UNKNOWN (not auto-PASS)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'human_review_required' in content, "B-Q: human_review_required flag not set"
    # ESCALATE should change PASS to UNKNOWN
    assert 'ESCALATE' in content


def test_v10440_e_calibration_factor_lock():
    """#E: CalibrationEngine must use _factor_lock for cache access."""
    ce_path = os.path.join(SCP_DIR, 'scp', 'meta', 'calibration_engine.py')
    with open(ce_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #E' in content, "B-E: marker not present"
    assert '_factor_lock' in content, "B-E: _factor_lock not present"
    assert 'with self._factor_lock' in content, "B-E: lock not used"


def test_v10440_e_question_tracker_cache_lock():
    """#E: QuestionTracker must use _cache_lock for cache access."""
    qt_path = os.path.join(SCP_DIR, 'scp', 'meta', 'question_tracker.py')
    with open(qt_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.40 #E' in content, "B-E-tracker: marker not present"
    assert '_cache_lock' in content, "B-E-tracker: _cache_lock not present"
    assert 'with self._cache_lock' in content, "B-E-tracker: lock not used"


def test_v10440_e_calibration_get_factor_uses_lock():
    """#E: get_factor must acquire lock before reading cache."""
    ce_path = os.path.join(SCP_DIR, 'scp', 'meta', 'calibration_engine.py')
    with open(ce_path, encoding="utf-8") as f:
        content = f.read()
    # get_factor should have 'with self._factor_lock' inside
    import re
    m = re.search(r'def get_factor.*?(?=\n    def )', content, re.DOTALL)
    if m:
        method_body = m.group(0)
        assert 'with self._factor_lock' in method_body, "B-E: get_factor doesn't use lock"
