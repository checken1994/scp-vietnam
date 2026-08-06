#!/usr/bin/env python3
"""Check tree sync — ensure no duplicate code trees.
Run before every zip/deploy. Exit 1 if divergence found.
"""
import sys
from pathlib import Path


def check():
    root = Path(__file__).parent
    plugin_dir = root / "scp-plugin"
    if plugin_dir.exists():
        print(f"❌ SYNC GATE FAILED: scp-plugin/ exists ({len(list(plugin_dir.rglob('*.py')))} files)")
        print("  Fix: rm -rf scp-plugin/")
        sys.exit(1)
    print("✅ SYNC GATE PASSED: no scp-plugin/")
    sys.exit(0)

if __name__ == "__main__":
    check()
