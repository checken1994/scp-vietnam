#!/usr/bin/env python3
"""
SCP Dependency Analyzer V3 — Production Ready (Linux + V105 FIXED)
===================================================================
- Fix dead code line 171 (ast.Call checked twice)
- Adapted cho Linux path (không Windows)
- Point to SCP V105 FIXED source
- Multiprocessing + SQLite cache + incremental
- Bắt static + dynamic + conditional import
- Memory-safe: không giữ AST trong RAM

Usage:
    python3 scp_analyzer_v3.py [--root /path/to/scp-vietnam] [--impact scp/runtime/judge.py]

Author: Z.ai Code (fix từ V2 của user)
"""

import ast
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# ============================================================================
# CONFIG — Sửa path này cho đúng máy
# ============================================================================
SCP_ROOT = Path(os.environ.get("SCP_ROOT", "/home/z/my-project/upload/scp_vietnam_extracted/scp-vietnam"))
CACHE_DB = SCP_ROOT / ".scp_analysis_cache.db"
MAX_WORKERS = min(6, os.cpu_count() or 4)

# ============================================================================
# DATABASE CACHE — Incremental parsing
# ============================================================================
class AnalysisCache:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        # [Task 19-B / Mục 16] Enable WAL + busy_timeout to survive
        # concurrent writes from ProcessPoolExecutor workers (was the
        # root cause of "database disk image is malformed" errors).
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_analysis (
                path TEXT PRIMARY KEY,
                mtime REAL,
                sha256 TEXT,
                imports TEXT,
                definitions TEXT,
                calls TEXT,
                dynamic_imports TEXT,
                parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS impact_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_file TEXT,
                impacted_files TEXT,
                test_results TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # [FIX-V3] Add index for faster lookup
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_analysis_path ON file_analysis(path)")
        conn.commit()
        conn.close()

    def get(self, fpath: str, mtime: float, sha256: str):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        # [Task 19-B / Mục 16] per-connection pragmas for concurrent-safe reads
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        cur = conn.execute(
            "SELECT imports, definitions, calls, dynamic_imports FROM file_analysis WHERE path=? AND mtime=? AND sha256=?",
            (fpath, mtime, sha256)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return {
                "imports": json.loads(row[0]),
                "definitions": json.loads(row[1]),
                "calls": json.loads(row[2]),
                "dynamic_imports": json.loads(row[3]),
            }
        return None

    def set(self, fpath: str, mtime: float, sha256: str, data: dict):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        # [Task 19-B / Mục 16] per-connection pragmas + retry on busy
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        for attempt in range(3):
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO file_analysis
                       (path, mtime, sha256, imports, definitions, calls, dynamic_imports)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (fpath, mtime, sha256,
                     json.dumps(data["imports"], ensure_ascii=False),
                     json.dumps(data["definitions"], ensure_ascii=False),
                     json.dumps(data["calls"], ensure_ascii=False),
                     json.dumps(data["dynamic_imports"], ensure_ascii=False))
                )
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
        conn.close()

    def log_impact(self, changed_file: str, impacted: list, tests: dict):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        conn.execute(
            "INSERT INTO impact_log (changed_file, impacted_files, test_results) VALUES (?, ?, ?)",
            (changed_file, json.dumps(impacted), json.dumps(tests))
        )
        conn.commit()
        conn.close()

# ============================================================================
# PARSER — Trích xuất thông tin, không giữ AST
# ============================================================================
def _sha256_file(fpath: Path) -> str:
    h = hashlib.sha256()
    with open(fpath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def parse_single_file(args):
    """Hàm chạy trong process pool — phải là top-level function"""
    fpath_str, root_str = args
    fpath = Path(fpath_str)
    root = Path(root_str)

    try:
        mtime = fpath.stat().st_mtime
        sha = _sha256_file(fpath)

        # Check cache
        # [Task 19-B / Mục 16] Wrap cache in try/except — if SQLite is
        # corrupted/locked (concurrent multi-process writes), skip cache
        # gracefully and parse fresh. NEVER fail the whole parse because
        # of a cache error.
        rel_path = str(fpath.relative_to(root))
        cache = None
        try:
            cache = AnalysisCache(Path(root_str) / ".scp_analysis_cache.db")
            cached = cache.get(rel_path, mtime, sha)
        except Exception:
            cached = None
        if cached:
            return {"status": "cached", "path": rel_path, "data": cached}

        source = fpath.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return {"status": "syntax_error", "path": rel_path, "error": str(e)}

        imports = []
        definitions = []
        calls = []
        dynamic_imports = []

        for node in ast.walk(tree):
            # --- Static imports ---
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"type": "import", "name": alias.name, "alias": alias.asname, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append({"type": "from", "module": module, "name": alias.name, "alias": alias.asname, "line": node.lineno})

            # --- Definitions ---
            elif isinstance(node, ast.FunctionDef):
                definitions.append({"type": "function", "name": node.name, "line": node.lineno, "args": [a.arg for a in node.args.args]})
            elif isinstance(node, ast.ClassDef):
                definitions.append({"type": "class", "name": node.name, "line": node.lineno})
            elif isinstance(node, ast.AsyncFunctionDef):
                definitions.append({"type": "async_function", "name": node.name, "line": node.lineno})

            # --- Calls (FIX V3: gộp logic ast.Call, không dead code) ---
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                    calls.append({"name": call_name, "line": node.lineno, "context": "direct"})
                    # [FIX-V3] Check dynamic import trong cùng block (không dead code)
                    if call_name == "__import__":
                        dynamic_imports.append({"type": "__import__", "line": node.lineno})
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                    calls.append({"name": call_name, "line": node.lineno, "context": "method"})
                    # [FIX-V3] Check importlib.import_module trong cùng block
                    if call_name == "import_module":
                        dynamic_imports.append({"type": "importlib", "line": node.lineno})

        # [FIX-V3] Regex scan cho dynamic imports mà AST miss (string-based __import__)
        if "__import__" in source and not any(d["type"] == "__import__" for d in dynamic_imports):
            for i, line in enumerate(source.split("\n"), 1):
                if "__import__" in line and "#" not in line.split("__import__")[0]:
                    dynamic_imports.append({"type": "__import___regex", "line": i})

        data = {
            "imports": imports,
            "definitions": definitions,
            "calls": calls,
            "dynamic_imports": dynamic_imports,
        }
        # [Task 19-B / Mục 16] Cache write wrapped — never fail parse on cache error
        if cache is not None:
            try:
                cache.set(rel_path, mtime, sha, data)
            except Exception:
                pass
        return {"status": "parsed", "path": rel_path, "data": data}

    except Exception as e:
        return {"status": "error", "path": str(fpath), "error": str(e)}

# ============================================================================
# ORCHESTRATOR
# ============================================================================
class SCPAnalyzerV3:
    def __init__(self, root: Path):
        self.root = root
        self.cache = AnalysisCache(root / ".scp_analysis_cache.db")
        self.files = {}
        self.import_index = defaultdict(list)
        self.call_index = defaultdict(list)
        self.def_index = defaultdict(list)

    def scan_all(self):
        py_files = [f for f in self.root.rglob("*.py")
                    if "__pycache__" not in str(f) and ".git" not in str(f)]
        total = len(py_files)
        print(f"[*] Found {total} Python files")
        print(f"[*] Using {MAX_WORKERS} workers")
        print(f"[*] Root: {self.root}")

        parsed = 0
        cached = 0
        errors = 0
        start = time.time()
        args_list = [(str(f), str(self.root)) for f in py_files]

        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for result in executor.map(parse_single_file, args_list):
                if result["status"] in ("parsed", "cached"):
                    if result["status"] == "parsed":
                        parsed += 1
                    else:
                        cached += 1
                    self.files[result["path"]] = result["data"]
                elif result["status"] == "syntax_error":
                    errors += 1
                    print(f"  [!] SYNTAX ERROR: {result['path']} — {result.get('error', '')}")
                elif result["status"] == "error":
                    errors += 1
                    print(f"  [!] ERROR: {result['path']} — {result.get('error', '')}")

                if (parsed + cached) % 50 == 0:
                    print(f"  ... {parsed + cached}/{total} done")

        elapsed = time.time() - start
        print(f"[+] Scan complete in {elapsed:.1f}s")
        print(f"    Parsed: {parsed} | Cached: {cached} | Errors: {errors}")
        self._build_indices()

    def _build_indices(self):
        print("[*] Building indices...")
        for fpath, data in self.files.items():
            for imp in data["imports"]:
                key = imp.get("name", imp.get("module", ""))
                self.import_index[key].append(fpath)
            for call in data["calls"]:
                self.call_index[call["name"]].append(fpath)
            for df in data["definitions"]:
                self.def_index[df["name"]].append(fpath)
        print(f"[+] Indices: {len(self.import_index)} imports, {len(self.call_index)} calls, {len(self.def_index)} defs")

    def find_dependents(self, target_rel: str) -> list:
        """Tìm tất cả file import file target"""
        target_mod = target_rel.replace("\\", "/").replace("/", ".").replace(".py", "")
        results = []
        for fpath, data in self.files.items():
            for imp in data["imports"]:
                name = imp.get("name", "")
                module = imp.get("module", "")
                if target_mod.endswith(name) or target_mod.endswith(module) or name in target_mod or module in target_mod:
                    results.append({
                        "file": fpath,
                        "import_name": name or module,
                        "line": imp["line"],
                        "type": imp["type"]
                    })
        return results

    def trace_callers(self, func_name: str) -> list:
        """Tìm tất cả file gọi hàm func_name"""
        results = []
        for fpath, data in self.files.items():
            for call in data["calls"]:
                if call["name"] == func_name:
                    results.append({"file": fpath, "line": call["line"], "context": call["context"]})
        return results

    def impact_analysis(self, changed_file: str):
        """Phân tích toàn diện khi sửa 1 file"""
        print(f"\n{'='*70}")
        print(f"IMPACT ANALYSIS: {changed_file}")
        print(f"{'='*70}")

        deps = self.find_dependents(changed_file)
        print(f"\n[1] DIRECT IMPORT DEPENDENTS ({len(deps)} files):")
        for d in deps[:20]:
            print(f"    → {d['file']} (import '{d['import_name']}' at line {d['line']})")
        if len(deps) > 20:
            print(f"    ... và {len(deps)-20} file khác")

        rel = changed_file.replace("\\", "/")
        funcs = [d for d in self.files.get(rel, {}).get("definitions", []) if d["type"] in ("function", "async_function")]

        print(f"\n[2] FUNCTION CALL CHAIN ({len(funcs)} functions):")
        total_callers = 0
        for func in funcs:
            callers = self.trace_callers(func["name"])
            if callers:
                total_callers += len(callers)
                print(f"    → {func['name']}() called by {len(callers)} locations:")
                for c in callers[:5]:
                    print(f"        {c['file']}:{c['line']}")
                if len(callers) > 5:
                    print(f"        ... and {len(callers)-5} more")

        dyn = self.files.get(rel, {}).get("dynamic_imports", [])
        if dyn:
            print(f"\n[!] DYNAMIC IMPORTS ({len(dyn)}):")
            for d in dyn:
                print(f"    Line {d['line']}: {d['type']}")

        print("\n[3] CIRCULAR DEPENDENCY CHECK:")
        cycles = self._detect_cycles(changed_file)
        if cycles:
            for cycle in cycles[:5]:
                print(f"    [!] CYCLE: {' -> '.join(cycle)}")
        else:
            print("    ✓ No circular dependency detected")

        return {
            "changed": changed_file,
            "dependents": deps,
            "functions": funcs,
            "total_callers": total_callers,
            "dynamic_imports": dyn,
        }

    def _detect_cycles(self, start_file: str, max_depth=5):
        cycles = []
        start_file.replace("\\", "/").replace(".py", "")

        def dfs(current, path, depth):
            if depth > max_depth:
                return
            current_data = self.files.get(current.replace("\\", "/"), {})
            for imp in current_data.get("imports", []):
                name = imp.get("name", imp.get("module", ""))
                for f in self.files:
                    f_clean = f.replace("\\", "/").replace(".py", "")
                    if name in f_clean or f_clean.endswith(name.replace(".", "/")):
                        if f == start_file:
                            cycles.append(path + [f])
                        elif f not in path:
                            dfs(f, path + [f], depth + 1)

        dfs(start_file, [start_file], 0)
        return cycles

    def find_orphans(self) -> list:
        """[V3 NEW] Tìm orphan files — 0 importers, không phải entry point"""
        in_degree = defaultdict(int)
        for _fpath, data in self.files.items():
            for imp in data["imports"]:
                name = imp.get("name", imp.get("module", ""))
                for target in self.files:
                    if name in target:
                        in_degree[target] += 1

        entry_suffixes = ("__init__.py", "test_", "conftest", "run_", "main.py", "start_", "setup.py", "scp_analyzer", "scp_simple")
        orphans = [f for f in self.files if in_degree.get(f, 0) == 0
                   and not any(f.endswith(s) for s in entry_suffixes)]
        return orphans

    def export_graph(self, output_path: Path):
        """Xuất dependency graph dạng JSON"""
        graph = {"nodes": list(self.files.keys()), "edges": []}
        for fpath, data in self.files.items():
            for imp in data["imports"]:
                name = imp.get("name", imp.get("module", ""))
                for target in self.files:
                    if name in target:
                        graph["edges"].append({"from": fpath, "to": target, "line": imp["line"]})

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        print(f"[+] Graph exported: {output_path}")

# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SCP Dependency Analyzer V3")
    parser.add_argument("--root", default=str(SCP_ROOT), help="Root directory")
    parser.add_argument("--impact", default=None, help="Impact analysis for specific file")
    parser.add_argument("--orphans", action="store_true", help="Find orphan files")
    parser.add_argument("--export", default=None, help="Export graph to JSON")
    args = parser.parse_args()

    analyzer = SCPAnalyzerV3(Path(args.root))
    analyzer.scan_all()

    if args.impact:
        analyzer.impact_analysis(args.impact)

    if args.orphans:
        orphans = analyzer.find_orphans()
        print(f"\n{'='*70}")
        print(f"ORPHAN FILES: {len(orphans)}")
        print(f"{'='*70}")
        for o in orphans[:20]:
            print(f"  {o}")
        if len(orphans) > 20:
            print(f"  ... và {len(orphans)-20} file khác")

    if args.export:
        analyzer.export_graph(Path(args.export))

    if not args.impact and not args.orphans and not args.export:
        # Default: impact analysis 3 file cốt lõi
        for target in ["scp/runtime/engine.py", "scp/runtime/judge.py", "scp/meta/governance_v97.py"]:
            if target in analyzer.files:
                analyzer.impact_analysis(target)
        orphans = analyzer.find_orphans()
        print(f"\n{'='*70}")
        print(f"ORPHAN FILES: {len(orphans)}")
        print(f"{'='*70}")
        for o in orphans[:20]:
            print(f"  {o}")

    print("\n[*] Done.")
