# [V8.0-SCANNER] DeadCodeScanner — detect functions/classes never called.
#
# TẠI SAO scanner này tồn tại?
#   SCP codebase 184 file .py, có những function/class định nghĩa nhưng không
#   bao giờ được gọi. Chiếm namespace, làm code khó đọc, và có thể là "dead
#   branch" của refactor cũ. Scanner này detect chúng.
#
# LOGIC:
#   Pass 1: thu thập tất cả function defs + class defs trong scp/ (skip tests,
#           scripts, examples).
#     - Track {symbol_name: list[(file, line)]}
#   Pass 2: scan tất cả files (incl. tests/scripts — dead code may be used by
#            tests only) cho references:
#     - Direct call: `symbol_name(`  → in any Call.func.Name
#     - Reference: `symbol_name`     → in any Name node (load context)
#     - Attribute access: `obj.symbol_name`  → in any Attribute.attr
#     - String reference: `"symbol_name"` in decorators/registry strings
#     - Import: `from X import symbol_name` / `import X.symbol_name`
#   Pass 3: nếu symbol KHÔNG có reference nào (trừ chính file định nghĩa) →
#           DEAD CODE.
#
# EXCEPTIONS (never flag as dead):
#   - Dunder methods: `__init__`, `__str__`, `__repr__`, `__call__`, etc.
#     (called by Python implicitly)
#   - Methods starting with `_` are private but may be called by other methods
#     in same class — too noisy to flag without class tracking. SKIP private
#     methods entirely (only flag PUBLIC module-level functions/classes).
#   - API endpoints: functions with decorators like @app.route, @app.post,
#     @app.get, @router.get, @websocket, @pytest.fixture, @property, @staticmethod,
#     @classmethod — these are called via framework dispatch.
#   - main(): commonly invoked via `if __name__ == "__main__":` block
#   - Functions in __init__.py: usually __all__ exports
#
# CONSERVATIVE HEURISTICS:
#   - Only flag TOP-LEVEL defs (not nested inside other functions/classes)
#   - Only flag PUBLIC names (no leading underscore)
#   - Skip if name is too short (<= 2 chars) — too likely to be a substring match
#   - Skip if name is a common English word that may appear in strings/comments
#     ("get", "set", "run", "init", "main", "test", "data", "value")
#
# RETURNS:
#   list[BugReport] — bug_type="DeadCode"
from __future__ import annotations

import ast
import logging
from pathlib import Path

from scp.autofix.classifier import BugReport, BugTier

logger = logging.getLogger("scp.autofix.scanners.dead_code")

_SCP_ROOT = Path(__file__).resolve().parent.parent.parent  # .../scp/
_MAX_FILES = 500

# Decorators that indicate framework-dispatched (not directly called)
_FRAMEWORK_DECORATORS = {
    "app.route", "app.get", "app.post", "app.put", "app.delete", "app.patch",
    "router.get", "router.post", "router.put", "router.delete", "router.patch",
    "websocket", "websocket_route",
    "pytest.fixture", "fixture",
    "property", "staticmethod", "classmethod",
    "abstractmethod",
    "cached_property", "functools.cached_property",
    "dataclasses.dataclass", "dataclass",
    "attr.s", "attr.frozen",
    "click.command", "click.option", "click.argument",
    "celery.task", "task", "shared_task",
    "signal", "receiver",
}

# Names too generic to flag as dead (substring matches too many false positives)
_GENERIC_NAMES = {
    "get", "set", "run", "init", "main", "test", "data", "value",
    "ok", "id", "do", "go", "no", "load", "save", "open", "close", "read", "write", "start", "stop",
    "send", "recv", "put", "add", "del", "rm", "ls",
    "is_", "has_", "can_",  # leading verb fragments
    "log", "err", "out",
}


def _has_framework_decorator(node) -> bool:
    """Return True if function has a framework-dispatch decorator."""
    for dec in node.decorator_list:
        # @app.post("/x") — dec is Call with func=Attribute
        if isinstance(dec, ast.Call):
            target = dec.func
        else:
            target = dec
        # Get full dotted name: app.post / router.get / etc.
        if isinstance(target, ast.Attribute):
            parts = []
            cursor = target
            while isinstance(cursor, ast.Attribute):
                parts.insert(0, cursor.attr)
                cursor = cursor.value
            if isinstance(cursor, ast.Name):
                parts.insert(0, cursor.id)
            full = ".".join(parts)
            if full in _FRAMEWORK_DECORATORS or any(
                full.endswith(d.split(".")[-1]) for d in _FRAMEWORK_DECORATORS
            ):
                return True
        elif isinstance(target, ast.Name):
            if target.id in _FRAMEWORK_DECORATORS or any(
                target.id == d.split(".")[-1] for d in _FRAMEWORK_DECORATORS
            ):
                return True
    return False


def _iter_python_files_for_defs(root: Path, limit: int = _MAX_FILES):
    """Iterate .py files for collecting definitions (skip tests/scripts)."""
    count = 0
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "__init__.py":
            continue  # exports, not real defs
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        if any(part in ("examples", "scripts", "attack_payloads") for part in path.parts):
            continue
        if count >= limit:
            break
        yield path
        count += 1


