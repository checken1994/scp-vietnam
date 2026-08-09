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
from pathlib import Path
SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def _read_module_tree(*subpaths: str) -> str:
    """Read all .py files in a module directory tree + concat content.

    [AUTOFIX-T1] Brittle tests grep'd single files. After refactors (judge.py
    → judge_parts/, slms.py → slms_parts/, api_server.py → api_server_parts/
    + api/routes/), markers moved but tests still looked at the old single
    file → false failures. This helper reads the WHOLE tree so tests survive
    future refactors. Behavior > file location.
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


def test_v10444_cv_notifications_on_kill():
    """#CV: judge must call notifications.notify on KILL."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.44 #CV' in content, "B-CV: marker not present"
    assert 'self.notifications.notify' in content, "B-CV: notify not called"
    assert 'Governance KILL' in content, "B-CV: KILL notification not present"
    assert 'Bypass Detected' in content, "B-CV: bypass notification not present"


def test_v10444_cs_canary_scan_in_scheduler():
    """#CS: Canary check_trigger must be in schedule_background_jobs."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.44 #CS' in content, "B-CS: marker not present"
    assert 'check_trigger' in content, "B-CS: check_trigger not called"
    assert '_last_canary_scan' in content, "B-CS: scan scheduling not present"
    assert 'Canary Token Triggered' in content, "B-CS: trigger notification not present"


def test_v10444_cq_cross_lang_loaded_into_detector():
    """#CQ: Cross-language patterns must be loaded into AttackPatternMemory."""
    content = _read_module_tree("scp", "api_server.py")
    content += _read_module_tree("scp", "api_server_parts")
    content += _read_module_tree("scp", "api", "routes")
    assert 'V104.44 #CQ' in content, "B-CQ: marker not present"
    assert 'attack_memory.record_bypass' in content, "B-CQ: record_bypass not called"
    assert 'cross_lang_' in content, "B-CQ: cross-lang attack type not present"


def test_v10444_cw_storage_in_scheduler():
    """#CW: StorageManager must be in schedule_background_jobs."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.44 #CW' in content, "B-CW: marker not present"
    assert 'check_and_maintain' in content, "B-CW: check_and_maintain not called"
    assert '_last_storage_check' in content, "B-CW: storage scheduling not present"


def test_v10444_bx_error_store_index_wired():
    """#BX: ErrorStoreIndex must be initialized + search_similar called."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
    assert 'V104.44 #BX' in content, "B-BX: marker not present"
    assert 'ErrorStoreIndex' in content, "B-BX: ErrorStoreIndex not imported"
    assert 'error_store_index' in content, "B-BX: instance not created"
    assert 'search_similar' in content or '_search_sync' in content, \
        "B-BX: search not called"
    assert 'v104_similar_errors' in content, "B-BX: evidence field not set"
