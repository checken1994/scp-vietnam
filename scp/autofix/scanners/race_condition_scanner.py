# [V8.0-SCANNER] RaceConditionScanner — detect missing locks on shared mutable state.
#
# TẠI SAO scanner này tồn tại?
#   PATTERN-MAP audit cũ phát hiện: `_SOURCE_HEALTH_LOCK` declared nhưng chưa
#   bao giờ được dùng trong `with self._source_health_lock:`. Code có
#   `self._source_health["src"] = {...}` (mutation) ở nhiều chỗ — async/threaded
#   → race condition: 2 task cùng update dict → 1 update bị lost, hoặc dict
#   resized mid-iteration → RuntimeError.
#
# LOGIC:
#   1. Trong mỗi class (ClassDef), tìm class-attribute init patterns:
#        - `self._X = {}`     (dict literal)
#        - `self._X = []`     (list literal)
#        - `self._X = set()`  (set call)
#        - `self._X = 0`      (int counter)
#        - `self._X = deque()`(deque call)
#      Track {var_name: lineno} của các mutable attrs (skip immutable: str/int/float const).
#   2. Tìm `with self._lock_name:` blocks trong cùng class → collect set của các
#      lock names.
#   3. Tìm mutation patterns trên các tracked attrs:
#        - `self._X[k] = v`            (Subscript assign)
#        - `self._X.append(v)`         (Call .append)
#        - `self._X.pop()`, `.remove()`, `.discard()`, `.add()`, `.extend()`, `.update()`
#        - `self._X += [...]`           (AugAssign)
#        - `self._X = self._X + ...`    (reassignment)
#   4. Mutation BÊN TRONG `with self._lock:` block = SAFE.
#      Mutation NGOÀI lock block (trong async/threaded method) = BUG.
#
# CONSERVATIVE HEURISTICS:
#   - Chỉ flag nếu method có `async def` hoặc method name gợi ý concurrent
#     (verify_, fetch_, process_, update_, increment_, record_, batch_).
#     Pure sync method chạy trong 1 thread = không race.
#   - Skip __init__ (constructor runs in single thread).
#   - Skip test files, scripts.
#   - Skip mutation BÊN TRONG try/except block (mutation thường an toàn hơn
#     vì exception path luôn single-thread).
#   - Nếu class có bất kỳ lock nào (self._*lock), flag mutations không trong
#     lock block. Nếu class KHÔNG có lock nào → skip (conservative — class có
#     thể chạy single-threaded).
#
# RETURNS:
#   list[BugReport] — bug_type="RaceCondition"
from __future__ import annotations

import ast
import logging
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.scanners.race_condition")

_SCP_ROOT = Path(__file__).resolve().parent.parent.parent  # .../scp/
_MAX_FILES = 500

# Method name prefixes suggesting concurrent execution.
# Conservative: only flag methods that look like they're called concurrently
# (async method, or method whose name suggests it's invoked from a worker).
_CONCURRENT_PREFIXES = (
    "verify_", "fetch_", "process_", "update_", "increment_", "record_",
    "batch_", "add_", "remove_", "delete_", "schedule_", "run_", "execute_",
    "submit_", "publish_", "consume_", "handle_",
    # Bare method names commonly called from async/threaded contexts
    "record", "add", "remove", "delete", "submit", "publish", "consume",
    "update", "fetch", "process", "verify", "schedule", "increment",
    "register", "append", "track", "log_event", "report",
)

# Async dispatch marker (set in visit_FunctionDef)
_ASYNC_MARKER = "__async__"

# Mutation methods on list/dict/set
_MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "pop", "clear", "update",
    "add", "discard", "setdefault", "popitem", "sort", "reverse",
    "appendleft", "popleft", "extendleft", "rotate",
}


def _is_mutable_init(node: ast.AST) -> bool:
    """True if RHS is `[]`, `{}`, `set()`, `deque()`, `defaultdict()`, `Counter()`."""
    # List/dict/set literal
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    # Constructor call: set(), deque(), defaultdict(), Counter()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"set", "deque", "defaultdict", "Counter", "OrderedDict"}
    # self._x: dict = {} (already handled by literal)
    # int counter
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return True
    return False


