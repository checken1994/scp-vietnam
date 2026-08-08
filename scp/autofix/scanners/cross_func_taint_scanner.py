# [V11-SCANNER] CrossFuncTaintScanner — Pysa-style whole-program taint analysis.
#
# TẠI SAO scanner này tồn tại? (Task 11-a — closes the cross-function taint gap)
#   The intra-function `taint_flow_scanner.py` (V10, 829 lines) only tracks
#   taint WITHIN a single function. If `def sanitize(x): return x` is called
#   on tainted data, the V10 scanner treats the return as clean (it doesn't
#   know that `sanitize` is just a passthrough). Conversely, if a helper like
#   `def run_it(cmd): subprocess.run(cmd, shell=True)` is called with tainted
#   data, V10 has no way to know that `run_it` SINKS its argument — the
#   `subprocess.run` call is in `run_it`'s body, not in the caller's body.
#
#   Pysa (Meta), CodeQL (GitHub), and Semgrep's dataflow engine solve this
#   via whole-program analysis: build a call graph, summarize each function
#   (which params propagate to return / which params reach sinks), then
#   iterate to a fixpoint so transitive flows (A → B → C → sink) are caught.
#
#   This scanner implements a Pysa-style 3-pass whole-program analysis:
#     Pass 1 — Build a call graph: parse every .py file in scp/, build a
#              FunctionInfo summary for each function (params, returns,
#              sinks-in-body, outgoing calls to other scp/ functions).
#     Pass 2 — Fixpoint taint propagation: iterate (max 10 rounds) to expand
#              each function's `propagating_params` (params that flow to
#              RETURN, including transitively via callees) and
#              `sink_consuming_params` (params that reach a SINK, including
#              transitively via callees).
#     Pass 3 — Cross-function detection: walk each function's body with
#              cross-function awareness (knowing which callees propagate
#              taint, which return sources, which sink their args). Flag a
#              CrossFuncTaint bug whenever a tainted var is passed to a
#              callee whose corresponding param is sink-consuming.
#
# SOURCES (taint origins) — same as V10 + builtin input():
#   - request.args / request.form / request.json / request.GET / request.POST
#     (and aliases: req.args, flask.request.args)
#   - request.args.get(...) / request.form.get(...) / etc.
#   - os.environ.get(...), os.getenv(...), getenv(...)
#   - sys.argv[...]
#   - input(...) / raw_input(...)   ← NEW in V11 (V10 misses these)
#   - Heuristic function parameters (only for non-@staticmethod/@classmethod):
#     user_input, query, command, data, payload, content, text, input
#
# SINKS (dangerous endpoints) — same as V10 + relaxed SQL execute:
#   CWE-78 (OS Command Injection): subprocess.run/Popen/call/check_output/
#     check_call, os.system, os.popen
#   CWE-89 (SQL Injection): cursor.execute / executemany / executescript /
#     db_query_all / db_query_one / db_query / db_exec
#     ← V11 RELAXED: V10 only flags these if the SQL string is built
#       dynamically (f-string/concat/%/format). V11 ALWAYS flags them in
#       cross-function mode, because if a param reaches execute(), the param
#       IS the SQL string in the worst case (regardless of how it was built).
#   CWE-79 (XSS): Markup, HTMLResponse
#   CWE-502 (Unsafe Deserialization): pickle.load(s), marshal.loads,
#     yaml.load (non-SafeLoader)
#   CWE-94 (Code Injection): eval, exec, compile(..., "exec")
#
# SANITIZERS (neutralize taint) — same as V10:
#   - re.escape, shlex.quote, html.escape, markupsafe.escape, flask.escape,
#     cgi.escape, bleach.clean
#   - str(int(x)) / str(float(x)) — numeric coercion
#   - isinstance(x, ...) checks in `if` tests (partial heuristic)
#
# RETURNS:
#   list[BugReport] — bug_type="CrossFuncTaint_CWE-XXX", tier=TIER_3_PERMISSION.
#   The `file`/`line` fields point to the CALLER's call-site (where tainted
#   data crosses the function boundary) — that's the most actionable location
#   for the operator to add a sanitization call. The `description` includes
#   the full call chain: source@caller.py:L → call@caller.py:L →
#   sink@callee.py:L (sink_name).
#
# LIMITATIONS (documented for future improvement):
#   - Callee resolution is by SIMPLE NAME only (bare-name calls like `foo(x)`).
#     `self.method(x)`, `obj.method(x)`, `module.func(x)` are NOT resolved
#     (treated as unknown callees). This means cross-function flows through
#     method calls are MISSED. A v2 would track class scope + imports.
#   - If multiple functions share the same simple name (e.g., two files both
#     define `process()`), the scanner conservatively checks if ANY of them
#     is sink-consuming — this is sound (no false negatives) but may over-
#     report (false positives).
#   - Local var taint tracking is intra-function-linear (no path-sensitivity,
#     no branch-sensitivity). A var tainted in one branch is considered
#     tainted for the rest of the function (same as V10).
#   - **kwargs / **kwonly args are not tracked positionally; only positional
#     and posonly args are matched to callee params by position.
#   - The scanner does NOT report bugs where source and sink are in the SAME
#     function (those are caught by V10's intra-function scanner). This
#     avoids duplicate reports. Cross-function = source and sink in
#     DIFFERENT functions.
from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

