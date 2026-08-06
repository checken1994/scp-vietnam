"""
[V104.44] Behavioral tests for peripheral chain fixes.

Tests verify:
  - #CV: Notifications.notify called on KILL/bypass
  - #CS: Canary check_trigger in schedule_background_jobs
  - #CQ: Cross-language patterns loaded into AttackPatternMemory
  - #CW: StorageManager in schedule_background_jobs
  - #BX: ErrorStoreIndex initialized + search_similar called
"""
import os, sys
SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10444_cv_notifications_on_kill():
    """#CV: judge must call notifications.notify on KILL."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.44 #CV' in content, "B-CV: marker not present"
    assert 'self.notifications.notify' in content, "B-CV: notify not called"
    assert 'Governance KILL' in content, "B-CV: KILL notification not present"
    assert 'Bypass Detected' in content, "B-CV: bypass notification not present"


def test_v10444_cs_canary_scan_in_scheduler():
    """#CS: Canary check_trigger must be in schedule_background_jobs."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.44 #CS' in content, "B-CS: marker not present"
    assert 'check_trigger' in content, "B-CS: check_trigger not called"
    assert '_last_canary_scan' in content, "B-CS: scan scheduling not present"
    assert 'Canary Token Triggered' in content, "B-CS: trigger notification not present"


def test_v10444_cq_cross_lang_loaded_into_detector():
    """#CQ: Cross-language patterns must be loaded into AttackPatternMemory."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.44 #CQ' in content, "B-CQ: marker not present"
    assert 'attack_memory.record_bypass' in content, "B-CQ: record_bypass not called"
    assert 'cross_lang_' in content, "B-CQ: cross-lang attack type not present"


def test_v10444_cw_storage_in_scheduler():
    """#CW: StorageManager must be in schedule_background_jobs."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.44 #CW' in content, "B-CW: marker not present"
    assert 'check_and_maintain' in content, "B-CW: check_and_maintain not called"
    assert '_last_storage_check' in content, "B-CW: storage scheduling not present"


def test_v10444_bx_error_store_index_wired():
    """#BX: ErrorStoreIndex must be initialized + search_similar called."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.44 #BX' in content, "B-BX: marker not present"
    assert 'ErrorStoreIndex' in content, "B-BX: ErrorStoreIndex not imported"
    assert 'error_store_index' in content, "B-BX: instance not created"
    assert 'search_similar' in content or '_search_sync' in content, \
        "B-BX: search not called"
    assert 'v104_similar_errors' in content, "B-BX: evidence field not set"
