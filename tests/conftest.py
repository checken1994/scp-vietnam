"""Shared test helpers — prevents test brittleness after refactors.

[AUTOFIX-T1-BRITTLENESS-FIX] SCP codebase was refactored:
  - scp/runtime/judge.py → judge.py + judge_parts/ (judgecore_mixin, judgebg_mixin, ...)
  - scp/runtime/slms.py → slms.py + slms_parts/ + slm_impls/
  - scp/api_server.py → api_server.py + api_server_parts/ + api/routes/
  - scp/core/real_question_fetcher.py → real_question_fetcher.py + question_fetchers/

Behavioral tests that grep'd a SINGLE file for markers (e.g. 'V104.42 #O')
broke because markers moved to submodules. This helper reads the WHOLE
module tree so tests survive future refactors.

DNA SCP: Behavior > file location. PASS ≠ ĐÚNG if test only checks
"string exists in one file" — that's testing the FILE, not the BEHAVIOR.
"""
import os
import sys
from pathlib import Path

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def _read_module_tree(*subpaths: str) -> str:
    """Read all .py files in a module directory tree + concat content.

    Usage:
      # Read single file:
      content = _read_module_tree("scp", "runtime", "judge.py")
      # Read whole tree:
      content = _read_module_tree("scp", "runtime", "judge_parts")
      # Combine (recommended for refactor-safe tests):
      content = (_read_module_tree("scp", "runtime", "judge.py")
                 + _read_module_tree("scp", "runtime", "judge_parts"))

    Returns empty string if path doesn't exist (test will fail with clear
    assertion error rather than FileNotFoundError).
    """
    root = Path(SCP_DIR).joinpath(*subpaths)
    if root.is_file() and root.suffix == ".py":
        try:
            return root.read_text(encoding="utf-8")
        except Exception:
            return ""
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


def _read_judge_tree() -> str:
    """Read judge.py + judge_parts/ — the full judge module tree."""
    return (_read_module_tree("scp", "runtime", "judge.py")
            + _read_module_tree("scp", "runtime", "judge_parts"))


def _read_slms_tree() -> str:
    """Read slms.py + slms_parts/ + slm_impls/ — the full SLM module tree."""
    return (_read_module_tree("scp", "runtime", "slms.py")
            + _read_module_tree("scp", "runtime", "slms_parts")
            + _read_module_tree("scp", "runtime", "slm_impls"))


def _read_api_tree() -> str:
    """Read api_server.py + api_server_parts/ + api/routes/ — the full API tree."""
    return (_read_module_tree("scp", "api_server.py")
            + _read_module_tree("scp", "api_server_parts")
            + _read_module_tree("scp", "api", "routes")
            + _read_module_tree("scp", "api"))


def _read_question_fetcher_tree() -> str:
    """Read real_question_fetcher.py + question_fetchers/ — the full fetcher tree."""
    return (_read_module_tree("scp", "core", "real_question_fetcher.py")
            + _read_module_tree("scp", "core", "question_fetchers"))


def _read_consolidator_tree() -> str:
    """Read consolidator.py + consolidator/ — the full consolidator tree."""
    return (_read_module_tree("scp", "consolidator.py")
            + _read_module_tree("scp", "consolidator"))
