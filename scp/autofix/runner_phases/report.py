"""
[V5.9-SCANNER + V8.0-SCANNER] Full system-wide scan report generation.

TẠI SAO: Gà yêu cầu "quét toàn hệ thống chứ không phải bị giới hạn như này."
AST scanner cũ (BareExceptPass + UndefinedName) chỉ bắt syntax bugs.
V5.9: 5 scanners mới bắt LOGIC/WIRING/ROUTING/SCHEMA bugs.
V8.0: 8 scanners mới bắt TYPE/NULL/RACE/SQL/RESOURCE/PERF/SECURITY/DEAD-CODE bugs.
  - DeadSLMScanner        — SLMs init nhưng không bao giờ được route
  - RoutingGapScanner     — SLM có route nhưng keywords quá hẹp
  - APIWiringScanner      — .env API keys nhưng data_source không wire
  - LogicFlowScanner      — `if confidence == 0.7` exact float (fragile)
  - SchemaMismatchScanner — CREATE TABLE khác shape giữa các file
  - TypeContractScanner   — dict > int, None.attr, str+int, list[str]
  - NullSafetyScanner     — Optional[X] return without None-check
  - RaceConditionScanner  — mutation on shared state without lock
  - SQLInjectionScanner   — f-string/concat/%-format SQL execution
  - ResourceLeakScanner   — open/socket/connect without `with`/`.close()`
  - PerformanceScanner    — O(n²) nested loops, list.index in loop, etc.
  - SecurityScanner       — CWE Top 25 (cmd injection, deserialization, etc.)
  - DeadCodeScanner       — functions/classes never referenced
  [OPT-18] XSS scanner:
  - XSSScanner            — CWE-79 reflected/stored XSS (Markup, HTMLResponse,
                            f-string HTML + request.args, etc.)
Tất cả OPT-IN qua `--full-scan` hoặc individual `--scan-*` flags.
Không phá flow cũ (BareExceptPass/UndefinedName vẫn default).

Extracted from `autofix/runner.py` in Task 10-B (Modularity Refactor B).
"""
from __future__ import annotations

import logging

from scp.autofix.classifier import BugReport
from scp.autofix.engine import get_autofix_engine
from scp.autofix.runner_phases.ast_scan import ast_scan_scp

logger = logging.getLogger("scp.autofix.runner")


def run_full_scan(include_ast: bool = True, max_bugs: int = 0) -> dict:
    """[V5.9+V8.0+OPT-18-SCANNER] Run ALL 14 scanners + (optionally) AST scan.

    Returns a summary dict with:
      - per-scanner bug counts
      - total bugs found
      - list of all BugReports (in `bugs` key)
      - if include_ast=True, also runs ast_scan_scp() and merges results
    """
    from scp.autofix.scanners import (
        APIWiringScanner,
        DeadCodeScanner,
        DeadSLMScanner,
        LogicFlowScanner,
        NullSafetyScanner,
        PerformanceScanner,
        RaceConditionScanner,
        ResourceLeakScanner,
        RoutingGapScanner,
        SchemaMismatchScanner,
        SecurityScanner,
        SQLInjectionScanner,
        TypeContractScanner,
        XSSScanner,
    )

    all_bugs: list[BugReport] = []
    per_scanner: dict[str, int] = {}

    scanners = [
        # V5.9 scanners
        ("DeadSLMScanner", DeadSLMScanner()),
        ("RoutingGapScanner", RoutingGapScanner()),
        ("APIWiringScanner", APIWiringScanner()),
        ("LogicFlowScanner", LogicFlowScanner()),
        ("SchemaMismatchScanner", SchemaMismatchScanner()),
        # V8.0 scanners
        ("TypeContractScanner", TypeContractScanner()),
        ("NullSafetyScanner", NullSafetyScanner()),
        ("RaceConditionScanner", RaceConditionScanner()),
        ("SQLInjectionScanner", SQLInjectionScanner()),
        ("ResourceLeakScanner", ResourceLeakScanner()),
        ("PerformanceScanner", PerformanceScanner()),
        ("SecurityScanner", SecurityScanner()),
        ("DeadCodeScanner", DeadCodeScanner()),
        # OPT-18 scanner
        ("XSSScanner", XSSScanner()),
    ]
    for name, scanner in scanners:
        try:
            found = scanner.scan()
            per_scanner[name] = len(found)
            all_bugs.extend(found)
        except Exception as e:
            logger.error(f"[V8.0-SCANNER] {name} failed: {e}")
            per_scanner[name] = -1  # signal error

    if include_ast:
        ast_bugs = ast_scan_scp()
        per_scanner["ASTScanner"] = len(ast_bugs)
        all_bugs.extend(ast_bugs)

    if max_bugs > 0 and len(all_bugs) > max_bugs:
        logger.info(
            f"[V8.0-SCANNER] capping at {max_bugs} bugs (had {len(all_bugs)})"
        )
        all_bugs = all_bugs[:max_bugs]

    summary = {
        "source": "full_scan",
        "scanners": per_scanner,
        "total_bugs": len(all_bugs),
        "bugs": all_bugs,
    }
    logger.info(
        f"[V8.0-SCANNER] full scan complete: {summary['total_bugs']} bug(s) — "
        f"{per_scanner}"
    )
    return summary


