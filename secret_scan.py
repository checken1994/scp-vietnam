#!/usr/bin/env python3
"""Secret scan — scan ALL files by CONTENT (regex), not by filename.
Run before every zip/deploy. Exit 1 if secrets found.
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    (r'sk-or-v1-[a-f0-9]{40,}', 'OpenRouter key'),
    (r'gsk_[A-Za-z0-9]{30,}', 'Groq key'),
    (r'sk-[a-zA-Z0-9]{40,}', 'OpenAI key'),
    (r'ghp_[a-zA-Z0-9]{30,}', 'GitHub token'),
]

def scan(root='.'):
    findings = []
    for f in Path(root).rglob('*'):
        if f.is_dir() or '__pycache__' in str(f) or '.git/' in str(f):
            continue
        if f.suffix in ('.pyc', '.db', '.sqlite', '.png', '.jpg', '.zip'):
            continue
        try:
            content = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for pattern, name in PATTERNS:
            for m in re.findall(pattern, content):
                if 'REDACTED' in m or 'REPLACE' in m or 'YOUR' in m:
                    continue
                findings.append((name, str(f), m[:20]+'...'))
    if findings:
        print(f"❌ SECRET SCAN FAILED: {len(findings)} leak(s)")
        for n,p,pr in findings: print(f"  [{n}] {p} ({pr})")
        sys.exit(1)
    print("✅ SECRET SCAN PASSED")
    sys.exit(0)

if __name__ == "__main__":
    scan(sys.argv[1] if len(sys.argv)>1 else '.')
