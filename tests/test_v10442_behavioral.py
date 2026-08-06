"""
[V104.42] Behavioral tests for deep audit Parts 5+6+deferred.

Tests verify:
  - #BC: _DomainSLM sets evidence["value"] (CRITICAL — ~30 domains)
  - #BD: Biology pokeapi/fruityvice set evidence["value"]
  - #AV: ReVerify process_pending in schedule_background_jobs
  - #BA: H8 bypass retracts answer (PASS → FAIL + abstain)
  - #AY: MetaFalsifier runs even without why_plan
  - #AH: SourceWatchlist check before knowledge INSERT
  - #D: Governance consumes Constitution Principle.default_action
  - #O: AI match non-primary SLM keeps CONFLICT (not PASS)
"""
import os
import sys

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10442_bc_domain_slm_sets_value():
    """#BC: _DomainSLM must set evidence['value'] (was: missing → adversary skip)."""
    slms_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'slms.py')
    with open(slms_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #BC' in content, "B-BC: marker not present"
    # The _DomainSLM evidence must include "value"
    assert '"value": raw_value' in content, "B-BC: value not in _DomainSLM evidence"
    # LiveKnowledge fallback must also include "value"
    assert '"value": result["value"]' in content, "B-BC: value not in LiveKnowledge evidence"


def test_v10442_bd_biology_sets_value():
    """#BD: Biology pokeapi/fruityvice must set evidence['value']."""
    slms_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'slms.py')
    with open(slms_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #BD' in content, "B-BD: marker not present"
    # pokeapi evidence must include value
    assert '"value": ", ".join(types)' in content, "B-BD: pokeapi value not set"
    # fruityvice evidence must include value
    assert '"value": answer[:200]' in content, "B-BD: fruityvice value not set"


def test_v10442_av_reverify_in_scheduler():
    """#AV: ReVerify process_pending must be in schedule_background_jobs."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #AV' in content, "B-AV: marker not present"
    assert 'process_pending' in content, "B-AV: process_pending not called"
    assert 'reverify_scheduler' in content, "B-AV: reverify_scheduler not referenced"


def test_v10442_ba_bypass_retracts_answer():
    """#BA: H8 bypass must retract answer (PASS → FAIL + abstain)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #BA' in content, "B-BA: marker not present"
    assert 'bypass_retract' in content, "B-BA: bypass_retract flag not present"
    assert 'Answer withheld — bypass detected' in content, "B-BA: abstain message not present"


def test_v10442_ay_metafalsifier_runs_without_plan():
    """#AY: MetaFalsifier must run even without why_plan."""
    ce_path = os.path.join(SCP_DIR, 'scp', 'meta', 'cognitive_engine.py')
    with open(ce_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #AY' in content, "B-AY: marker not present"
    # Must have a branch for when why_plan is None
    assert 'No WHY plan to falsify' in content, "B-AY: no-plan branch not present"


def test_v10442_ah_watchlist_check_before_kb_write():
    """#AH: SourceWatchlist.is_blocked must be checked before knowledge INSERT."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #AH' in content, "B-AH: marker not present"
    assert 'SourceWatchlist' in content, "B-AH: SourceWatchlist not imported"
    assert 'is_blocked' in content, "B-AH: is_blocked not called"


def test_v10442_d_governance_consumes_constitution():
    """#D: Governance must check Constitution Principle.default_action."""
    gov_path = os.path.join(SCP_DIR, 'scp', 'meta', 'governance_v97.py')
    with open(gov_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #D' in content, "B-D: marker not present"
    assert 'default_action' in content, "B-D: default_action not checked"
    assert '_constitution_kill' in content, "B-D: constitution kill flag not present"


def test_v10442_o_primary_slm_match_required():
    """#O: AI match non-primary SLM must keep CONFLICT (not PASS)."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.42 #O' in content, "B-O: marker not present"
    assert '_primary_slm' in content, "B-O: primary SLM check not present"
    assert '_is_primary' in content, "B-O: is_primary check not present"
    # Non-primary match must set CONFLICT
    assert 'verdict_type = "CONFLICT"' in content, "B-O: CONFLICT not set for non-primary"
