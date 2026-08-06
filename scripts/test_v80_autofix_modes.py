#!/usr/bin/env python3
"""[V8.0-AUTOFIX] Test script — verify 5 new pattern fixers generate valid patches.

Tests:
  1. fix_type_mismatch   — `if result > 0:` → `if isinstance(result, (int, float)) and result > 0:`
  2. add_null_check      — `value = result.attr` → `if result is not None:  # null guard\n    value = result.attr`
  3. add_lock            — `self._cache[key] = value` → `with self._lock:  # race condition guard\n    self._cache[key] = value`
                          + `self._lock = threading.Lock()` added to __init__
  4. parameterize_sql    — `f"SELECT * FROM t WHERE id={x}"` → `"SELECT * FROM t WHERE id=?", (x,)`
  5. add_context_manager — `f = open(path)` → `with open(path) as f:` (with subsequent block indented)

Each test:
  - Creates a temp dir with a buggy file
  - Mocks BugReport with file/line/description
  - Sets SCP_EVOLUTION_ENABLED=1
  - Calls the fixer
  - Verifies: result["action"] == "fixed", result["patched"] == True
  - Verifies: patched file parses with ast.parse
  - Verifies: expected patch content is present
  - Restores from .evolutionbak

Run: python scripts/test_v80_autofix_modes.py
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Make scp package importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _make_bug(file: str, line: int, bug_type: str, description: str, suggested_fix: str = ""):
    """Build a BugReport for testing."""
    from scp.autofix.classifier import BugReport, BugTier
    return BugReport(
        file=file,
        line=line,
        bug_type=bug_type,
        description=description,
        suggested_fix=suggested_fix,
        tier=BugTier.TIER_2_AUTO_FIX_LOG,
    )


def _reset_engine():
    """Reset EvolutionEngine singleton + rate-limit state."""
    try:
        from scp.autofix.evolution import reset_evolution_engine
        reset_evolution_engine()
    except Exception:
        pass


def _setup_env():
    """Enable SCP_EVOLUTION_ENABLED for the test session."""
    os.environ["SCP_EVOLUTION_ENABLED"] = "1"


def _teardown_env():
    os.environ.pop("SCP_EVOLUTION_ENABLED", None)


# ============================================================
# Test 1: fix_type_mismatch
# ============================================================
def test_fix_type_mismatch():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_type.py"
        source = (
            "def get_data() -> dict:\n"
            "    return {'count': 5}\n"
            "\n"
            "def main():\n"
            "    result = get_data()\n"
            "    if result > 0:\n"  # line 6 — TypeMismatch bug
            "        print('positive')\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=6,
            bug_type="TypeMismatch",
            description="TypeMismatch: variable `result` has inferred type `dict` (from function return annotation) but is used in numeric Compare (> < >= <=) at line 6 — this raises TypeError at runtime.",
        )
        result = evo.fix_type_mismatch(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_type_mismatch"
        assert "isinstance(result, (int, float))" in result["new"], f"new line missing isinstance: {result['new']}"
        assert "and result > 0:" in result["new"], f"new line missing comparison: {result['new']}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))  # raises if invalid
        assert "isinstance(result, (int, float)) and result > 0:" in patched, "patched file missing isinstance guard"

        # Verify backup created
        bak = target.with_suffix(target.suffix + ".evolutionbak")
        assert bak.exists(), "backup .evolutionbak not created"

        print("  [PASS] fix_type_mismatch — isinstance guard added correctly")


# ============================================================
# Test 2: add_null_check
# ============================================================
def test_add_null_check():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_null.py"
        source = (
            "def maybe_get() -> Optional[Thing]:\n"
            "    return thing_or_none()\n"
            "\n"
            "def main():\n"
            "    result = maybe_get()\n"
            "    value = result.attr\n"  # line 6 — NullDereference bug
            "    print(value)\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=6,
            bug_type="NullDereference",
            description="NullDereference: variable `result` was assigned from Optional-returning function at line 5 but accessed `.attr` (attr) at line 6 without a preceding `if result is not None:` check — raises AttributeError if None.",
        )
        result = evo.add_null_check(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_null_check"
        assert "if result is not None:" in result["new"], f"new block missing null check: {result['new']}"
        assert "# [V8.0-AUTOFIX] null guard" in result["new"], f"missing marker: {result['new']}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))
        assert "if result is not None:  # [V8.0-AUTOFIX] null guard" in patched, "patched file missing null check"
        assert "    value = result.attr" in patched, "patched file missing indented original line"

        print("  [PASS] add_null_check — null guard added correctly")


# ============================================================
# Test 3: add_lock
# ============================================================
def test_add_lock():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_race.py"
        source = (
            "class Cache:\n"
            "    def __init__(self):\n"
            "        self._cache = {}\n"
            "\n"
            "    def put(self, key, value):\n"
            "        self._cache[key] = value\n"  # line 6 — RaceCondition bug
            "\n"
            "    def get(self, key):\n"
            "        return self._cache.get(key)\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=6,
            bug_type="RaceCondition",
            description="RaceCondition: shared state `self._cache[key] = value` mutated at line 6 without lock — concurrent threads can corrupt dict.",
        )
        result = evo.add_lock(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_lock"
        assert "with self._lock:" in result["new"], f"new block missing with-statement: {result['new']}"
        assert "# [V8.0-AUTOFIX] race condition guard" in result["new"], f"missing marker: {result['new']}"
        assert result.get("lock_init_added") is True, f"lock_init_added should be True: {result}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))
        assert "with self._lock:  # [V8.0-AUTOFIX] race condition guard" in patched, "missing with-statement"
        assert "self._lock = threading.Lock()  # [V8.0-AUTOFIX] shared state lock" in patched, "missing lock init"
        assert "import threading" in patched, "missing threading import"

        print("  [PASS] add_lock — with self._lock: + threading.Lock() + import threading")


# ============================================================
# Test 4: parameterize_sql (f-string variant)
# ============================================================
def test_parameterize_sql_fstring():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_sql.py"
        source = (
            "import sqlite3\n"
            "\n"
            "def lookup(user_id):\n"
            "    conn = sqlite3.connect('db.sqlite')\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(f\"SELECT * FROM users WHERE id={user_id}\")\n"  # line 6
            "    return cur.fetchone()\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=6,
            bug_type="SQLInjection",
            description="SQLInjection: f-string interpolation in SQL at line 6 — user input directly interpolated, vulnerable to SQL injection.",
        )
        result = evo.parameterize_sql(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_sql"
        assert "?" in result["new"], f"new line missing ?: {result['new']}"
        assert "(user_id,)" in result["new"], f"new line missing params tuple: {result['new']}"
        assert "user_id" in result.get("params", []), f"params missing user_id: {result.get('params')}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))
        assert '"SELECT * FROM users WHERE id=?"' in patched, "missing parameterized SQL"
        assert "(user_id,)" in patched, "missing params tuple"

        print("  [PASS] parameterize_sql (f-string) — ? placeholder + (user_id,) tuple")


# ============================================================
# Test 4b: parameterize_sql (concatenation variant)
# ============================================================
def test_parameterize_sql_concat():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_sql_concat.py"
        source = (
            "import sqlite3\n"
            "\n"
            "def lookup(name):\n"
            "    conn = sqlite3.connect('db.sqlite')\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"  # line 6
            "    return cur.fetchone()\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=6,
            bug_type="SQLInjection",
            description="SQLInjection: string concatenation in SQL at line 6 — user input concatenated, vulnerable to SQL injection.",
        )
        result = evo.parameterize_sql(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_sql"
        assert "?" in result["new"], f"new line missing ?: {result['new']}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))
        assert "?" in patched, "missing ? placeholder"
        assert "(name,)" in patched, "missing (name,) tuple"

        print("  [PASS] parameterize_sql (concat) — ? placeholder + (name,) tuple")


# ============================================================
# Test 5: add_context_manager
# ============================================================
def test_add_context_manager():
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_leak.py"
        source = (
            "def read_config(path):\n"
            "    f = open(path)\n"  # line 2 — ResourceLeak bug
            "    data = f.read()\n"
            "    lines = data.splitlines()\n"
            "    return lines\n"
        )
        target.write_text(source, encoding="utf-8")

        _reset_engine()
        evo = get_evolution_engine(data_dir=tmp)
        bug = _make_bug(
            file=str(target),
            line=2,
            bug_type="ResourceLeak",
            description="ResourceLeak: `f = open(path)` at line 2 — file handle may leak if exception occurs. Use `with open(path) as f:` instead.",
        )
        result = evo.add_context_manager(bug)

        assert result["action"] == "fixed", f"action should be 'fixed', got {result['action']}: {result.get('reason')}"
        assert result["patched"] is True, f"patched should be True: {result}"
        assert result["fix_source"] == "pattern_ctx_mgr"
        assert "with open(path) as f:" in result["new"], f"new line missing with-statement: {result['new']}"
        assert "# [V8.0-AUTOFIX] context manager" in result["new"], f"missing marker: {result['new']}"
        assert result.get("block_lines_indented", 0) >= 2, f"block_lines_indented should be >= 2: {result}"

        # Verify patched file parses
        patched = target.read_text(encoding="utf-8")
        ast.parse(patched, filename=str(target))
        assert "with open(path) as f:  # [V8.0-AUTOFIX] context manager" in patched, "missing with-statement"
        assert "    data = f.read()" in patched, "subsequent line not indented"
        assert "    lines = data.splitlines()" in patched, "subsequent line not indented"
        assert "    return lines" in patched, "subsequent line not indented"

        print("  [PASS] add_context_manager — `with open(path) as f:` + block indented")


# ============================================================
# Test 6: Safety guards — env var OFF should skip all 5 fixers
# ============================================================
def test_safety_guards_env_off():
    """All 5 fixers should skip when SCP_EVOLUTION_ENABLED is not '1'."""
    _teardown_env()  # ensure env var OFF
    _reset_engine()

    from scp.autofix.classifier import BugReport, BugTier
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "noop.py"
        target.write_text("x = 1\n", encoding="utf-8")
        evo = get_evolution_engine(data_dir=tmp)

        bug = BugReport(
            file=str(target), line=1, bug_type="X",
            description="X", suggested_fix="",
            tier=BugTier.TIER_2_AUTO_FIX_LOG,
        )

        for fixer_name in ("fix_type_mismatch", "add_null_check", "add_lock",
                           "parameterize_sql", "add_context_manager"):
            result = getattr(evo, fixer_name)(bug)
            assert result["action"] == "skipped", f"{fixer_name} should skip when env OFF: {result}"
            assert result.get("patched") is False, f"{fixer_name} should not patch when env OFF: {result}"
            assert result.get("reason") == "evolution guards blocked", (
                f"{fixer_name} wrong reason: {result.get('reason')}")

        print("  [PASS] safety guards — all 5 fixers skip when SCP_EVOLUTION_ENABLED=0")

    _setup_env()  # restore for subsequent tests


# ============================================================
# Test 7: Test files rejected (falsification guard)
# ============================================================
def test_test_files_rejected():
    """Test files should be rejected_by_why (heuristic falsification).

    Each fixer only fires for its matching bug_type — we test all 5 with
    their corresponding types and verify each rejects test files.
    """
    _setup_env()
    _reset_engine()

    from scp.autofix.classifier import BugReport, BugTier
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        # Create a file in tests/ subdir
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir()
        target = tests_dir / "test_thing.py"
        target.write_text("x = 1\n", encoding="utf-8")
        evo = get_evolution_engine(data_dir=tmp)

        # Each fixer's matching bug_type
        fixer_to_bug_type = {
            "fix_type_mismatch": "TypeMismatch",
            "add_null_check": "NullDereference",
            "add_lock": "RaceCondition",
            "parameterize_sql": "SQLInjection",
            "add_context_manager": "ResourceLeak",
        }

        for fixer_name, bug_type in fixer_to_bug_type.items():
            bug = BugReport(
                file=str(target), line=1, bug_type=bug_type,
                description=f"{bug_type} test", suggested_fix="",
                tier=BugTier.TIER_2_AUTO_FIX_LOG,
            )
            result = getattr(evo, fixer_name)(bug)
            assert result["action"] == "rejected_by_why", (
                f"{fixer_name} should reject test files: {result}")
            assert result.get("patched") is False, f"{fixer_name} should not patch test files: {result}"

        print("  [PASS] test files rejected by all 5 fixers (heuristic falsification)")


# ============================================================
# Test 8: process_bug_with_llm wiring (smoke test)
# ============================================================
def test_process_bug_with_llm_wiring():
    """Verify process_bug_with_llm() routes TypeMismatch bug to fix_type_mismatch
    when SCP_EVOLUTION_ENABLED=1 and BugReport has no suggested_fix."""
    _setup_env()
    _reset_engine()

    from scp.autofix import llm_fix
    from scp.autofix.classifier import BugReport, BugTier
    from scp.autofix.engine import AutoFixEngine
    from scp.autofix.evolution import get_evolution_engine

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "buggy_wiring.py"
        source = (
            "def get_data() -> dict:\n"
            "    return {'count': 5}\n"
            "\n"
            "def main():\n"
            "    result = get_data()\n"
            "    if result > 0:\n"  # line 6
            "        print('positive')\n"
        )
        target.write_text(source, encoding="utf-8")

        bug = BugReport(
            file=str(target),
            line=6,
            bug_type="TypeMismatch",
            description="TypeMismatch: variable `result` has inferred type `dict` (from function return annotation) but is used in numeric Compare (> < >= <=) at line 6 — this raises TypeError at runtime.",
            suggested_fix="",  # no predefined fix → should try pattern fixers
            tier=BugTier.TIER_2_AUTO_FIX_LOG,
        )

        # Build AutoFixEngine (won't actually apply Tier-2 without running full process_bug)
        # but we test that llm_fix.process_bug_with_llm routes correctly
        engine = AutoFixEngine(data_dir=tmp)

        # Monkey-patch engine.process_bug so it doesn't actually run the full classifier/cooldown
        # (we only want to test that process_bug_with_llm TRIES the pattern fixer first)
        call_log = []

        def mock_process_bug(b):
            call_log.append(b)
            # Simulate AutoFix accepting the patch
            return {"action": "fixed", "tier": int(b.tier), "patched": True}

        engine.process_bug = mock_process_bug

        result = llm_fix.process_bug_with_llm(bug, engine)
        assert result.get("action") == "fixed", f"expected fixed, got: {result}"
        # Note: process_bug_with_llm calls evo.fix_type_mismatch DIRECTLY (it does the patch),
        # so engine.process_bug is NOT called for pattern-fixed bugs. The result comes from
        # the EvolutionEngine method directly.
        assert result.get("fix_source") == "pattern_fix_type_mismatch", (
            f"fix_source should be pattern_fix_type_mismatch: {result}")
        assert result.get("llm_generated") is False, f"should not have called LLM: {result}"

        print("  [PASS] process_bug_with_llm routes TypeMismatch bug to fix_type_mismatch (no LLM call)")


# ============================================================
# Main runner
# ============================================================
def main():
    print("=" * 60)
    print("[V8.0-AUTOFIX] Test suite — 5 new pattern fixers")
    print("=" * 60)

    _setup_env()
    try:
        tests = [
            ("Test 1: fix_type_mismatch", test_fix_type_mismatch),
            ("Test 2: add_null_check", test_add_null_check),
            ("Test 3: add_lock", test_add_lock),
            ("Test 4a: parameterize_sql (f-string)", test_parameterize_sql_fstring),
            ("Test 4b: parameterize_sql (concat)", test_parameterize_sql_concat),
            ("Test 5: add_context_manager", test_add_context_manager),
            ("Test 6: safety guards (env OFF)", test_safety_guards_env_off),
            ("Test 7: test files rejected", test_test_files_rejected),
            ("Test 8: process_bug_with_llm wiring", test_process_bug_with_llm_wiring),
        ]
        passed = 0
        failed = 0
        for label, fn in tests:
            print(f"\n{label}")
            try:
                fn()
                passed += 1
            except AssertionError as e:
                print(f"  [FAIL] {e}")
                failed += 1
            except Exception as e:
                import traceback
                print(f"  [ERROR] {type(e).__name__}: {e}")
                traceback.print_exc()
                failed += 1

        print()
        print("=" * 60)
        print(f"Results: {passed} passed / {failed} failed / {len(tests)} total")
        print("=" * 60)
        return 0 if failed == 0 else 1
    finally:
        _teardown_env()


if __name__ == "__main__":
    sys.exit(main())
