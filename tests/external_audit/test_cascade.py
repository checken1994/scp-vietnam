"""External audit — verify fixes don't cause cascade failures.

RC-10 fix: break circular self-audit. External test suite independent
from SCP's own autofix scanners.

These tests are deliberately minimal and self-contained. They check INVARIANTS
that must hold after every fix:
  1. RC-1: ruff --select F821,E722 → 0 errors (no undefined names, no bare except)
  2. RC-2: no hardcoded token, no DEV_MODE bypass, evolution default ON
  3. Cascade: every .py file still parses (no syntax errors introduced by fixes)

If any of these tests fail, the corresponding RC fix has regressed — revert
the offending change before merging.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve SCP source root: tests/external_audit/../../ = scp-v105-fixed/
SCP_ROOT = Path(__file__).resolve().parent.parent.parent

<<<<<<< HEAD
# [FALSE-POS-FIX] B105: extract test token to module-level constant
_TEST_TOKEN = "test-token-for-import-only"  # nosec B105 - test fixture token, not a real credential

=======
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f

# ---------------------------------------------------------------------------
# RC-1: lint invariants (F821 + E722 must stay clean)
# ---------------------------------------------------------------------------

def test_no_undefined_names_via_ruff():
    """RC-1: F821 (undefined name) must be 0 after fix.

    Previously pyproject.toml ignored F821 with bogus justification
    "lazy loading pattern" — grep TYPE_CHECKING = 0 hits. Removing the
    ignore exposed 32 real bugs (logger/Optional/threading/sys/_tracker_log_deferred
    /PredictiveEngine undefined). This test guards against recurrence.
    """
<<<<<<< HEAD
    result = subprocess.run(  # noqa: S603
        ["ruff", "check", "--select=F821", "--no-cache", str(SCP_ROOT)],  # noqa: S607
=======
    result = subprocess.run(
        ["ruff", "check", "--select=F821", "--no-cache", str(SCP_ROOT)],
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"F821 errors found (RC-1 regression):\n{result.stdout}\n{result.stderr}"
    )


def test_no_bare_except_via_ruff():
    """RC-1: E722 (bare except) must be 0 after fix.

    Bare `except:` swallows everything including KeyboardInterrupt and
    SystemExit — and silently hides bugs. V105 had 1 in judge.py:1689
    (tracker.log failure path). Fixed to `except Exception as e: logger.warning(...)`.
    """
<<<<<<< HEAD
    result = subprocess.run(  # noqa: S603
        ["ruff", "check", "--select=E722", "--no-cache", str(SCP_ROOT)],  # noqa: S607
=======
    result = subprocess.run(
        ["ruff", "check", "--select=E722", "--no-cache", str(SCP_ROOT)],
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"E722 errors found (RC-1 regression):\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# RC-2: security defaults
# ---------------------------------------------------------------------------

def test_no_hardcoded_token_via_grep():
    """RC-2: hardcoded production token must be removed from source.

    The token `<ROTATED>` was leaked in 8+ files
    (api_server.py JS fallback, run_benchmark.py, evaluate.py, dashboard,
    QUICKSTART docs, ISO_BENCHMARK_SPEC). All source occurrences removed;
    .env is KEPT (user-confirmed) — only source code is checked here.
    """
    # [G5-FIX] If env var unset, grep would be called with empty pattern →
    # matches every line → false positives. Skip when no token configured.
    token = os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
    if not token:
        pytest.skip("SCP_AUTH_TOKEN_SECRET not set — cannot verify hardcoded token absence")
<<<<<<< HEAD
    result = subprocess.run(  # noqa: S603
        ["grep", "-rn", "--include=*.py", "--include=*.md", "--include=*.js",  # noqa: S607
=======
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", "--include=*.md", "--include=*.js",
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
         "--include=*.ts", "--include=*.tsx",
         token, str(SCP_ROOT)],
        capture_output=True, text=True,
    )
    # grep returns 0 (found) / 1 (not found) / 2 (error)
    # We want NOT FOUND → returncode != 0.
    # Exclude pycache / .pyc / our own test files (we mention the token
    # in our assertions — that's not a regression).
    found_lines = [
        line for line in result.stdout.splitlines()
        if "__pycache__" not in line
        and ".pyc" not in line
        and "/tests/external_audit/" not in line
    ]
    assert not found_lines, (
        "Hardcoded token still in source (RC-2 regression):\n"
        + "\n".join(found_lines)
    )


def test_no_dev_mode_bypass_via_grep():
    """RC-2: SCP_DEV_MODE=1 → return True bypass must be removed from verify_admin.

    The V104.22 #3 "fix" re-introduced an admin auth bypass: setting
    SCP_DEV_MODE=1 in env made verify_admin return True without any token check.
    RC-2 removed this branch entirely — now admins MUST configure a real token
    via .env. The grep pattern catches any future reintroduction of the bypass.
    """
    # Search for the specific bypass pattern in api_server.py
    api_server = SCP_ROOT / "api_server.py"
    if not api_server.exists():
        pytest.skip("api_server.py not found — package layout differs")
    src = api_server.read_text(encoding="utf-8")
    # The bypass pattern: SCP_DEV_MODE check immediately followed by `return True`
    # inside verify_admin. We approximate with: any "SCP_DEV_MODE" → "return True"
    # within 5 lines.
    import re
    pattern = re.compile(
        r'SCP_DEV_MODE[^\\n]*?\\n(?:[^\\n]*?\\n){0,4}?\\s*return\\s+True',
        re.MULTILINE
    )
    matches = pattern.findall(src)
    assert not matches, (
        "SCP_DEV_MODE bypass still present in api_server.py (RC-2 regression):\n"
        + "\n---\n".join(matches)
    )


def test_evolution_enabled_default_via_grep():
    """RC-2: SCP_EVOLUTION_ENABLED default must be "1" (ON), not "0".

    V105 defaulted SCP_EVOLUTION_ENABLED=0 in 4 sites:
      - autofix/evolution.py:157, 2101
      - autofix/llm_fix.py:449
      - autofix/engine.py:388
    Defaulting it OFF silently disabled DNA #8 (learning + accumulation).
    RC-2 flipped all 4 defaults to "1".
    """
    # Find any os.environ.get("SCP_EVOLUTION_ENABLED", "0") still present
<<<<<<< HEAD
    result = subprocess.run(  # noqa: S603
        ["grep", "-rn", "--include=*.py",  # noqa: S607
=======
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py",
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
         'SCP_EVOLUTION_ENABLED", "0"', str(SCP_ROOT)],
        capture_output=True, text=True,
    )
    found_lines = [
        line for line in result.stdout.splitlines()
        if "__pycache__" not in line
        and "/tests/external_audit/" not in line  # skip our own test files
    ]
    assert not found_lines, (
        "SCP_EVOLUTION_ENABLED still defaults to 0 somewhere (RC-2 regression):\n"
        + "\n".join(found_lines)
    )


# ---------------------------------------------------------------------------
# Cascade: syntax integrity (no fix may break parseability)
# ---------------------------------------------------------------------------

def test_all_python_files_parse():
    """Cascade check: every .py file in SCP must parse (no syntax errors).

    If a fix introduces a syntax error (e.g. broken decorator, missing colon,
    unbalanced paren), this test catches it. AST parse is independent of
    imports — it only checks syntax, not whether modules can be imported.
    """
    failures = []
    for py_file in SCP_ROOT.rglob("*.py"):
        # Skip __pycache__ and the tests folder itself (we don't audit our own tests)
        if "__pycache__" in str(py_file):
            continue
        if "/tests/external_audit/" in str(py_file) or str(py_file).endswith("conftest.py"):
            continue
        try:
            ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as e:
            failures.append(f"{py_file}: {e}")
    assert not failures, (
        "Syntax errors introduced by fixes (cascade regression):\n"
        + "\n".join(failures)
    )


def test_api_server_imports():
    """Cascade check: api_server.py must still import after RC-2 changes.

    Adding `dependencies=[Depends(verify_admin)]` to 24 decorators and
    removing the SCP_DEV_MODE branch could have broken imports. Verify the
    FastAPI app still constructs.
    """
    # Use a subprocess so we don't pollute the test process's sys.path.
    code = (
        f"import sys; sys.path.insert(0, {str(SCP_ROOT.parent)!r}); "
        "from scp.api_server import app; "
        "print('IMPORT_OK', type(app).__name__)"
    )
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
<<<<<<< HEAD
        "HOME": __import__("tempfile").gettempdir(),  # nosec B108 - test fixture, uses tempfile.gettempdir() (not hardcoded)
        # Skip heavy startup gates that might fail in CI without Ollama.
        "SCP_SKIP_STARTUP_GATE": "1",
        "SCP_AUTH_TOKEN_SECRET": _TEST_TOKEN,  # nosec B105 - test fixture token (constant defined at module level)
    }
    result = subprocess.run(  # noqa: S603
=======
        "HOME": "/tmp",
        # Skip heavy startup gates that might fail in CI without Ollama.
        "SCP_SKIP_STARTUP_GATE": "1",
        "SCP_AUTH_TOKEN_SECRET": "test-token-for-import-only",
    }
    result = subprocess.run(
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert "IMPORT_OK" in result.stdout, (
        f"api_server.py failed to import (cascade regression):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
