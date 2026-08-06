#!/bin/bash
# [V104.36] Pre-commit hook — run WHY audit tool + behavioral tests before commit.
# TẠI SAO: prevent new silent failures from entering the codebase.
# Install: cp scripts/pre-commit-why-audit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

echo "=== SCP WHY Audit Pre-commit Hook ==="
echo ""

# 1. Run WHY audit tool — fail if CRITICAL findings
echo "[1/3] Running WHY audit tool..."
python /home/z/my-project/scripts/why_audit_tool.py --severity CRITICAL 2>&1 | tail -5
WHY_EXIT=$?
if [ $WHY_EXIT -ne 0 ]; then
    echo ""
    echo "❌ WHY audit found CRITICAL issues. Fix them before committing."
    echo "   Run: python /home/z/my-project/scripts/why_audit_tool.py"
    exit 1
fi
echo "✅ WHY audit: no CRITICAL findings"
echo ""

# 2. Run behavioral tests — fail if any fix-loss detected
echo "[2/3] Running behavioral tests (fix-loss detection)..."
cd /home/z/my-project/download/scp_v97
SCP_DEV_MODE=1 python -m pytest tests/test_v10431_behavioral.py tests/test_v10432_behavioral.py tests/test_v10434_behavioral.py tests/test_v10435_behavioral.py -q --tb=no 2>&1 | tail -3
TEST_EXIT=$?
if [ $TEST_EXIT -ne 0 ]; then
    echo ""
    echo "❌ Behavioral tests failed — a previous fix was lost (fix-loss pattern)."
    echo "   Re-apply the fix using scripts/apply_v104_*.py"
    exit 1
fi
echo "✅ Behavioral tests: all pass"
echo ""

# 3. Syntax check all Python files
echo "[3/3] Syntax check all Python files..."
python -c "
import ast, os
fails = 0
for root, dirs, files in os.walk('scp'):
    for f in files:
        if f.endswith('.py'):
            fp = os.path.join(root, f)
            try:
                with open(fp, encoding='utf-8') as fh:
                    ast.parse(fh.read(), filename=fp)
            except SyntaxError as e:
                print(f'  FAIL {fp}: {e}')
                fails += 1
if fails:
    print(f'  {fails} syntax errors')
    exit(1)
print('  All files OK')
"
SYNTAX_EXIT=$?
if [ $SYNTAX_EXIT -ne 0 ]; then
    echo ""
    echo "❌ Syntax errors detected. Fix before committing."
    exit 1
fi
echo "✅ Syntax: all files OK"
echo ""

echo "=== All pre-commit checks passed. Committing... ==="
exit 0