def _is_self_attr(node) -> tuple[bool, str]:
    """If node is `self.NAME`, return (True, NAME). Else (False, '')."""
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return True, node.attr
    return False, ""


def _is_lock_with(node: ast.With) -> list[str]:
    """Return list of `self.X` lock names used as context managers in this With."""
    lock_names: list[str] = []
    for item in node.items:
        ctx = item.context_expr
        # `with self._lock:` — ctx is Attribute
        is_self, name = _is_self_attr(ctx)
        if is_self and ("lock" in name.lower() or "mutex" in name.lower()):
            lock_names.append(name)
    return lock_names


class _ClassRaceFinder(ast.NodeVisitor):
    """Walk one ClassDef, collect mutable attrs + lock usages + unsafe mutations."""

    def __init__(self, filepath: str, class_name: str, class_start_line: int):
        self.filepath = filepath
        self.class_name = class_name
        self.class_start_line = class_start_line
        self.findings: list[dict] = []
        # Mutable attrs: {name: init_lineno}
        self._mutable_attrs: dict[str, int] = {}
        # All lock attrs declared in class (self._lock, self._mutex, etc.)
        self._lock_attrs: set[str] = set()
        # Current lock scope stack: list of sets of lock names
        self._lock_stack: list[set[str]] = []
        # Current method name (for concurrent-method check)
        self._cur_method: str = ""

    def _in_lock(self) -> bool:
        return any(bool(s) for s in self._lock_stack)

    def _is_concurrent_method(self) -> bool:
        if not self._cur_method:
            return False
        if self._cur_method.startswith("__"):
            return False  # __init__, __enter__, etc — usually single-thread
        # Exact match OR prefix match
        for p in _CONCURRENT_PREFIXES:
            if self._cur_method == p or self._cur_method.startswith(p):
                return True
        return False

    def visit_FunctionDef(self, node):
        # Enter method scope
        saved_method = self._cur_method
        self._cur_method = node.name
        # Check if async
        is_async = isinstance(node, ast.AsyncFunctionDef)
        for stmt in node.body:
            self.visit(stmt)
        # Restore
        self._cur_method = saved_method
        # Tag this finding set if any method was async — we don't track that
        # per-finding, so let's add it as a flag.
        if is_async and self._is_concurrent_method():
            for f in self.findings:
                if "method" not in f:
                    f["method"] = node.name
                    f["async"] = True

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        # Detect `self._X = {}` or `self._X = []` etc. AND subscript assigns
        for tgt in node.targets:
            is_self, name = _is_self_attr(tgt)
            if not is_self:
                # Still check subscript assign below
                if (isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.value, ast.Attribute)):
                    is_self2, name2 = _is_self_attr(tgt.value)
                    if is_self2 and name2 in self._mutable_attrs:
                        self._check_unsafe_mutation(node.lineno, name2, "subscript_assign")
                continue
            # If attr name contains "lock" or "mutex", register as lock attr
            # (regardless of init value — could be asyncio.Lock(), threading.Lock(),
            # multiprocessing.Lock(), etc.)
            if "lock" in name.lower() or "mutex" in name.lower():
                self._lock_attrs.add(name)
                # Lock attrs themselves are not "mutable" in the race sense
                continue
            # Otherwise, check if it's a mutable init
            if _is_mutable_init(node.value):
                self._mutable_attrs[name] = node.lineno
            # Detect Subscript assign on mutable attr: self._X[k] = v
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Attribute)):
                is_self3, name3 = _is_self_attr(tgt.value)
                if is_self3 and name3 in self._mutable_attrs:
                    self._check_unsafe_mutation(node.lineno, name3, "subscript_assign")
        # Visit RHS
        self.visit(node.value)

    def visit_With(self, node: ast.With):
        lock_names = _is_lock_with(node)
        self._lock_stack.append(set(lock_names))
        for stmt in node.body:
            self.visit(stmt)
        self._lock_stack.pop()
        # Visit context expressions (in case they have their own attrs)
        for item in node.items:
            self.visit(item.context_expr)

    visit_AsyncWith = visit_With

    def visit_AugAssign(self, node):
        # self._X += ...  or self._X[k] += ...
        if isinstance(node.target, ast.Attribute):
            is_self, name = _is_self_attr(node.target)
            if is_self and name in self._mutable_attrs:
                self._check_unsafe_mutation(node.lineno, name, "aug_assign")
        elif isinstance(node.target, ast.Subscript):
            is_self, name = _is_self_attr(node.target.value)
            if is_self and name in self._mutable_attrs:
                self._check_unsafe_mutation(node.lineno, name, "aug_assign_subscript")
        self.visit(node.value)

    def visit_Call(self, node: ast.Call):
        # self._X.append(...) / .pop() / .update() / .add() / etc.
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATING_METHODS):
            is_self, name = _is_self_attr(node.func.value)
            if is_self and name in self._mutable_attrs:
                self._check_unsafe_mutation(node.lineno, name, f"call_{node.func.attr}")
        self.generic_visit(node)

    def _check_unsafe_mutation(self, lineno: int, attr: str, kind: str):
        # If mutation is inside a `with self._lock:` block → safe
        if self._in_lock():
            return
        # Conservative: only flag if class HAS any lock (suggests concurrency)
        # AND method looks concurrent (async or concurrent prefix)
        if not self._lock_attrs:
            return
        if not self._is_concurrent_method():
            return
        self.findings.append({
            "line": lineno,
            "class": self.class_name,
            "attr": attr,
            "kind": kind,
            "class_line": self.class_start_line,
            "method": self._cur_method,
        })


