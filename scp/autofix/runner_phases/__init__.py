"""Runner phases package — extracted from `autofix/runner.py` in Task 10-B.

Sub-modules:
  - ast_scan: AST scanner classes + helpers (BareExceptPass, UndefinedName)
  - permission_check: V9.1 ImpactPrioritization layer
  - report: V5.9 + V8.0 full-scan + individual scanners + fix-and-report
  - pre_startup: STARTUP-GATE pre-startup audit + scheduled audit

All public symbols are re-exported here so `from scp.autofix.runner_phases
import ast_scan_scp` works directly.
"""
from scp.autofix.runner_phases.ast_scan import (
    _MAX_BUGS_PER_SCAN,
    _MAX_SCAN_FILES,
    _SCP_ROOT,
    DEEP_AUDIT_RESULTS_FILE,
    _BareExceptPassFinder,
    _build_bug_report,
    _iter_python_files,
    _ModuleNameCollector,
    _scan_file,
    _UndefinedNameFinder,
    _write_deep_audit_result,
    ast_scan_scp,
)
from scp.autofix.runner_phases.permission_check import _prioritize_bugs
from scp.autofix.runner_phases.pre_startup import (
    pre_startup_audit,
    run_scheduled,
)
from scp.autofix.runner_phases.report import (
    run_full_scan,
    run_full_scan_and_fix,
    run_single_scanner,
)

__all__ = [
    # ast_scan
    "ast_scan_scp",
    "DEEP_AUDIT_RESULTS_FILE",
    "_MAX_SCAN_FILES",
    "_MAX_BUGS_PER_SCAN",
    "_SCP_ROOT",
    "_BareExceptPassFinder",
    "_ModuleNameCollector",
    "_UndefinedNameFinder",
    "_iter_python_files",
    "_scan_file",
    "_build_bug_report",
    "_write_deep_audit_result",
    # permission_check
    "_prioritize_bugs",
    # report
    "run_full_scan",
    "run_single_scanner",
    "run_full_scan_and_fix",
    # pre_startup
    "pre_startup_audit",
    "run_scheduled",
]