# Reuse SOURCE/SINK definitions and helpers from the intra-function scanner.
# (Attribution: copied + extended from scp/autofix/scanners/taint_flow_scanner.py)
from scp.autofix.scanners.taint_flow_scanner import (
    _CWE_TITLES,
    _HEURISTIC_PARAM_NAMES,
    _MARSHAL_FUNCS,
    _PICKLE_FUNCS,
    _SQL_EXECUTE_NAMES,
    _SUBPROCESS_FUNCS,
    _XSS_BUILDERS,
    _collect_names,
    _is_sanitizer_call,
    _is_source,
    _iter_python_files,
)

logger = logging.getLogger("scp.autofix.scanners.cross_func_taint")

_SCP_ROOT = Path(__file__).resolve().parent.parent.parent  # .../scp/
_MAX_FILES = 500
_MAX_FIXPOINT_ROUNDS = 10

__all__ = ["scan_file", "scan_scp"]


# ============================================================================
# Extra sources (beyond what V10 _is_source detects)
# ============================================================================

# Python builtins that read user input from stdin. V10's _is_source() does NOT
# include these (V10 relies on heuristic param names for CLI taint). V11 adds
# them because cross-function flows often involve `input()` → helper → sink.
_BUILTIN_INPUT_FUNCS: frozenset[str] = frozenset({
    "input",        # Python 3
    "raw_input",    # Python 2 (legacy code)
})


def _xfunc_is_source(node) -> tuple[str, str] | None:
    """Extended SOURCE detection: V10's _is_source + builtin input()/raw_input()."""
    # Built-in input(...) / raw_input(...) call
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _BUILTIN_INPUT_FUNCS:
            return ("cli", f"{node.func.id}()")
    return _is_source(node)


# ============================================================================
# Relaxed SINK classifier for cross-function analysis
# ============================================================================

def _classify_sink_xfunc(node: ast.Call) -> tuple[str, str] | None:
    """Like V10's _classify_sink, but SQL execute calls are ALWAYS classified
    as sinks (V10 only flags dynamic SQL strings).

    Rationale: in cross-function mode, the question is "does a param reach
    this sink?" — if `cursor.execute(query)` is called and `query` is a
    param, then any caller passing tainted data to this function creates a
    SQL injection path, REGARDLESS of how `query` was built inside the
    function (the caller controls it).
    """
    func = node.func

    # --- Attribute-based sinks ---
    if isinstance(func, ast.Attribute):
        recv = func.value
        attr = func.attr

        # os.system(...) / os.popen(...)
        if isinstance(recv, ast.Name) and recv.id == "os":
            if attr in ("system", "popen"):
                return ("CWE-78", f"os.{attr}")

        # subprocess.run / Popen / call / check_output / check_call
        if isinstance(recv, ast.Name) and recv.id == "subprocess":
            if attr in _SUBPROCESS_FUNCS:
                return ("CWE-78", f"subprocess.{attr}")

        # pickle.loads / pickle.load
        if isinstance(recv, ast.Name) and recv.id == "pickle":
            if attr in _PICKLE_FUNCS:
                return ("CWE-502", f"pickle.{attr}")

        # marshal.loads
        if isinstance(recv, ast.Name) and recv.id == "marshal":
            if attr in _MARSHAL_FUNCS:
                return ("CWE-502", f"marshal.{attr}")

        # yaml.load (non-SafeLoader)
        if isinstance(recv, ast.Name) and recv.id == "yaml":
            if attr == "load":
                has_safe_loader = False
                for kw in node.keywords:
                    if kw.arg == "Loader":
                        if (isinstance(kw.value, ast.Name)
                                and "Safe" in kw.value.id):
                            has_safe_loader = True
                        elif (isinstance(kw.value, ast.Attribute)
                              and "Safe" in kw.value.attr):
                            has_safe_loader = True
                if not has_safe_loader:
                    return ("CWE-502", "yaml.load")

        # SQL execute — V11 RELAXED: always flag (param-reaching matters)
        if attr in _SQL_EXECUTE_NAMES:
            return ("CWE-89", attr)

        # flask.Markup / markupsafe.Markup
        if attr in _XSS_BUILDERS:
            return ("CWE-79", attr)

    # --- Bare-name sinks ---
    if isinstance(func, ast.Name):
        if func.id in ("eval", "exec"):
            return ("CWE-94", func.id)
        if func.id == "compile":
            # compile(source, filename, mode, ...) — flag if mode == 'exec'
            for arg in node.args:
                if (isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and arg.value == "exec"):
                    return ("CWE-94", "compile")
            for kw in node.keywords:
                if (kw.arg == "mode"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                        and kw.value.value == "exec"):
                    return ("CWE-94", "compile")
        if func.id in _XSS_BUILDERS:
            return ("CWE-79", func.id)

    return None


# ============================================================================
# Call graph data structures
# ============================================================================

@dataclass
class CallEdge:
    """An edge in the call graph: caller F calls callee G with F's param as arg.

    Only recorded for calls where (a) the callee is a bare-name call (so we
    can resolve it by simple name) and (b) one of the args is a param of F.
    """
    callee_name: str        # simple name of callee (e.g., "run_it")
    arg_param: str          # F's param passed as the arg
    call_line: int          # line of the call expression in F
    arg_pos: int            # 0-based positional index of the arg
    in_return: bool         # is this Call node inside a Return statement?


@dataclass
class SinkHit:
    """A sink reached by a param (directly in F, or transitively via a callee)."""
    cwe: str
    sink_name: str
    sink_line: int
    # For transitive hits (reached via a callee), the callee info:
    via_callee: str | None = None         # callee qualname (e.g., "path::run_it")
    via_callee_file: str | None = None    # callee file path
    via_callee_param: str | None = None   # callee's param that reaches the sink


