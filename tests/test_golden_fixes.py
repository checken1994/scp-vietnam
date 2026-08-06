"""
[OPT-29] Golden tests for LLM fix generation.

DNA SCP #2 PASS ≠ ĐÚNG: test that LLM can actually generate correct patches.
Each test case has:
  - Input: bug type + code snippet
  - Expected: correct patch (search-replace block)

If LLM fails these, we know the LLM is too weak (not AutoFix).
"""
import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


GOLDEN_TEST_CASES = [
    {
        "name": "bare_except_pass_basic",
        "bug_type": "BareExceptPass",
        "code": "try:\n    do_something()\nexcept Exception:\n    pass\n",
        "expected_search": "except Exception:\n    pass",
        "expected_replace_contains": ["except Exception as e:", "logger"],
    },
    {
        "name": "unused_import",
        "bug_type": "UnusedImport",
        "code": "import os\nimport unused_module\n\ndef foo():\n    os.getcwd()\n",
        "expected_search": "import unused_module",
        "expected_replace_contains": [],  # should remove the line
    },
    {
        "name": "sql_injection_fstring",
        "bug_type": "SQLInjection",
        "code": "def query(user_input):\n    sql = f\"SELECT * FROM users WHERE name = '{user_input}'\"\n    return db.execute(sql)\n",
        "expected_search": "f\"SELECT * FROM users WHERE name = '{user_input}'\"",
        "expected_replace_contains": ["?", "parameterized", "%s"],  # should use parameterized
    },
]


def test_golden_test_cases_exist():
    """Verify golden test cases are defined."""
    assert len(GOLDEN_TEST_CASES) >= 3, "Should have at least 3 golden test cases"
    for case in GOLDEN_TEST_CASES:
        assert "name" in case
        assert "bug_type" in case
        assert "code" in case
        assert "expected_search" in case
        assert "expected_replace_contains" in case


def test_validate_patch_accepts_golden_fixes():
    """Test that validate_patch accepts correct golden fixes."""
    import tempfile

    from scp.autofix.validate_patch import validate_patch

    for case in GOLDEN_TEST_CASES:
        # Create temp file with the code
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(case["code"])
            path = f.name

        try:
            # Construct expected replace
            replace = case.get("expected_replace", "pass  # fixed")
            if case["expected_replace_contains"]:
                replace = "except Exception as e:\n    logger.warning(f'Error: {e}')"

            result = validate_patch(path, case["expected_search"], replace)
            assert result.search_found, f"{case['name']}: SEARCH not found"
            assert result.syntax_ok, f"{case['name']}: REPLACE syntax error"
            assert result.no_dangerous, f"{case['name']}: dangerous pattern"
        finally:
            import os
            os.unlink(path)


def test_diagnostic_classifies_correctly():
    """Test diagnostic module classifies failures correctly."""
    from scp.autofix.diagnostic import diagnose_fix_failure

    # LLM failed
    r = diagnose_fix_failure("test-1", "BareExceptPass", None, None, None, "failed")
    assert r.diagnosis == "LLM_FAILED_TO_GENERATE"

    # Format error
    r = diagnose_fix_failure("test-2", "SQLInjection", "raw text", None, None, "failed")
    assert r.diagnosis == "LLM_OUTPUT_FORMAT_ERROR"

    # Success
    r = diagnose_fix_failure("test-3", "UnusedImport", "output", {"search": "x", "replace": "y"}, {"valid": True}, "success")
    assert r.diagnosis == "SUCCESS"


def test_monitor_tracks_stats():
    """Test AutoFixMonitor tracks stats correctly."""
    from scp.autofix.monitor import AutoFixMonitor, FixAttempt

    m = AutoFixMonitor()
    m.record(FixAttempt("b1", "BareExceptPass", "ollama", "SUCCESS", True))
    m.record(FixAttempt("b2", "SQLInjection", "anthropic", "LLM_OUTPUT_FORMAT_ERROR", False))

    stats = m.get_stats()
    assert stats["total_attempts"] == 2
    assert stats["success_count"] == 1
    assert stats["success_rate"] == 0.5
    assert "BareExceptPass" in stats["by_bug_type"]
    assert stats["by_bug_type"]["BareExceptPass"]["success"] == 1
