# [V5.9-SCANNER] Scanner package — system-wide bug detection.
# [V8.0-SCANNER] Extended with 8 NEW scanners for broader coverage.
#
# TẠI SAO package này tồn tại?
#   Gà: "Tại Sao: quét toàn hệ thống chứ không phải bị giới hạn như này."
#   AST scanner cũ (runner.py) chỉ detect 2 loại bug syntax:
#     - BareExceptPass
#     - PossiblyUndefinedName
#   KHÔNG detect được:
#     - Dead SLMs (init nhưng không route)
#     - Routing gaps (SLM không có keywords)
#     - API wiring gaps (.env có key nhưng data_source không dùng)
#     - Logic flow bugs (confidence == 0.7 exact match — fragile)
#     - Schema mismatch (CREATE TABLE khác nhau giữa các file)
#
#   5 scanners V5.9 mở rộng coverage TOÀN HỆ THỐNG:
#     1. DeadSLMScanner       — SLMs init nhưng không bao giờ được route
#     2. RoutingGapScanner    — SLM có route nhưng keywords quá hẹp (VN-only)
#     3. APIWiringScanner     — API keys trong .env nhưng data_source không wire
#     4. LogicFlowScanner     — `if confidence == X` exact match (fragile)
#     5. SchemaMismatchScanner — `CREATE TABLE` khác shape giữa các file
#
#   8 scanners V8.0 mở rộng coverage thêm 8 loại bug mới (CWE Top 25, perf,
#   type contract, null safety, race, SQL, resource leak, dead code):
#     6.  TypeContractScanner  — dict > int, None.attr, str+int, list[str]
#     7.  NullSafetyScanner    — Optional[X] return without None check
#     8.  RaceConditionScanner — mutation on shared state without lock
#     9.  SQLInjectionScanner  — f-string/concat/%-format SQL execution
#     10. ResourceLeakScanner  — open/socket/connect without `with`/`.close()`
#     11. PerformanceScanner   — O(n²) nested loops, list.index in loop, etc.
#     12. SecurityScanner      — CWE Top 25 (cmd injection, deserialization, etc.)
#     13. DeadCodeScanner      — functions/classes never referenced
#
#   [OPT-18] 1 NEW scanner cho CWE-79 (XSS — OWASP Top 10 #1):
#     14. XSSScanner           — reflected/stored XSS via HTML response with
#                                unescaped user input (Markup, HTMLResponse,
#                                f-string with HTML tags + request.args, etc.)
#
#   Tổng cộng 14 scanners (5 V5.9 + 8 V8.0 + 1 OPT-18). Coverage ước tính ~43%.
#
#   Tất cả return `list[BugReport]` (reuse classifier.BugReport).
#   Tất cả OPT-IN qua `--full-scan` hoặc individual `--scan-*` flags.
#   Không bao giờ phá flow cũ (BareExceptPass / UndefinedName vẫn default).
from __future__ import annotations

from scp.autofix.scanners.api_wiring_scanner import APIWiringScanner
from scp.autofix.scanners.dead_code_scanner import DeadCodeScanner
from scp.autofix.scanners.dead_slm_scanner import DeadSLMScanner
from scp.autofix.scanners.logic_flow_scanner import LogicFlowScanner
from scp.autofix.scanners.null_safety_scanner import NullSafetyScanner
from scp.autofix.scanners.performance_scanner import PerformanceScanner
from scp.autofix.scanners.race_condition_scanner import RaceConditionScanner
from scp.autofix.scanners.resource_leak_scanner import ResourceLeakScanner
from scp.autofix.scanners.routing_gap_scanner import RoutingGapScanner
from scp.autofix.scanners.schema_scanner import SchemaMismatchScanner
from scp.autofix.scanners.security_scanner import SecurityScanner
from scp.autofix.scanners.sql_injection_scanner import SQLInjectionScanner

# [V8.0-SCANNER] 8 NEW scanners
from scp.autofix.scanners.type_contract_scanner import TypeContractScanner

# [OPT-18-SCANNER] 1 NEW scanner for CWE-79 (XSS)
from scp.autofix.scanners.xss_scanner import XSSScanner

__all__ = [
    # V5.9 scanners
    "DeadSLMScanner",
    "RoutingGapScanner",
    "APIWiringScanner",
    "LogicFlowScanner",
    "SchemaMismatchScanner",
    # V8.0 scanners
    "TypeContractScanner",
    "NullSafetyScanner",
    "RaceConditionScanner",
    "SQLInjectionScanner",
    "ResourceLeakScanner",
    "PerformanceScanner",
    "SecurityScanner",
    "DeadCodeScanner",
    # OPT-18 scanner
    "XSSScanner",
]
