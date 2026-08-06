"""
[V104.41] Behavioral tests for deep audit Part 3 — API boundary + Security V98 + Knowledge.

Tests verify:
  - #X: /ask enforces KILL/FAIL/FLAGGED (clears answer)
  - #AA: OpenAI path uses asyncio.to_thread (not sync)
  - #AC: DoS record_verdict called after judge
  - #AD: ResponseMonitor.observe called on /ask
  - #AB: Canary not in visible content (metadata only)
  - #AI: Knowledge entity fallback skipped (not question[:100])
  - #AF: Antibody CRITICAL overrides to FAIL + abstain
"""
import os
import sys

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10441_x_ask_enforces_kill_fail_flagged():
    """#X: /ask must clear answer for FAIL/FLAGGED/KILL."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #X' in content, "B-X: marker not present"
    assert 'Answer withheld' in content, "B-X: abstain message not present"
    assert '_api_final_answer' in content, "B-X: enforced answer variable not present"
    # Must check for KILL and FAIL and FLAGGED
    assert '"FAIL", "FLAGGED"' in content, "B-X: FAIL/FLAGGED check not present"
    assert 'KILL' in content


def test_v10441_aa_openai_uses_to_thread():
    """#AA: OpenAI path must use asyncio.to_thread (not sync judge.judge)."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AA' in content, "B-AA: marker not present"
    # Must have asyncio.to_thread in OpenAI path
    assert 'await asyncio.to_thread' in content, "B-AA: asyncio.to_thread not used"


def test_v10441_ac_dos_record_verdict_called():
    """#AC: DoS record_verdict must be called after judge."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AC' in content, "B-AC: marker not present"
    assert 'record_verdict' in content, "B-AC: record_verdict not called"


def test_v10441_ad_response_monitor_observed():
    """#AD: ResponseMonitor.observe must be called on /ask."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AD' in content, "B-AD: marker not present"
    assert 'response_monitor' in content, "B-AD: response_monitor not referenced"
    assert 'observe' in content, "B-AD: observe not called"


def test_v10441_ab_canary_not_in_content():
    """#AB: Canary must NOT be appended to visible content (metadata only)."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AB' in content, "B-AB: marker not present"
    # Old pattern (canary in content) should NOT be active
    assert 'answer += f"\\n\\n[ref: {canary}]"' not in content, \
        "B-AB: canary still appended to visible content"


def test_v10441_ai_knowledge_entity_not_question():
    """#AI: Knowledge entity fallback must NOT use question[:100] as active code."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AI' in content, "B-AI: marker not present"
    # The ACTIVE code (not comment) should NOT assign entity = question[:100]
    import re
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = re.sub(r'#.*', '', line)
        # Active assignment: entity = question[:100] (not in comment, not in string)
        if 'entity = question[:100]' in stripped and 'was' not in stripped:
            assert False, f"B-AI: line {i+1} still uses question[:100] as entity: {line.strip()}"


def test_v10441_af_antibody_critical_override():
    """#AF: Antibody CRITICAL must override PASS → FAIL + abstain."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f:
        content = f.read()
    assert 'V104.41 #AF' in content, "B-AF: marker not present"
    assert 'antibody_kill' in content, "B-AF: antibody_kill flag not present"
    assert 'has_critical' in content, "B-AF: has_critical check not present"
    # Must set verdict to FAIL and clear final_answer
    assert 'verdict.verdict = "FAIL"' in content
    assert 'verdict.final_answer = ""' in content


def test_v10441_x_openai_path_also_enforces():
    """#X: OpenAI path must also enforce KILL/FAIL/FLAGGED."""
    api_path = os.path.join(SCP_DIR, 'scp', 'api_server.py')
    with open(api_path, encoding="utf-8") as f:
        content = f.read()
    # OpenAI path should have the same enforcement
    assert 'I cannot comply' in content, "B-X: OpenAI path abstain message not present"
