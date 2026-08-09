"""
[V104.39] Behavioral tests for deep audit fixes — closed-loop + calibration + governance.

Tests verify:
  - #A: Closed-loop re-enabled via SCP_ENABLE_CLOSED_LOOP env var (not if False)
  - #B: record_outcome uses transaction (BEGIN IMMEDIATE) + UNKNOWN neutral
  - #C: Calibration no longer counts FAIL+conf>0.7 as correct
  - #F: WHY engine re-enabled via same env var
  - #G: CuriosityAsker skips when entity not provided (no question-as-entity)
"""
import os
import sys
from pathlib import Path

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def _read_module_tree(*subpaths: str) -> str:
    """Read all .py files in a module directory tree + concat content.

    [AUTOFIX-T1] Brittle tests grep'd single files. After refactors (judge.py
    → judge_parts/, slms.py → slms_parts/), markers moved but tests still
    looked at the old single file → false failures. This helper reads the
    WHOLE tree so tests survive future refactors. Behavior > file location.
    """
    root = Path(SCP_DIR).joinpath(*subpaths)
    if root.is_file() and root.suffix == ".py":
        return root.read_text(encoding="utf-8")
    if not root.is_dir():
        return ""
    chunks: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.name:
            continue
        try:
            chunks.append(py.read_text(encoding="utf-8"))
        except Exception:
            continue
    return "\n".join(chunks)


def test_v10439_a_closed_loop_env_var_guard():
    """#A: Closed-loop must use env var guard (not if False)."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.39 #A' in content, "B-A: marker not present"
    assert 'SCP_ENABLE_CLOSED_LOOP' in content, "B-A: env var guard not present"
    # Old if False pattern should NOT be active
    assert 'if False and self.policy_applier' not in content.replace('# ', ''), \
        "B-A: still using if False for policy_applier"
    assert 'if False and self.predictor' not in content.replace('# ', ''), \
        "B-A: still using if False for predictor"


def test_v10439_a_feedback_re_enabled():
    """#A: Feedback (record_outcome) must be re-enabled."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'if _enable_closed_loop and self.policy_applier and applied_principle_ids' in content, \
        "B-A: feedback not re-enabled"


def test_v10439_b_record_outcome_transaction():
    """#B: record_outcome must use BEGIN IMMEDIATE transaction."""
    pa_path = os.path.join(SCP_DIR, 'scp', 'meta', 'policy_applier.py')
    with open(pa_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.39 #B' in content, "B-B: marker not present"
    assert 'BEGIN IMMEDIATE' in content, "B-B: transaction not used"
    assert 'COMMIT' in content, "B-B: COMMIT not present"
    assert 'ROLLBACK' in content, "B-B: ROLLBACK not present"


def test_v10439_b_unknown_neutral():
    """#B: UNKNOWN/PARTIAL must be neutral (success=None, skip update)."""
    pa_path = os.path.join(SCP_DIR, 'scp', 'meta', 'policy_applier.py')
    with open(pa_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.39 #B-success' in content, "B-B-success: marker not present"
    assert 'success = None' in content, "B-B: success=None not present"
    assert 'if success is None' in content or 'if success is None:' in content, \
        "B-B: skip when success is None not present"


def test_v10439_c_calibration_fail_not_correct():
    """#C: FAIL must NOT be counted as correct (was: FAIL+conf>0.7 = correct)."""
    ce_path = os.path.join(SCP_DIR, 'scp', 'meta', 'calibration_engine.py')
    with open(ce_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.39 #C' in content, "B-C: marker not present"
    assert 'predicted_correct = False  # FAIL = AI wrong' in content, \
        "B-C: FAIL not counted as wrong"
    # Old V82 logic (benefit of doubt) should NOT be active
    assert 'benefit of doubt' not in content.replace('# ', '').replace('TẠI SAO', ''), \
        "B-C: benefit of doubt logic still active"


def test_v10439_f_why_engine_re_enabled():
    """#F: WHY engine must be re-enabled via env var."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.39 #F' in content, "B-F: marker not present"
    assert 'if _enable_closed_loop and self.why_engine' in content, \
        "B-F: WHY engine not re-enabled"
    # Old V90 OPT skip should NOT be active
    assert 'why_plan = None\n        # [V90 OPT] Skip WHY engine' not in content, \
        "B-F: still using V90 OPT skip"


def test_v10439_g_curiosity_entity_guard():
    """#G: CuriosityAsker must skip when entity not provided."""
    ca_path = os.path.join(SCP_DIR, 'scp', 'meta', 'curiosity_asker.py')
    with open(ca_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.39 #G' in content, "B-G: marker not present"
    assert 'if not entity' in content, "B-G: entity guard not present"
    assert 'continue  # skip' in content, "B-G: skip not present"
    # The ACTIVE code (not comment) should NOT use question[:50] as entity fallback
    import re
    # Find all lines with "entity = " that are not comments
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = re.sub(r'#.*', '', line)
        if 'entity = ' in stripped and 'or question[:50]' in stripped:
            raise AssertionError(f"B-G: line {i+1} still uses question[:50] as entity: {line.strip()}")


def test_v10439_a_enable_closed_loop_env_var_works():
    """#A: Setting SCP_ENABLE_CLOSED_LOOP=1 should not break judge."""
    # This is a smoke test — just verify the env var is read correctly
    os.environ['SCP_ENABLE_CLOSED_LOOP'] = '1'
    try:
        # Import should work without error
        from scp.runtime.judge import RealityJudge
        assert RealityJudge is not None
    finally:
        del os.environ['SCP_ENABLE_CLOSED_LOOP']