def _iter_python_files_for_refs(root: Path, limit: int = 2000):
    """Iterate ALL .py files for reference search (include tests)."""
    count = 0
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if count >= limit:
            break
        yield path
        count += 1


class _DefCollector(ast.NodeVisitor):
    """Pass 1: collect top-level function/class definitions."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.defs: list[dict] = []  # {name, line, kind, file}

    def visit_Module(self, node: ast.Module):
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._maybe_collect(stmt, "function")
            elif isinstance(stmt, ast.ClassDef):
                self._maybe_collect(stmt, "class")
            # Don't recurse into nested defs (only top-level)

    def _maybe_collect(self, node, kind: str):
        name = node.name
        # Skip dunders
        if name.startswith("__") and name.endswith("__"):
            return
        # Skip private (leading underscore)
        if name.startswith("_"):
            return
        # Skip too-short names (substring false-positives)
        if len(name) <= 2:
            return
        # Skip generic names
        if name.lower() in _GENERIC_NAMES:
            return
        # Skip framework-dispatched (decorators)
        if _has_framework_decorator(node):
            return
        # Skip "main" — usually called via __main__ block
        if name == "main":
            return
        self.defs.append({
            "name": name,
            "line": node.lineno,
            "kind": kind,
            "file": self.filepath,
        })


class _ReferenceCounter(ast.NodeVisitor):
    """Pass 2: count references to each symbol in this file."""

    def __init__(self, target_names: set[str], def_filename: str):
        self.target_names = target_names
        self.def_filename = def_filename
        self.references: dict[str, int] = {n: 0 for n in target_names}

    def visit_Name(self, node: ast.Name):
        if node.id in self.references:
            # Don't count self-references in the def file
            self.references[node.id] += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in self.references:
            self.references[node.attr] += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Count Call target name (function call)
        if isinstance(node.func, ast.Name) and node.func.id in self.references:
            self.references[node.func.id] += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        # import X.Y — count as ref to X
        for alias in node.names:
            base = alias.name.split(".")[0]
            if base in self.references:
                self.references[base] += 1
            # alias.asname override
            if alias.asname and alias.asname in self.references:
                self.references[alias.asname] += 1

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            name = alias.asname or alias.name
            if name in self.references:
                self.references[name] += 1


class DeadCodeScanner:
    """Detect functions/classes defined but never referenced across the codebase."""

    name: str = "DeadCodeScanner"
    bug_type: str = "DeadCode"

    def __init__(self, scp_root: Path | None = None, max_files: int = _MAX_FILES):
        self.scp_root = scp_root or _SCP_ROOT
        self.max_files = max_files

    def scan(self) -> list[BugReport]:
        # Pass 1: collect all definitions
        all_defs: list[dict] = []
        for path in _iter_python_files_for_defs(self.scp_root, limit=self.max_files):
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
            collector = _DefCollector(str(path))
            collector.visit(tree)
            all_defs.extend(collector.defs)

        if not all_defs:
            logger.info("[DeadCodeScanner] no definitions to scan")
            return []

        # Pass 2: scan all files for references
        target_names = {d["name"] for d in all_defs}
        # Map symbol → total reference count across all files
        total_refs: dict[str, int] = {n: 0 for n in target_names}

        for path in _iter_python_files_for_refs(self.scp_root):
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
            counter = _ReferenceCounter(target_names, str(path))
            counter.visit(tree)
            for name, count in counter.references.items():
                total_refs[name] += count

        # Pass 3: definitions with 0 references = dead code
        # Conservative: only flag if symbol is NEVER referenced anywhere in the
        # codebase (including its own def file's body — recursive calls count
        # as references). This avoids false positives for callbacks passed to
        # frameworks (e.g. `FastAPI(lifespan=lifespan)`) — those count as 1 ref.
        bugs: list[BugReport] = []
        for d in all_defs:
            refs = total_refs[d["name"]]
            if refs == 0:
                bugs.append(BugReport(
                    file=d["file"],
                    line=d["line"],
                    bug_type=self.bug_type,
                    description=(
                        f"DeadCode: {d['kind']} `{d['name']}` defined at line "
                        f"{d['line']} in {Path(d['file']).name} but never referenced "
                        f"anywhere in the codebase. This is dead code — wastes "
                        f"namespace, confuses readers, may be a stale refactoring artifact."
                    ),
                    suggested_fix=(
                        f"Either: (a) delete the {d['kind']} if truly unused; "
                        f"(b) check git log to see when it was last used — if >6 months "
                        f"ago, likely safe to remove; (c) if it's a public API intended "
                        f"for external use, add it to module's __all__ + document it."
                    ),
                    tier=BugTier.TIER_2_AUTO_FIX_LOG,
                    affects_logic=False,
                ))

        logger.info(
            f"[DeadCodeScanner] found {len(bugs)} dead symbol(s) "
            f"(checked {len(all_defs)} top-level defs)"
        )
        return bugs