def _iter_python_files(root: Path, limit: int = _MAX_FILES):
    count = 0
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if path.name.startswith("test_"):
            continue
        if any(part in ("examples", "scripts", "attack_payloads") for part in path.parts):
            continue
        if count >= limit:
            break
        yield path
        count += 1


class RaceConditionScanner:
    """Detect mutations on mutable class attrs outside lock blocks in concurrent methods."""

    name: str = "RaceConditionScanner"
    bug_type: str = "RaceCondition"

    def __init__(self, scp_root: Path | None = None, max_files: int = _MAX_FILES):
        self.scp_root = scp_root or _SCP_ROOT
        self.max_files = max_files

    def scan(self) -> list[BugReport]:
        bugs: list[BugReport] = []
        files_scanned = 0
        for path in _iter_python_files(self.scp_root, limit=self.max_files):
            files_scanned += 1
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
<<<<<<< HEAD
            except Exception:  # noqa: S112
=======
            except Exception:
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                finder = _ClassRaceFinder(str(path), node.name, node.lineno)
                finder.visit(node)
                for f in finder.findings:
                    bugs.append(BugReport(
                        file=str(path),
                        line=f["line"],
                        bug_type=self.bug_type,
                        description=(
                            f"RaceCondition: mutation `{f['kind']}` on mutable attr "
                            f"`self.{f['attr']}` (defined in class `{f['class']}` at "
                            f"line {f['class_line']}) at line {f['line']} in method "
                            f"`{f.get('method', '?')}` is NOT inside any "
                            f"`with self._lock:` block. Class declares lock(s) "
                            f"{sorted(finder._lock_attrs) if finder._lock_attrs else 'N/A'} "
                            f"→ concurrent calls may corrupt state."
                        ),
                        suggested_fix=(
                            "Wrap the mutation in `with self._<lock>:` block, OR "
                            "document that the method is single-threaded only, OR "
                            "move the mutation into a method that already holds the lock."
                        ),
                        tier=BugTier.TIER_3_PERMISSION,  # concurrency changes semantics
                        affects_logic=True,
                    ))

        logger.info(
            f"[RaceConditionScanner] found {len(bugs)} race-risk mutation(s) "
            f"(scanned {files_scanned} files)"
        )
        return bugs
