"""[V8.0-WHY] Test for 3 new WHY modes in scp/meta/why_engine.py + wiring in evolution.py.

Tests:
  1. type_inference_why  — env OFF (defensive default) + env ON (mocked LLM)
  2. data_flow_why       — env OFF + env ON + invalid JSON fallback
  3. security_threat_why — env OFF + env ON + CWE normalization
  4. evolution._v80_why_deep_analysis — dispatch by bug type
  5. evolution._v80_extract_* helpers — regex extraction

Run: cd <repo-root> && python -m pytest tests/test_v80_why_modes.py -v
  OR: python tests/test_v80_why_modes.py
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure scp package is importable when run as `python tests/test_v80_why_modes.py`
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scp.autofix.classifier import BugReport, BugTier
from scp.autofix.evolution import EvolutionEngine
from scp.meta.why_engine import WhyEngine

# Aliases for clarity in tests — these are the enum values, not strings.
T1 = BugTier.TIER_1_AUTO_FIX
T3 = BugTier.TIER_3_PERMISSION

# [SANDBOX FIX] Skip WhyEngine DB init — sandbox filesystem (ossfs) doesn't
# support SQLite WAL mode (causes "disk I/O error" on CREATE TABLE).
# Tests below don't need DB persistence — they only test the v8.0 WHY methods
# which are pure LLM-call + JSON-parse logic. Patching __init__ to no-op
# keeps the test focused on WHY logic, not DB plumbing.
WhyEngine.__init__ = lambda self: None


class TestTypeInferenceWhy(unittest.TestCase):
    """[V8.0-WHY] Mode 1: type_inference_why."""

    def setUp(self):
        # Ensure env is OFF by default for each test.
        for k in ("SCP_WHY_TYPE_INFERENCE", "SCP_WHY_DATA_FLOW", "SCP_WHY_SECURITY_THREAT"):
            os.environ.pop(k, None)

    def test_env_off_returns_defensive_default(self):
        """When SCP_WHY_TYPE_INFERENCE != 1, returns default dict, llm_used=False."""
        we = WhyEngine()
        result = we.type_inference_why("result", "int", "dict", "def f() -> int: return {}")
        self.assertIsInstance(result, dict)
        self.assertIn("why_question", result)
        self.assertIn("root_cause", result)
        self.assertIn("fix_direction", result)
        self.assertIn("confidence", result)
        self.assertIn("llm_used", result)
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["fix_direction"], "add type check")
        self.assertEqual(result["root_cause"], "")
        self.assertIn("result", result["why_question"])
        self.assertIn("int", result["why_question"])
        self.assertIn("dict", result["why_question"])

    def test_env_on_with_mocked_llm_parses_json(self):
        """When env=1 and LLM returns valid JSON, returns parsed result."""
        os.environ["SCP_WHY_TYPE_INFERENCE"] = "1"
        we = WhyEngine()
        mock_response = (
            '{"root_cause": "Function returns dict but caller expects int — API changed.", '
            '"fix_direction": "change caller", "confidence": 0.85}'
        )
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response) as mock_call:
            result = we.type_inference_why("result", "int", "dict", "def f(): return {}")
        mock_call.assert_called_once()
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["root_cause"], "Function returns dict but caller expects int — API changed.")
        self.assertEqual(result["fix_direction"], "change caller")
        self.assertAlmostEqual(result["confidence"], 0.85)
        self.assertIn("result", result["why_question"])

    def test_env_on_invalid_json_falls_back_to_default(self):
        """When env=1 but LLM returns garbage, falls back to default."""
        os.environ["SCP_WHY_TYPE_INFERENCE"] = "1"
        we = WhyEngine()
        with patch.object(we, "_v80_why_llm_call", return_value="not json at all"):
            result = we.type_inference_why("x", "int", "str", "code")
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["confidence"], 0.0)

    def test_env_on_invalid_fix_direction_normalized(self):
        """LLM returns bogus fix_direction → normalized to 'add type check'."""
        os.environ["SCP_WHY_TYPE_INFERENCE"] = "1"
        we = WhyEngine()
        mock_response = '{"root_cause": "x", "fix_direction": "REWRITE EVERYTHING", "confidence": 0.5}'
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.type_inference_why("x", "int", "str", "code")
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["fix_direction"], "add type check")

    def test_env_on_confidence_clamped_to_range(self):
        """LLM returns confidence > 1.0 → clamped to 1.0; < 0.0 → 0.0."""
        os.environ["SCP_WHY_TYPE_INFERENCE"] = "1"
        we = WhyEngine()
        mock_response = '{"root_cause": "x", "fix_direction": "change function", "confidence": 99.0}'
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.type_inference_why("x", "int", "str", "code")
        self.assertEqual(result["confidence"], 1.0)


class TestDataFlowWhy(unittest.TestCase):
    """[V8.0-WHY] Mode 2: data_flow_why."""

    def setUp(self):
        for k in ("SCP_WHY_TYPE_INFERENCE", "SCP_WHY_DATA_FLOW", "SCP_WHY_SECURITY_THREAT"):
            os.environ.pop(k, None)

    def test_env_off_returns_defensive_default(self):
        we = WhyEngine()
        result = we.data_flow_why(
            source="request.body", sink="SQL query",
            path=["parse_json", "build_query"], context="code",
        )
        self.assertFalse(result["llm_used"])
        self.assertTrue(result["sanitize_missing"])  # conservative default
        self.assertEqual(result["risk_level"], "high")
        self.assertIn("add validation", result["fix_direction"])
        self.assertEqual(result["confidence"], 0.0)
        self.assertIn("request.body", result["why_question"])
        self.assertIn("SQL query", result["why_question"])

    def test_env_on_with_mocked_llm_parses_json(self):
        os.environ["SCP_WHY_DATA_FLOW"] = "1"
        we = WhyEngine()
        mock_response = (
            '{"sanitize_missing": true, "risk_level": "high", '
            '"fix_direction": "add parameterized query at SQL step", "confidence": 0.92}'
        )
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.data_flow_why("user_input", "SQL", ["build_query"], "code")
        self.assertTrue(result["llm_used"])
        self.assertTrue(result["sanitize_missing"])
        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["fix_direction"], "add parameterized query at SQL step")
        self.assertAlmostEqual(result["confidence"], 0.92)

    def test_env_on_invalid_risk_level_normalized(self):
        """LLM returns bogus risk_level → normalized to 'high'."""
        os.environ["SCP_WHY_DATA_FLOW"] = "1"
        we = WhyEngine()
        mock_response = '{"sanitize_missing": false, "risk_level": "CRITICAL", "fix_direction": "x", "confidence": 0.5}'
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.data_flow_why("u", "s", [], "c")
        self.assertEqual(result["risk_level"], "high")

    def test_env_on_llm_returns_none(self):
        """LLM call returns None (e.g. no API key) → falls back to default."""
        os.environ["SCP_WHY_DATA_FLOW"] = "1"
        we = WhyEngine()
        with patch.object(we, "_v80_why_llm_call", return_value=None):
            result = we.data_flow_why("u", "s", [], "c")
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["confidence"], 0.0)

    def test_empty_path_renders_in_question(self):
        we = WhyEngine()
        result = we.data_flow_why("u", "s", [], "c")
        self.assertIn("no intermediate", result["why_question"])


class TestSecurityThreatWhy(unittest.TestCase):
    """[V8.0-WHY] Mode 3: security_threat_why."""

    def setUp(self):
        for k in ("SCP_WHY_TYPE_INFERENCE", "SCP_WHY_DATA_FLOW", "SCP_WHY_SECURITY_THREAT"):
            os.environ.pop(k, None)

    def test_env_off_returns_defensive_default(self):
        we = WhyEngine()
        result = we.security_threat_why(
            code_pattern="cursor.execute(f'SELECT * FROM u WHERE id={user_id}')",
            cwe_id="CWE-89", context="context",
        )
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["risk_after_fix"], "high")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["attack_vector"], "")
        self.assertEqual(result["fix_verify"], "")
        self.assertIn("CWE-89", result["why_question"])

    def test_env_on_with_mocked_llm_parses_json(self):
        os.environ["SCP_WHY_SECURITY_THREAT"] = "1"
        we = WhyEngine()
        mock_response = (
            '{"attack_vector": "Attacker injects SQL via user_id param to dump users table.", '
            '"fix_verify": "Fix uses parameterized query — safe, no new vuln.", '
            '"risk_after_fix": "low", "confidence": 0.88}'
        )
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.security_threat_why("cursor.execute(f'...')", "CWE-89", "ctx")
        self.assertTrue(result["llm_used"])
        self.assertIn("Attacker", result["attack_vector"])
        self.assertIn("parameterized", result["fix_verify"])
        self.assertEqual(result["risk_after_fix"], "low")
        self.assertAlmostEqual(result["confidence"], 0.88)

    def test_cwe_id_normalization_variants(self):
        """CWE id accepted in many formats: 'CWE-89', 'cwe-89', '89', 'CWE89'."""
        we = WhyEngine()
        for cwe_input, expected in [("CWE-89", "89"), ("cwe-89", "89"),
                                     ("89", "89"), ("CWE89", "89")]:
            self.assertEqual(we._v80_normalize_cwe(cwe_input), expected,
                             f"Failed for input: {cwe_input!r}")
        # Empty / no digits → "0"
        self.assertEqual(we._v80_normalize_cwe(""), "0")
        self.assertEqual(we._v80_normalize_cwe("CWE-XYZ"), "0")

    def test_env_on_invalid_risk_after_fix_normalized(self):
        """LLM returns bogus risk_after_fix → normalized to 'high'."""
        os.environ["SCP_WHY_SECURITY_THREAT"] = "1"
        we = WhyEngine()
        mock_response = '{"attack_vector": "x", "fix_verify": "y", "risk_after_fix": "EXTREME", "confidence": 0.5}'
        with patch.object(we, "_v80_why_llm_call", return_value=mock_response):
            result = we.security_threat_why("code", "CWE-89", "ctx")
        self.assertEqual(result["risk_after_fix"], "high")

    def test_cwe_question_uses_normalized_id(self):
        """why_question always shows 'CWE-{digits}' regardless of input format."""
        we = WhyEngine()
        for cwe_input in ["CWE-89", "cwe-89", "89"]:
            result = we.security_threat_why("code", cwe_input, "ctx")
            self.assertIn("CWE-89", result["why_question"],
                          f"Failed for input: {cwe_input!r}")


class TestWhyJsonExtractionHelper(unittest.TestCase):
    """[V8.0-WHY] _v80_why_extract_json — handles LLM output variants."""

    def test_plain_json(self):
        we = WhyEngine()
        out = we._v80_why_extract_json('{"a": 1, "b": "x"}')
        self.assertEqual(out, {"a": 1, "b": "x"})

    def test_json_with_explanatory_text(self):
        we = WhyEngine()
        out = we._v80_why_extract_json('Here is the analysis:\n{"a": 1}\nDone.')
        self.assertEqual(out, {"a": 1})

    def test_json_with_nested_object(self):
        we = WhyEngine()
        out = we._v80_why_extract_json('{"outer": {"inner": 1}, "x": 2}')
        self.assertEqual(out, {"outer": {"inner": 1}, "x": 2})

    def test_invalid_json_returns_none(self):
        we = WhyEngine()
        self.assertIsNone(we._v80_why_extract_json("not json"))
        self.assertIsNone(we._v80_why_extract_json(""))
        self.assertIsNone(we._v80_why_extract_json(None))

    def test_markdown_fenced_json(self):
        we = WhyEngine()
        out = we._v80_why_extract_json('```json\n{"a": 1}\n```')
        self.assertEqual(out, {"a": 1})


class TestEvolutionV80Dispatch(unittest.TestCase):
    """[V8.0-WHY] evolution.py — _v80_why_deep_analysis + extractors."""

    def setUp(self):
        for k in ("SCP_WHY_TYPE_INFERENCE", "SCP_WHY_DATA_FLOW", "SCP_WHY_SECURITY_THREAT"):
            os.environ.pop(k, None)

    def test_extract_type_mismatch_valid(self):
        ee = EvolutionEngine()
        desc = "TypeMismatch: var 'result' expected int but got dict"
        var_name, expected, actual = ee._v80_extract_type_mismatch(desc)
        self.assertEqual(var_name, "result")
        self.assertEqual(expected, "int")
        self.assertEqual(actual, "dict")

    def test_extract_type_mismatch_fallback(self):
        ee = EvolutionEngine()
        var_name, expected, actual = ee._v80_extract_type_mismatch("no match here")
        self.assertEqual((var_name, expected, actual), ("unknown", "unknown", "unknown"))

    def test_extract_data_flow_valid(self):
        ee = EvolutionEngine()
        desc = "DataFlow: source request.body flows to SQL_query without sanitize"
        source, sink, path = ee._v80_extract_data_flow(desc)
        self.assertEqual(source, "request.body")
        self.assertEqual(sink, "SQL_query")
        self.assertEqual(path, [])

    def test_extract_data_flow_fallback(self):
        ee = EvolutionEngine()
        source, sink, path = ee._v80_extract_data_flow("nothing here")
        self.assertEqual(source, "unknown_source")
        self.assertEqual(sink, "unknown_sink")
        self.assertEqual(path, [])

    def test_extract_cwe_valid(self):
        ee = EvolutionEngine()
        self.assertEqual(ee._v80_extract_cwe("CWE-89 SQL injection"), "CWE-89")
        self.assertEqual(ee._v80_extract_cwe("cwe-79 xss"), "CWE-79")
        self.assertEqual(ee._v80_extract_cwe("CWE89 no dash"), "CWE-89")
        self.assertEqual(ee._v80_extract_cwe("no cwe here"), "CWE-0")

    def test_dispatch_type_mismatch_bug(self):
        """Bug with type+mismatch in bug_type → calls type_inference_why."""
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=10, bug_type="TypeMismatch",
            description="TypeMismatch: var 'result' expected int but got dict",
            suggested_fix="", tier=T3, affects_logic=True,
        )
        # Mock WhyEngine.type_inference_why to verify dispatch
        with patch("scp.meta.why_engine.WhyEngine.type_inference_why") as mock_method:
            mock_method.return_value = {"why_question": "mock", "llm_used": False}
            result = ee._v80_why_deep_analysis(bug, fix_diff="diff")
            mock_method.assert_called_once_with("result", "int", "dict", "diff")
        self.assertEqual(result, {"why_question": "mock", "llm_used": False})

    def test_dispatch_data_flow_bug(self):
        """Bug with data_flow in bug_type → calls data_flow_why."""
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=10, bug_type="DataFlow",
            description="DataFlow: source request.body flows to SQL_query",
            suggested_fix="", tier=T3, affects_logic=True,
        )
        with patch("scp.meta.why_engine.WhyEngine.data_flow_why") as mock_method:
            mock_method.return_value = {"why_question": "mock"}
            result = ee._v80_why_deep_analysis(bug, "diff")
            mock_method.assert_called_once_with("request.body", "SQL_query", [], "diff")
        self.assertEqual(result, {"why_question": "mock"})

    def test_dispatch_security_bug(self):
        """Bug with security/cwe in bug_type → calls security_threat_why."""
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=10, bug_type="SecurityThreat",
            description="CWE-89 SQL injection at line 10",
            suggested_fix="", tier=T3, affects_logic=True,
        )
        with patch("scp.meta.why_engine.WhyEngine.security_threat_why") as mock_method:
            mock_method.return_value = {"why_question": "mock"}
            result = ee._v80_why_deep_analysis(bug, "diff")
            # Args: (code_pattern=desc, cwe_id, context=fix_diff)
            mock_method.assert_called_once_with(
                "CWE-89 SQL injection at line 10", "CWE-89", "diff"
            )
        self.assertEqual(result, {"why_question": "mock"})

    def test_dispatch_no_match_returns_empty(self):
        """Bug with unrelated type → returns {} (no mode dispatched)."""
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=10, bug_type="BareExceptPass",
            description="some other bug", suggested_fix="", tier=T1,
            affects_logic=False,
        )
        result = ee._v80_why_deep_analysis(bug, "diff")
        self.assertEqual(result, {})

    def test_dispatch_whyengine_init_failure_returns_empty(self):
        """If WhyEngine init fails, returns {} (no raise)."""
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=10, bug_type="TypeMismatch",
            description="TypeMismatch: var 'r' expected int but got str",
            suggested_fix="", tier=T3, affects_logic=True,
        )
        with patch("scp.meta.why_engine.WhyEngine", side_effect=RuntimeError("init failed")):
            result = ee._v80_why_deep_analysis(bug, "diff")
        self.assertEqual(result, {})


class TestReflectWiring(unittest.TestCase):
    """[V8.0-WHY] Verify reflect() calls _v80_why_deep_analysis (smoke test).

    This is a smoke test — it doesn't go through the full reflect() flow
    (which requires LLM calls + EvolutionEngine safety guards enabled).
    Instead it verifies the dispatch method exists and is callable.
    """

    def test_dispatch_callable_with_real_bug(self):
        ee = EvolutionEngine()
        bug = BugReport(
            file="x.py", line=1, bug_type="TypeMismatch",
            description="TypeMismatch: var 'r' expected int but got str",
            suggested_fix="", tier=T3, affects_logic=True,
        )
        # Env OFF → WhyEngine returns default dict (still non-empty)
        result = ee._v80_why_deep_analysis(bug, "fix_diff")
        self.assertIsInstance(result, dict)
        # Should have why_question (defensive default from type_inference_why)
        self.assertIn("why_question", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
