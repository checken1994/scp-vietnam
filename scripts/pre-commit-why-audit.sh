#!/usr/bin/env bash
# [V104.36] Pre-commit WHY audit — runs behavioral tests + pattern checks.
# TẠI SAO: Catch fix-loss BEFORE it reaches the repo. DNA SCP #6 Evidence.
set -e
echo "[pre-commit] Running WHY audit..."

# 1. Check for hardcoded dummy API key
if grep -r "AIzaSyDummyKey\|hardcoded_dummy_api_key" scp/ --include="*.py"; then
    echo "❌ FAIL: hardcoded_dummy_api_key found"
    exit 1
fi

# 2. Check for dead code (if False:)
if grep -rn "if False:" scp/ --include="*.py"; then
    echo "❌ FAIL: if_False_dead_code found"
    exit 1
fi

# 3. Run behavioral tests (fast subset)
python -m pytest tests/test_v10442_behavioral.py tests/test_p2_behavioral_fixes.py --tb=short -q || {
    echo "⚠️  Behavioral tests failed — review before commit"
    exit 1
}

echo "✅ WHY audit passed"
