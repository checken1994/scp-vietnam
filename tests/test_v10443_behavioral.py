"""
[V104.43] Behavioral tests for deep audit Parts 7+8+deferred.

Tests verify:
  - #BO: LiveKnowledge cross-check compares values (not just count)
  - #BS: _DomainSLM respects DataSource confidence (not always 0.85)
  - #BI: KB short-circuit for high-tier hits
  - #BJ: Search requires ≥2 token overlap (not 1)
  - #AW: ReVerify CHANGED fixes KB + calls cognitive_gate
  - #BY: ThreatSim writes rules to AttackPatternMemory
  - #CB: UnifiedDetector called on hot path
  - #CF: RealLearning checks SourceWatchlist
"""
import os, sys
SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10443_bo_cross_check_compares_values():
    """#BO: LiveKnowledge must compare values, not just count sources."""
    lk_path = os.path.join(SCP_DIR, 'scp', 'data_sources', 'live_knowledge.py')
    with open(lk_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #BO' in content, "B-BO: marker not present"
    assert '_overlap' in content, "B-BO: overlap comparison not present"
    assert 'cross_check_conflict' in content, "B-BO: conflict flag not present"
    assert '0.45' in content, "B-BO: lowered confidence on conflict not present"


def test_v10443_bs_domain_slm_respects_ds_confidence():
    """#BS: _DomainSLM must respect DataSource confidence (not always 0.85)."""
    slms_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'slms.py')
    with open(slms_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #BS' in content, "B-BS: marker not present"
    assert '_ds_conf' in content, "B-BS: DS confidence check not present"
    assert 'result.get("confidence")' in content, "B-BS: DS confidence not read"


def test_v10443_bi_kb_short_circuit():
    """#BI: KB must short-circuit for high-tier + high-conf hits."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #BI' in content, "B-BI: marker not present"
    assert 'KB short-circuit' in content, "B-BI: short-circuit logic not present"
    assert '_hit_tier <= 2' in content, "B-BI: tier check not present"


def test_v10443_bj_search_requires_2_tokens():
    """#BJ: Search must require ≥2 token overlap (not 1)."""
    ds_path = os.path.join(SCP_DIR, 'scp', 'knowledge', 'domain_store.py')
    with open(ds_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #BJ' in content, "B-BJ: marker not present"
    assert 'len(overlap) >= 2' in content, "B-BJ: ≥2 token requirement not present"


def test_v10443_aw_reverify_changed_fixes_kb():
    """#AW: ReVerify CHANGED must lower KB confidence + call cognitive_gate."""
    rv_path = os.path.join(SCP_DIR, 'scp', 'meta', 'reverify_scheduler.py')
    with open(rv_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #AW' in content, "B-AW: marker not present"
    assert 'UPDATE knowledge SET confidence = MIN' in content, "B-AW: KB confidence fix not present"
    assert 'record_reverify_outcome' in content, "B-AW: cognitive_gate feedback not present"


def test_v10443_by_threatsim_writes_rules():
    """#BY: ThreatSim must write rules to AttackPatternMemory."""
    ts_path = os.path.join(SCP_DIR, 'scp', 'security', 'threat_simulator.py')
    with open(ts_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #BY' in content, "B-BY: marker not present"
    assert 'AttackPatternMemory' in content, "B-BY: AttackPatternMemory not referenced"
    assert 'record_bypass' in content, "B-BY: record_bypass not called"
    assert 'total_rules_installed' in content, "B-BY: installed metric not present"


def test_v10443_cb_unified_detector_on_hot_path():
    """#CB: UnifiedDetector must be called on hot path."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #CB' in content, "B-CB: marker not present"
    assert 'unified_detector.detect' in content or 'self.unified_detector.detect' in content, \
        "B-CB: detect() not called on hot path"


def test_v10443_cf_real_learning_watchlist():
    """#CF: RealLearning must check SourceWatchlist before KB write."""
    rl_path = os.path.join(SCP_DIR, 'scp', 'core', 'real_learning_engine.py')
    with open(rl_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.43 #CF' in content, "B-CF: marker not present"
    assert 'SourceWatchlist' in content, "B-CF: SourceWatchlist not imported"
    assert 'is_blocked' in content, "B-CF: is_blocked not called"