@dataclass
class FunctionInfo:
    """Per-function summary used by the whole-program taint analysis.

    Pass 1 fields are populated by `_CallGraphBuilder` from AST:
      - params, is_static_or_class
      - returns_source: True if any return statement returns a SOURCE call
      - returns_param: params that flow to RETURN (without sanitization)
      - param_sinks: params that flow to a SINK in this function's body
      - outgoing_calls: edges to other scp/ functions (for fixpoint)
      - func_node: the AST FunctionDef node (for Pass 3 detector walk)

    Pass 2 fields are populated by `_run_fixpoint`:
      - propagating_params: subset of params whose taint propagates to RETURN
        (initially = returns_param; expanded by fixpoint when a callee
        propagates taint back through F's return)
      - sink_consuming_params: subset of params whose taint reaches a SINK
        (initially = param_sinks.keys(); expanded by fixpoint transitively
        through callee chains)
      - transitive_sinks: per-param list of SinkHit objects (including
        transitive hits via callees) — used to build the call chain in
        the bug description
    """
    qualname: str
    file: str
    lineno: int
    name: str
    params: list[str]
    is_static_or_class: bool
    func_node: ast.AST | None = field(default=None, repr=False)

    # Pass 1:
    returns_source: bool = False
    source_return_line: int = 0
    source_return_desc: str = ""
    returns_param: set[str] = field(default_factory=set)
    param_sinks: dict[str, list[SinkHit]] = field(default_factory=dict)
    outgoing_calls: list[CallEdge] = field(default_factory=list)

    # Pass 2 (fixpoint-expanded):
    propagating_params: set[str] = field(default_factory=set)
    sink_consuming_params: set[str] = field(default_factory=set)
    transitive_sinks: dict[str, list[SinkHit]] = field(default_factory=dict)


# ============================================================================
# Pass 1: Call graph builder (AST visitor)
# ============================================================================

