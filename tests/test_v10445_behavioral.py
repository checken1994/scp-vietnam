"""
[V104.45] Behavioral tests for final deferred bugs: BV, BW, CP, CR, Z.
"""
import os, sys
<<<<<<< HEAD
=======
from pathlib import Path
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


<<<<<<< HEAD
def test_v10445_bv_healing_on_ask_path():
    """#BV: Healing engine must be initialized in judge and called after verdict."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
=======
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


def test_v10445_bv_healing_on_ask_path():
    """#BV: Healing engine must be initialized in judge and called after verdict."""
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
    assert 'V104.45 #BV' in content, "B-BV: marker not present"
    assert 'V14SelfHealingEngine' in content, "B-BV: healing engine not imported"
    assert 'self.healing_engine' in content, "B-BV: instance not created"
    assert 'healing_engine.monitor' in content, "B-BV: monitor not called"
    assert 'healing_engine.heal' in content, "B-BV: heal not called"
    assert 'healing_actions' in content, "B-BV: evidence field not set"


def test_v10445_bw_healing_success_measures_verdict():
    """#BW: Healing success must measure verdict improvement, not SQL DELETE."""
<<<<<<< HEAD
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
=======
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
    assert 'V104.45 #BW' in content, "B-BW: marker not present"
    assert 'verdict_improved' in content, "B-BW: verdict_improved not present"


def test_v10445_cp_multimodal_on_ask():
    """#CP: /ask must support image_url + voice_url for multimodal detection."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.45 #CP' in content, "B-CP: marker not present"
    assert 'image_url' in content, "B-CP: image_url field not present"
    assert 'voice_url' in content, "B-CP: voice_url field not present"
    assert '_multimodal_block' in content, "B-CP: multimodal block not present"
    assert 'multimodal jailbreak detected' in content, "B-CP: block message not present"


def test_v10445_cr_autopayload_scheduled():
    """#CR: AutoPayloadGenerator must be scheduled in background."""
<<<<<<< HEAD
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
=======
    content = _read_module_tree("scp", "runtime", "judge.py")
    content += _read_module_tree("scp", "runtime", "judge_parts")
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
    assert 'V104.45 #CR' in content, "B-CR: marker not present"
    assert 'AutoPayloadGenerator' in content, "B-CR: not imported"
    assert '_last_payload_gen' in content, "B-CR: scheduling not present"


def test_v10445_z_fact_check_retract_queue():
    """#Z: Fact-check must queue retract when FALSE claims found."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f: content = f.read()
    assert 'V104.45 #Z' in content, "B-Z: marker not present"
    assert '_fact_check_retract_queue' in content, "B-Z: retract queue not present"
    assert 'queuing retract' in content, "B-Z: retract message not present"


def test_v10445_closed_loop_staging():
    """SCP_ENABLE_CLOSED_LOOP=1 must not break any tests."""
    # This is a smoke test — the real staging test runs the full suite
    # with SCP_ENABLE_CLOSED_LOOP=1 env var set.
    os.environ['SCP_ENABLE_CLOSED_LOOP'] = '1'
    try:
        from scp.runtime.judge import RealityJudge
        assert RealityJudge is not None
    finally:
        del os.environ['SCP_ENABLE_CLOSED_LOOP']