def run_single_scanner(scanner_name: str) -> list[BugReport]:
    """[V5.9+V8.0-SCANNER] Run a single named scanner. Returns its BugReports.

    Used by individual --scan-* CLI flags.
    """
    name = scanner_name.lower()
    # V5.9 scanners
    if name in ("dead_slm", "dead-slm", "deadslm"):
        from scp.autofix.scanners import DeadSLMScanner
        return DeadSLMScanner().scan()
    if name in ("routing", "routing_gap", "routing-gap"):
        from scp.autofix.scanners import RoutingGapScanner
        return RoutingGapScanner().scan()
    if name in ("api_wiring", "api-wiring", "apiwiring"):
        from scp.autofix.scanners import APIWiringScanner
        return APIWiringScanner().scan()
    if name in ("logic", "logic_flow", "logic-flow"):
        from scp.autofix.scanners import LogicFlowScanner
        return LogicFlowScanner().scan()
    if name in ("schema", "schema_mismatch"):
        from scp.autofix.scanners import SchemaMismatchScanner
        return SchemaMismatchScanner().scan()
    # V8.0 scanners
    if name in ("type", "type_contract", "type-contract", "typecontract"):
        from scp.autofix.scanners import TypeContractScanner
        return TypeContractScanner().scan()
    if name in ("null", "null_safety", "null-safety", "nullsafety"):
        from scp.autofix.scanners import NullSafetyScanner
        return NullSafetyScanner().scan()
    if name in ("race", "race_condition", "race-condition", "racecondition"):
        from scp.autofix.scanners import RaceConditionScanner
        return RaceConditionScanner().scan()
    if name in ("sql", "sql_injection", "sql-injection", "sqlinjection"):
        from scp.autofix.scanners import SQLInjectionScanner
        return SQLInjectionScanner().scan()
    if name in ("resource", "resource_leak", "resource-leak", "resourceleak"):
        from scp.autofix.scanners import ResourceLeakScanner
        return ResourceLeakScanner().scan()
    if name in ("perf", "performance"):
        from scp.autofix.scanners import PerformanceScanner
        return PerformanceScanner().scan()
    if name in ("security",):
        from scp.autofix.scanners import SecurityScanner
        return SecurityScanner().scan()
    if name in ("dead_code", "dead-code", "deadcode"):
        from scp.autofix.scanners import DeadCodeScanner
        return DeadCodeScanner().scan()
    # OPT-18 scanner
    if name in ("xss", "xss_vulnerability", "xss-vulnerability", "xssvulnerability"):
        from scp.autofix.scanners import XSSScanner
        return XSSScanner().scan()
    raise ValueError(f"unknown scanner: {scanner_name}")


def run_full_scan_and_fix(max_bugs: int = 0) -> dict:
    """[V5.9-SCANNER] Run full scan AND feed bugs into AutoFixEngine.

    Like run_once(ast_scan=True) but uses the full-scan bug list.
    Respects all safety guards (Tier 1-4, classifier, rate limit, cooldown).
    """
    scan_summary = run_full_scan(include_ast=True, max_bugs=max_bugs)
    bugs: list[BugReport] = scan_summary["bugs"]
    engine = get_autofix_engine()

    summary = {
        "processed": 0, "fixed": 0, "permission_requested": 0,
        "skipped": 0, "denied": 0, "details": [], "engine_stats": {},
        "source": "full_scan_and_fix",
        "scanners": scan_summary["scanners"],
        "total_bugs_found": scan_summary["total_bugs"],
    }

    from scp.autofix.llm_fix import process_bug_with_llm
    for bug in bugs:
        try:
            result = process_bug_with_llm(bug, engine)
        except Exception as e:
            logger.error(
                f"[runner] process_bug_with_llm failed for "
                f"{bug.file}:{bug.line}: {e}"
            )
            result = {"action": "skipped", "tier": 0,
                      "reason": f"runner error: {e}"}

        summary["processed"] += 1
        action = result.get("action", "skipped")
        if action == "fixed":
            summary["fixed"] += 1
        elif action == "permission_requested":
            summary["permission_requested"] += 1
        elif action == "denied":
            summary["denied"] += 1
        else:
            summary["skipped"] += 1

        summary["details"].append({
            "file": bug.file, "line": bug.line,
            "bug_type": bug.bug_type, "result": result,
        })

    summary["engine_stats"] = engine.stats()
    return summary