class _CallGraphBuilder(ast.NodeVisitor):
    """Walk a file's AST and build a FunctionInfo for every function def.

    Tracks current class scope so qualnames include the class prefix
    (e.g., "scp/core/foo.py::MyClass.my_method").
    """

    def __init__(self, file: Path, relpath: str):
        self.file = file
        self.relpath = relpath
        self.class_stack: list[str] = []
        self.funcs: list[FunctionInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_func(node)

    def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        prefix = ".".join(self.class_stack) if self.class_stack else ""
        qualname = (
            f"{self.relpath}::{prefix}.{node.name}"
            if prefix else f"{self.relpath}::{node.name}"
        )
        info = FunctionInfo(
            qualname=qualname,
            file=str(self.file),
            lineno=node.lineno,
            name=node.name,
            params=self._extract_params(node),
            is_static_or_class=self._is_static_or_class(node),
            func_node=node,
        )
        self._analyze_body(node, info)
        self.funcs.append(info)
        # Recurse into body for nested funcs (nested funcs get their own info)
        for child in node.body:
            self.visit(child)

    @staticmethod
    def _extract_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Return positional + posonly + kwonly param names (skip *args/**kwargs)."""
        a = node.args
        params: list[str] = []
        params.extend(arg.arg for arg in a.posonlyargs)
        params.extend(arg.arg for arg in a.args)
        params.extend(arg.arg for arg in a.kwonlyargs)
        return params

    @staticmethod
    def _is_static_or_class(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(
            (isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod"))
            or (isinstance(d, ast.Attribute)
                and d.attr in ("staticmethod", "classmethod"))
            for d in node.decorator_list
        )

    def _analyze_body(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        info: FunctionInfo,
    ) -> None:
        """Walk function body (NOT descending into nested funcs) to extract:
        - isinstance-sanitized params
        - return-statement analysis (source returns, param returns)
        - sink calls (which params reach them)
        - outgoing calls to other scp/ functions (with params as args)
        """
        sanitized: set[str] = set()

        # Pass A: collect isinstance-sanitized vars
        for n in self._walk_in_scope(func_node):
            if isinstance(n, ast.If):
                for sub in ast.walk(n.test):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "isinstance"):
                        if (sub.args
                                and isinstance(sub.args[0], ast.Name)
                                and sub.args[0].id in info.params):
                            sanitized.add(sub.args[0].id)

        # Pre-collect Call node IDs that appear inside any Return statement
        # (so we can mark outgoing_calls as in_return=True for those).
        return_call_ids: set[int] = set()
        for n in self._walk_in_scope(func_node):
            if isinstance(n, ast.Return) and n.value is not None:
                for sub in ast.walk(n.value):
                    if isinstance(sub, ast.Call):
                        return_call_ids.add(id(sub))

        # Pass B: returns, sinks, outgoing calls
        for n in self._walk_in_scope(func_node):
            if isinstance(n, ast.Return) and n.value is not None:
                self._analyze_return_value(n.value, info, sanitized)
            if isinstance(n, ast.Call):
                self._analyze_call_node(
                    n, info, sanitized,
                    in_return=id(n) in return_call_ids,
                )

    @staticmethod
    def _walk_in_scope(node: ast.AST):
        """Yield all descendants of `node` EXCEPT nested FunctionDef /
        AsyncFunctionDef / ClassDef bodies (those have their own scope)."""
        for child in ast.iter_child_nodes(node):
            yield from _CallGraphBuilder._walk_in_scope_helper(child)

    @staticmethod
    def _walk_in_scope_helper(node: ast.AST):
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            return  # skip nested scopes
        for child in ast.iter_child_nodes(node):
            yield from _CallGraphBuilder._walk_in_scope_helper(child)

    def _analyze_return_value(
        self,
        value: ast.AST,
        info: FunctionInfo,
        sanitized: set[str],
    ) -> None:
        """Inspect a return expression: does it return a SOURCE? a param? a
        sanitized value? Updates info.returns_source / returns_param."""
        # Sanitizer call wraps taint → no propagation
        if _is_sanitizer_call(value):
            return

        # SOURCE call (input(), request.args.get(), os.environ.get(), etc.)
        src = _xfunc_is_source(value)
        if src is not None:
            info.returns_source = True
            if info.source_return_line == 0:
                info.source_return_line = getattr(value, "lineno", 0)
                _, info.source_return_desc = src
            # Don't return — a source call may also reference a param
            # (rare, but possible: `return input() + suffix`)

        # Direct param reference: `return x`
        if isinstance(value, ast.Name) and value.id in info.params:
            if value.id not in sanitized:
                info.returns_param.add(value.id)
            return

        # Expression involving a param: `return f"...{x}"`, `return x + "y"`,
        # `return x.strip()`, `return foo(x)`, etc.
        names = _collect_names(value)
        for n in names:
            if n in info.params and n not in sanitized:
                info.returns_param.add(n)

    def _analyze_call_node(
        self,
        call_node: ast.Call,
        info: FunctionInfo,
        sanitized: set[str],
        in_return: bool,
    ) -> None:
        """Inspect a Call node: is it a SINK that consumes a param? is it an
        outgoing call to another scp/ function with a param as arg?"""
        # --- SINK detection ---
        sink = _classify_sink_xfunc(call_node)
        if sink is not None:
            cwe, sink_name = sink
            # Find which params reach this sink (skip sanitizer-wrapped args)
            for arg in call_node.args:
                if _is_sanitizer_call(arg):
                    continue
                arg_names = _collect_names(arg)
                for n in arg_names:
                    if n in info.params and n not in sanitized:
                        hit = SinkHit(
                            cwe=cwe,
                            sink_name=sink_name,
                            sink_line=call_node.lineno,
                        )
                        info.param_sinks.setdefault(n, []).append(hit)
            for kw in call_node.keywords:
                if kw.value is None or _is_sanitizer_call(kw.value):
                    continue
                arg_names = _collect_names(kw.value)
                for n in arg_names:
                    if n in info.params and n not in sanitized:
                        hit = SinkHit(
                            cwe=cwe,
                            sink_name=sink_name,
                            sink_line=call_node.lineno,
                        )
                        info.param_sinks.setdefault(n, []).append(hit)

        # --- Outgoing call to another scp/ function (bare-name resolution) ---
        callee_name = self._callee_simple_name(call_node)
        if callee_name is not None:
            for i, arg in enumerate(call_node.args):
                if _is_sanitizer_call(arg):
                    continue
                if (isinstance(arg, ast.Name)
                        and arg.id in info.params
                        and arg.id not in sanitized):
                    info.outgoing_calls.append(CallEdge(
                        callee_name=callee_name,
                        arg_param=arg.id,
                        call_line=call_node.lineno,
                        arg_pos=i,
                        in_return=in_return,
                    ))

    @staticmethod
    def _callee_simple_name(call_node: ast.Call) -> str | None:
        """Return the simple name of the callee for bare-name calls only.

        `foo(x)` → "foo"
        `obj.method(x)` / `self.method(x)` / `module.func(x)` → None
            (attribute calls are NOT resolved in v1 — would need import/scope
             analysis to know which `obj.method` is meant)
        """
        func = call_node.func
        if isinstance(func, ast.Name):
            return func.id
        return None


# ============================================================================
# Pass 2: Fixpoint taint propagation
# ============================================================================

def _run_fixpoint(funcs_by_qualname: dict[str, FunctionInfo]) -> None:
    """Iterate taint propagation through the call graph until fixpoint.

    Expands each function's `propagating_params` and `sink_consuming_params`
    based on callee behavior:
      - If F calls G with F's param `p` at arg position `i`, and G's param
        at position `i` is in G.sink_consuming_params, then `p` is added to
        F.sink_consuming_params (transitive sink).
      - If F returns the result of calling G with F's param `p` at position
        `i`, and G's param at position `i` is in G.propagating_params (or
        G.returns_source), then `p` is added to F.propagating_params.

    Iterates until no changes (or _MAX_FIXPOINT_ROUNDS reached).
    """
    # Index by simple name (for callee resolution)
    funcs_by_name: dict[str, list[FunctionInfo]] = defaultdict(list)
    for f in funcs_by_qualname.values():
        funcs_by_name[f.name].append(f)

    # Initialize Pass 2 state from Pass 1
    for f in funcs_by_qualname.values():
        f.propagating_params = set(f.returns_param)
        f.sink_consuming_params = set(f.param_sinks.keys())
        f.transitive_sinks = {
            p: list(hits) for p, hits in f.param_sinks.items()
        }

    # Iterate
    for round_num in range(_MAX_FIXPOINT_ROUNDS):
        changed = False
        for f in funcs_by_qualname.values():
            for edge in f.outgoing_calls:
                candidates = funcs_by_name.get(edge.callee_name, [])
                if not candidates:
                    continue
                for g in candidates:
                    if edge.arg_pos >= len(g.params):
                        continue
                    g_param = g.params[edge.arg_pos]

                    # --- Transitive sink propagation ---
                    # If G's g_param reaches a sink (directly or transitively),
                    # then F's arg_param also reaches that sink (transitively).
                    if g_param in g.sink_consuming_params:
                        if edge.arg_param not in f.sink_consuming_params:
                            f.sink_consuming_params.add(edge.arg_param)
                            changed = True
                        # Record transitive sink hits (for description)
                        existing_keys = {
                            (h.cwe, h.sink_name, h.sink_line, h.via_callee)
                            for h in f.transitive_sinks.get(edge.arg_param, [])
                        }
                        for h in g.transitive_sinks.get(g_param, []):
                            key = (h.cwe, h.sink_name, h.sink_line, h.via_callee)
                            if key in existing_keys:
                                continue
                            transitive = SinkHit(
                                cwe=h.cwe,
                                sink_name=h.sink_name,
                                sink_line=h.sink_line,
                                via_callee=g.qualname,
                                via_callee_file=g.file,
                                via_callee_param=g_param,
                            )
                            f.transitive_sinks.setdefault(
                                edge.arg_param, []
                            ).append(transitive)
                            existing_keys.add(key)
                            changed = True

                    # --- Transitive propagation (return taint flow) ---
                    # If F returns `G(arg_param)` and G propagates g_param
                    # (or G returns a source), then F's arg_param propagates
                    # to F's return.
                    if edge.in_return and (
                        g_param in g.propagating_params or g.returns_source
                    ):
                        if edge.arg_param not in f.propagating_params:
                            f.propagating_params.add(edge.arg_param)
                            changed = True

        if not changed:
            logger.debug(
                f"[CrossFuncTaint] fixpoint reached at round {round_num + 1} "
                f"({len(funcs_by_qualname)} functions)"
            )
            return

    logger.debug(
        f"[CrossFuncTaint] fixpoint maxed out at {_MAX_FIXPOINT_ROUNDS} rounds "
        f"({len(funcs_by_qualname)} functions)"
    )


# ============================================================================
# Pass 3: Cross-function bug detector (per-function walk)
# ============================================================================

class _FunctionDetector:
    """Per-function cross-function taint detector.

    Walks the function body tracking tainted vars (params + locals), with
    cross-function awareness of callee behavior:
      - When `x = G(...)` and G.returns_source → x is tainted
      - When `x = G(arg)` and arg is tainted and G.propagating_params
        contains the corresponding param → x is tainted
      - When `G(tainted_arg)` is called and G.sink_consuming_params contains
        the corresponding param → flag as cross-function taint bug
    """

    def __init__(
        self,
        info: FunctionInfo,
        funcs_by_name: dict[str, list[FunctionInfo]],
    ):
        self.info = info
        self.funcs_by_name = funcs_by_name
        self.findings: list[dict] = []
        # tainted: var_name -> (source_line, source_desc)
        self.tainted: dict[str, tuple[int, str]] = {}
        # sanitized: var_name -> True (cleared from tainted, won't re-taint)
        self.sanitized: set[str] = set()

    def analyze(self) -> None:
        if self.info.func_node is None:
            return
        # Pre-taint heuristic-named params (only for non-static/class methods)
        if not self.info.is_static_or_class:
            for p in self.info.params:
                if p in _HEURISTIC_PARAM_NAMES:
                    self.tainted[p] = (
                        self.info.lineno,
                        f"parameter '{p}' (heuristic user-input name)",
                    )

        # Walk body in source order
        for stmt in self.info.func_node.body:
            self._walk_in_scope(stmt)

    def _walk_in_scope(self, node: ast.AST) -> None:
        """Walk node + descendants, processing assigns/calls/ifs.

        Does NOT descend into nested FunctionDef / AsyncFunctionDef /
        ClassDef — those have their own scope and get their own analysis.
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            return

        if isinstance(node, ast.If):
            self._check_isinstance_in_test(node.test)
        elif isinstance(node, ast.Assign):
            self._handle_assign(node)
        elif isinstance(node, ast.AnnAssign):
            self._handle_annassign(node)
        elif isinstance(node, ast.AugAssign):
            self._handle_augassign(node)
        elif isinstance(node, ast.Call):
            self._handle_call(node)

        for child in ast.iter_child_nodes(node):
            self._walk_in_scope(child)

    def _check_isinstance_in_test(self, test_node: ast.AST) -> None:
        """If test is `isinstance(var, ...)` → mark var sanitized (partial)."""
        for n in ast.walk(test_node):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "isinstance"):
                if n.args and isinstance(n.args[0], ast.Name):
                    var_name = n.args[0].id
                    self.sanitized.add(var_name)
                    self.tainted.pop(var_name, None)

    def _handle_assign(self, node: ast.Assign) -> None:
        """Track taint through `x = value`."""
        # Sanitizer wrap → mark target sanitized
        if isinstance(node.value, ast.Call) and _is_sanitizer_call(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted.pop(tgt.id, None)
                    self.sanitized.add(tgt.id)
            return

        # Source call/attr on RHS → taint target
        src = _xfunc_is_source(node.value)
        if src is not None:
            _, desc = src
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted[tgt.id] = (node.lineno, desc)
                    self.sanitized.discard(tgt.id)
            return

        # Call to a known scp/ function — check cross-function return taint
        if isinstance(node.value, ast.Call):
            xfunc_taint = self._check_callee_return_taint(node.value)
            if xfunc_taint is not None:
                src_line, src_desc = xfunc_taint
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        self.tainted[tgt.id] = (src_line, src_desc)
                        self.sanitized.discard(tgt.id)
                return

        # RHS references a tainted var → propagate taint to target
        rhs_names = _collect_names(node.value)
        tainted_refs = (
            rhs_names & set(self.tainted.keys())
        ) - self.sanitized
        if tainted_refs:
            src_line, src_desc = min(
                (self.tainted[n] for n in tainted_refs),
                key=lambda x: x[0],
            )
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted[tgt.id] = (
                        src_line,
                        f"{src_desc} (propagated)",
                    )
                    self.sanitized.discard(tgt.id)
            return

        # Reassignment to clearly-safe value → remove taint
        if isinstance(node.value, ast.Constant):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted.pop(tgt.id, None)
                    self.sanitized.discard(tgt.id)
        elif (isinstance(node.value, ast.Name)
              and node.value.id not in self.tainted):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.tainted.pop(tgt.id, None)
                    self.sanitized.discard(tgt.id)

    def _handle_annassign(self, node: ast.AnnAssign) -> None:
        """Same as _handle_assign but for `x: Type = value` form."""
        if node.value is None:
            return
        if isinstance(node.value, ast.Call) and _is_sanitizer_call(node.value):
            if isinstance(node.target, ast.Name):
                self.tainted.pop(node.target.id, None)
                self.sanitized.add(node.target.id)
            return
        src = _xfunc_is_source(node.value)
        if src is not None:
            _, desc = src
            if isinstance(node.target, ast.Name):
                self.tainted[node.target.id] = (node.lineno, desc)
                self.sanitized.discard(node.target.id)
            return
        if isinstance(node.value, ast.Call):
            xfunc_taint = self._check_callee_return_taint(node.value)
            if xfunc_taint is not None and isinstance(node.target, ast.Name):
                src_line, src_desc = xfunc_taint
                self.tainted[node.target.id] = (src_line, src_desc)
                self.sanitized.discard(node.target.id)
                return
        rhs_names = _collect_names(node.value)
        tainted_refs = (
            rhs_names & set(self.tainted.keys())
        ) - self.sanitized
        if tainted_refs and isinstance(node.target, ast.Name):
            src_line, src_desc = min(
                (self.tainted[n] for n in tainted_refs),
                key=lambda x: x[0],
            )
            self.tainted[node.target.id] = (
                src_line,
                f"{src_desc} (propagated)",
            )
            self.sanitized.discard(node.target.id)
            return
        if isinstance(node.value, ast.Constant):
            if isinstance(node.target, ast.Name):
                self.tainted.pop(node.target.id, None)
                self.sanitized.discard(node.target.id)
        elif (isinstance(node.value, ast.Name)
              and node.value.id not in self.tainted):
            if isinstance(node.target, ast.Name):
                self.tainted.pop(node.target.id, None)
                self.sanitized.discard(node.target.id)

    def _handle_augassign(self, node: ast.AugAssign) -> None:
        """Handle `x += y` — propagate taint from y to x if y is tainted."""
        if not isinstance(node.target, ast.Name):
            return
        rhs_names = _collect_names(node.value)
        tainted_refs = (
            rhs_names & set(self.tainted.keys())
        ) - self.sanitized
        if tainted_refs:
            src_line, src_desc = min(
                (self.tainted[n] for n in tainted_refs),
                key=lambda x: x[0],
            )
            self.tainted[node.target.id] = (
                src_line,
                f"{src_desc} (propagated via +=)",
            )

    def _check_callee_return_taint(
        self, call_node: ast.Call
    ) -> tuple[int, str] | None:
        """If call_node is a call to a known scp/ function G, and G's return
        is tainted (returns_source OR G propagates one of the args we pass),
        return (source_line, source_desc) describing the taint origin.

        Returns None if the call's return is not tainted.
        """
        callee_name = _CallGraphBuilder._callee_simple_name(call_node)
        if callee_name is None:
            return None
        candidates = self.funcs_by_name.get(callee_name, [])
        if not candidates:
            return None

        for g in candidates:
            # Case 1: G returns a SOURCE — return is always tainted
            if g.returns_source:
                desc = g.source_return_desc or "source"
                return (
                    g.source_return_line or call_node.lineno,
                    f"{desc} (returned by {g.name})",
                )

            # Case 2: G propagates one of its params that we pass tainted
            for i, arg in enumerate(call_node.args):
                if i >= len(g.params):
                    break
                g_param = g.params[i]
                if g_param not in g.propagating_params:
                    continue
                # G propagates this param — is our arg tainted?
                if (isinstance(arg, ast.Name)
                        and arg.id in self.tainted
                        and arg.id not in self.sanitized):
                    src_line, src_desc = self.tainted[arg.id]
                    return (
                        src_line,
                        f"{src_desc} (propagated through {g.name})",
                    )
        return None

    def _handle_call(self, node: ast.Call) -> None:
        """Check if this Call passes tainted data to a callee whose
        corresponding param is sink-consuming → flag cross-function bug.

        Also checks intra-function sinks (V10-style) but SKIPS them — those
        are reported by taint_flow_scanner.py, not this scanner.
        """
        callee_name = _CallGraphBuilder._callee_simple_name(node)
        if callee_name is None:
            return
        candidates = self.funcs_by_name.get(callee_name, [])
        if not candidates:
            return

        # For each tainted arg, check if any callee candidate is sink-consuming
        # at the corresponding param position.
        for i, arg in enumerate(node.args):
            if not (isinstance(arg, ast.Name)
                    and arg.id in self.tainted
                    and arg.id not in self.sanitized):
                continue
            tainted_var = arg.id
            src_line, src_desc = self.tainted[tainted_var]

            for g in candidates:
                if i >= len(g.params):
                    continue
                g_param = g.params[i]
                if g_param not in g.sink_consuming_params:
                    continue

                # Found a cross-function taint bug!
                # Get the sink hit(s) — prefer direct (param_sinks), fall
                # back to transitive_sinks.
                direct_hits = g.param_sinks.get(g_param, [])
                transitive_hits = [
                    h for h in g.transitive_sinks.get(g_param, [])
                    if h not in direct_hits
                ]
                all_hits = direct_hits + transitive_hits

                if not all_hits:
                    continue

                for hit in all_hits:
                    self.findings.append({
                        "call_line": node.lineno,
                        "callee_qualname": g.qualname,
                        "callee_name": g.name,
                        "callee_file": g.file,
                        "callee_param": g_param,
                        "tainted_var": tainted_var,
                        "source_line": src_line,
                        "source_desc": src_desc,
                        "cwe": hit.cwe,
                        "sink_name": hit.sink_name,
                        "sink_line": hit.sink_line,
                        "via_callee": hit.via_callee,  # None for direct hits
                        "via_callee_file": hit.via_callee_file,
                    })


# ============================================================================
# Whole-program scanner
# ============================================================================

class _CrossFuncScanner:
    """Encapsulates the whole-program call graph + detection logic."""

    def __init__(self):
        self.funcs_by_qualname: dict[str, FunctionInfo] = {}
        self.funcs_by_name: dict[str, list[FunctionInfo]] = defaultdict(list)
        self._fixpoint_done: bool = False

    def add_file(self, path: Path) -> None:
        """Parse a file and add its functions to the call graph."""
        path = Path(path)
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not read {path}: {e}")
            return
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            logger.debug(f"SyntaxError in {path}: {e}")
            return

        # relpath relative to _SCP_ROOT if possible, else absolute
        try:
            relpath = str(path.relative_to(_SCP_ROOT.parent))
        except ValueError:
            relpath = str(path)

        builder = _CallGraphBuilder(path, relpath)
        builder.visit(tree)
        for info in builder.funcs:
            # Skip duplicate qualnames (same file added twice)
            if info.qualname in self.funcs_by_qualname:
                continue
            self.funcs_by_qualname[info.qualname] = info
            self.funcs_by_name[info.name].append(info)

        # Adding new functions invalidates the fixpoint
        self._fixpoint_done = False

    def run_fixpoint(self) -> None:
        if self._fixpoint_done:
            return
        _run_fixpoint(self.funcs_by_qualname)
        self._fixpoint_done = True

    def detect_bugs(
        self,
        only_in_files: set[str] | None = None,
    ) -> list[BugReport]:
        """Walk each function with cross-function awareness; return bugs.

        If `only_in_files` is set, only report bugs whose CALLER is in one of
        those files (used by scan_file to limit reports to the target file).
        """
        if not self._fixpoint_done:
            self.run_fixpoint()

        bugs: list[BugReport] = []
        seen_keys: set[tuple[str, int, str, int, int]] = set()

        for _qualname, info in self.funcs_by_qualname.items():
            if only_in_files is not None and info.file not in only_in_files:
                continue
            detector = _FunctionDetector(info, self.funcs_by_name)
            detector.analyze()
            for f in detector.findings:
                # Deduplicate by (caller_file, call_line, callee_qualname,
                # sink_line, source_line)
                key = (
                    info.file, f["call_line"], f["callee_qualname"],
                    f["sink_line"], f["source_line"],
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                bugs.append(self._build_bug_report(info, f))
        return bugs

    @staticmethod
    def _build_bug_report(
        caller_info: FunctionInfo,
        finding: dict,
    ) -> BugReport:
        """Construct a BugReport from a detector finding."""
        cwe = finding["cwe"]
        cwe_title = _CWE_TITLES.get(cwe, "Cross-Function Taint")
        sink_name = finding["sink_name"]
        sink_line = finding["sink_line"]
        callee_name = finding["callee_name"]
        callee_file = finding["callee_file"]
        callee_param = finding["callee_param"]
        tainted_var = finding["tainted_var"]
        src_line = finding["source_line"]
        src_desc = finding["source_desc"]
        call_line = finding["call_line"]
        via_callee = finding["via_callee"]
        via_callee_file = finding.get("via_callee_file")

        caller_short = Path(caller_info.file).name
        callee_short = Path(callee_file).name

        # Extract intermediate callee name from qualname (format: "<relpath>::<Class>.<method>" or "<relpath>::<func>")
        via_callee_name: str | None = None
        if via_callee and "::" in via_callee:
            via_callee_name = via_callee.split("::", 1)[1]

        # Build call chain string + description
        if via_callee_name is not None:
            # Transitive: caller → callee → via_callee → sink
            sink_file_short = Path(via_callee_file or callee_file).name
            chain = (
                f"source@{caller_short}:{src_line} → "
                f"call@{caller_short}:{call_line} ({caller_info.name} → "
                f"{callee_name}) → {callee_name} calls {via_callee_name} "
                f"→ sink@{sink_file_short}:{sink_line} ({sink_name})"
            )
            desc = (
                f"CrossFuncTaint [{cwe} — {cwe_title}]: tainted variable "
                f"`{tainted_var}` flows from source {src_desc} at line "
                f"{src_line} through call `{callee_name}({tainted_var})` at "
                f"line {call_line} (param `{callee_param}`), which "
                f"transitively passes it to `{via_callee_name}` where it "
                f"reaches sink `{sink_name}(...)` at line {sink_line}. "
                f"Call chain: {chain}. "
                f"This is a CROSS-FUNCTION taint flow (source and sink in "
                f"different functions); the intra-function taint scanner "
                f"(taint_flow_scanner.py) would miss this."
            )
        else:
            # Direct: caller → callee → sink
            chain = (
                f"source@{caller_short}:{src_line} → "
                f"call@{caller_short}:{call_line} ({caller_info.name} → "
                f"{callee_name}) → sink@{callee_short}:{sink_line} ({sink_name})"
            )
            desc = (
                f"CrossFuncTaint [{cwe} — {cwe_title}]: tainted variable "
                f"`{tainted_var}` flows from source {src_desc} at line {src_line} "
                f"through call `{callee_name}({tainted_var})` at line {call_line} "
                f"to sink `{sink_name}(...)` at line {sink_line} in callee "
                f"`{callee_name}` (param `{callee_param}`). "
                f"Call chain: {chain}. "
                f"This is a CROSS-FUNCTION taint flow (source and sink in "
                f"different functions); the intra-function taint scanner "
                f"(taint_flow_scanner.py) would miss this."
            )

        fix = (
            f"Sanitize `{tainted_var}` before passing to `{callee_name}` at "
            f"line {call_line}. "
        )
        if cwe == "CWE-78":
            fix += (
                "Use shlex.quote() per arg, or pass an argument list with "
                "shell=False. For SCP, prefer scp.core.safe_process."
            )
        elif cwe == "CWE-89":
            fix += (
                "Use parameterized SQL inside the callee: "
                "cursor.execute('... WHERE id=?', (var,)). If the callee "
                "cannot be changed, validate/sanitize the input at the call "
                "site (e.g., regex-whitelist)."
            )
        elif cwe == "CWE-79":
            fix += (
                "Use markupsafe.escape() on the input before passing it in, "
                "or rely on Jinja2 autoescape inside the callee."
            )
        elif cwe == "CWE-502":
            fix += (
                "Avoid passing untrusted data to a function that "
                "deserializes it. Use yaml.safe_load() / json.loads() inside "
                "the callee, or validate the input at the call site."
            )
        elif cwe == "CWE-94":
            fix += (
                "Avoid passing untrusted data to a function that eval/execs "
                "it. Use ast.literal_eval() inside the callee, or refactor "
                "to a real parser."
            )

        return BugReport(
            file=caller_info.file,
            line=call_line,
            bug_type=f"CrossFuncTaint_{cwe}",
            description=desc,
            suggested_fix=fix,
            tier=BugTier.TIER_3_PERMISSION,
            affects_logic=True,
        )


# ============================================================================
# Module-level call graph cache (for scan_file efficiency)
# ============================================================================

_SCP_CALL_GRAPH_CACHE: _CrossFuncScanner | None = None


def _get_scp_call_graph() -> _CrossFuncScanner:
    """Build (once) and return the scp/ call graph.

    Subsequent calls return the cached call graph. Used by scan_file() to
    avoid rebuilding the scp/ call graph for every file scan.
    """
    global _SCP_CALL_GRAPH_CACHE
    if _SCP_CALL_GRAPH_CACHE is not None:
        return _SCP_CALL_GRAPH_CACHE

    scanner = _CrossFuncScanner()
    files_added = 0
    for path in _iter_python_files(_SCP_ROOT, limit=_MAX_FILES):
        scanner.add_file(path)
        files_added += 1
    scanner.run_fixpoint()
    logger.info(
        f"[CrossFuncTaint] built scp/ call graph: "
        f"{len(scanner.funcs_by_qualname)} functions across {files_added} files"
    )
    _SCP_CALL_GRAPH_CACHE = scanner
    return scanner


def _clone_call_graph(base: _CrossFuncScanner) -> _CrossFuncScanner:
    """Clone a call graph (for scan_file to extend with a target file)."""
    new = _CrossFuncScanner()
    for qualname, info in base.funcs_by_qualname.items():
        new.funcs_by_qualname[qualname] = info
    for name, lst in base.funcs_by_name.items():
        new.funcs_by_name[name] = list(lst)
    new._fixpoint_done = True  # base already fixpointed; will re-run on add
    return new


# ============================================================================
# Public API
# ============================================================================

def scan_file(path: Path) -> list[BugReport]:
    """Scan a single Python file for CROSS-FUNCTION taint bugs.

    Builds a call graph from scp/ + the target file, then walks the target
    file's functions with cross-function awareness. Reports bugs whose
    CALLER is in the target file.

    NOTE: this scanner ONLY reports cross-function bugs (source and sink in
    DIFFERENT functions). Intra-function bugs are reported by
    taint_flow_scanner.py's scan_file().

    Args:
        path: Path to a .py file.

    Returns:
        List of BugReports with bug_type="CrossFuncTaint_CWE-XXX".
    """
    path = Path(path)

    # Build a call graph that includes scp/ + the target file
    base = _get_scp_call_graph()
    scanner = _clone_call_graph(base)
    scanner.add_file(path)
    scanner.run_fixpoint()

    return scanner.detect_bugs(only_in_files={str(path)})


def scan_scp() -> list[BugReport]:
    """Scan the entire SCP package for CROSS-FUNCTION taint bugs.

    Walks `scp/` recursively (up to _MAX_FILES=500 files), skipping tests,
    __pycache__, examples, scripts, attack_payloads, and benchmark dirs.

    Returns:
        List of BugReports with bug_type="CrossFuncTaint_CWE-XXX". Each
        report captures a confirmed source→callee→sink dataflow across
        two or more functions (the intra-function scanner misses these).
    """
    scanner = _CrossFuncScanner()
    files_scanned = 0
    for path in _iter_python_files(_SCP_ROOT, limit=_MAX_FILES):
        files_scanned += 1
        try:
            scanner.add_file(path)
        except Exception as e:  # noqa: BLE001 — never crash the whole scan
            logger.warning(f"Error adding {path} to call graph: {e}")

    scanner.run_fixpoint()
    bugs = scanner.detect_bugs()

    logger.info(
        f"[CrossFuncTaintScanner] found {len(bugs)} cross-function taint "
        f"bug(s) across {len(scanner.funcs_by_qualname)} functions "
        f"(scanned {files_scanned} files)"
    )
    return bugs
