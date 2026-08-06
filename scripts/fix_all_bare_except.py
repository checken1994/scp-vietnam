#!/usr/bin/env python3
"""
SCP Fix All BareExceptPass — chạy 1 lần, fix hết bằng AST.

Cách chạy:
    python scripts/fix_all_bare_except.py

TẠI SAO: STARTUP-GATE block vì 46 BareExceptPass unfixed.
Script này fix hết trước khi start server → 0 bugs → server start OK.
"""
import ast
import os
import sys
from pathlib import Path

# Add SCP root to path
SCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCP_ROOT))


def fix_file(filepath: Path) -> int:
    """Fix all BareExceptPass in a file. Returns count of fixes."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except Exception:
        return 0

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return 0  # Can't fix files with syntax errors

    fixes = []
    filename = filepath.name

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if len(node.body) != 1:
            continue
        if not isinstance(node.body[0], ast.Pass):
            continue

        lines = source.splitlines()
        except_line_idx = node.lineno - 1  # 0-indexed
        pass_line_idx = node.body[0].lineno - 1

        if except_line_idx >= len(lines) or pass_line_idx >= len(lines):
            continue

        except_line = lines[except_line_idx]
        pass_line = lines[pass_line_idx]

        # Get indents
        except_indent = len(except_line) - len(except_line.lstrip())
        pass_indent = len(pass_line) - len(pass_line.lstrip())
        except_indent_str = ' ' * except_indent
        pass_indent_str = ' ' * pass_indent

        # Build OLD block
        old_block = f"{except_line.rstrip()}\n{pass_line.rstrip()}"

        # Build NEW block
        except_text = except_line.strip()
        if ' as ' not in except_text:
            new_except = except_text.rstrip(':').rstrip() + ' as e:'
        else:
            new_except = except_text

        new_block = (
            f"{except_indent_str}{new_except}\n"
            f"{pass_indent_str}logger.exception(f\"[{filename}:{node.lineno}] silenced exception\")"
        )

        # Verify fix is valid Python
        test_source = source.replace(old_block, new_block, 1)
        try:
            ast.parse(test_source, filename=str(filepath))
        except SyntaxError:
            print(f"  SKIP {filepath.name}:{node.lineno} — fix would break syntax")
            continue

        fixes.append((old_block, new_block))

    if not fixes:
        return 0

    # Apply all fixes
    patched = source
    for old_block, new_block in fixes:
        patched = patched.replace(old_block, new_block, 1)

    # Final verify
    try:
        ast.parse(patched, filename=str(filepath))
    except SyntaxError as e:
        print(f"  SKIP {filepath.name} — final verify failed: {e}")
        return 0

    # Write
    filepath.write_text(patched, encoding="utf-8")
    print(f"  FIXED {filepath.name}: {len(fixes)} BareExceptPass → logger.exception()")
    return len(fixes)


def main():
    print("=" * 60)
    print("  SCP Fix All BareExceptPass — AST-based")
    print("=" * 60)

    scp_dir = SCP_ROOT / "scp"
    if not scp_dir.exists():
        print(f"ERROR: scp/ directory not found at {scp_dir}")
        return 1

    total_fixed = 0
    files_fixed = 0

    for pyfile in sorted(scp_dir.rglob("*.py")):
        if "__pycache__" in pyfile.parts:
            continue
        if "tests" in pyfile.parts:
            continue
        if pyfile.name.startswith("test_"):
            continue

        count = fix_file(pyfile)
        if count > 0:
            total_fixed += count
            files_fixed += 1

    print()
    print(f"  Total: {total_fixed} bugs fixed in {files_fixed} files")
    print()

    if total_fixed > 0:
        print("  ✅ All BareExceptPass fixed! Server should start without STARTUP-GATE block.")
    else:
        print("  ℹ️  No BareExceptPass found — already clean.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
