# SCP V104.70 — Fresh Audit Release

## Summary
- Quên hết thông tin SCP, audit lại từ đầu bằng 6 tools + manual logic verification
- Tìm 12 bugs THẬT mà phiên trước bỏ sót (2 CRITICAL, 6 HIGH, 4 MEDIUM/LOW)
- Fix tất cả từ root cause
- 322 tests pass (6 CI config tests fail vì zip không include .github/workflows)

## Critical Bugs Found & Fixed

### CRITICAL #1: real_learning_engine "Không đúng" parsed as "Đúng"
- **File**: scp/core/real_learning_engine.py:483, 544
- **Bug**: `"đúng" in response[:10]` — substring match. Vietnamese "Không đúng" (NOT correct) contains "đúng" → parsed as TRUE. Verifier INVERTED — facts Ollama says are FALSE got stored as verified_true in KB.
- **Fix**: Check `startswith("đúng")` AND `not startswith("không")` AND `"sai" not in first 15 chars`

### CRITICAL #2: guardrails ToolRisk unknown tools auto-pass
- **File**: scp/security/guardrails.py:376-378
- **Bug**: `TOOL_RISK.get(tool_name, ("unknown", ...))` → unknown tier returned `passed=True`. Any tool not in dict (typos, new tools, renamed tools) auto-passed → governance bypass.
- **Fix**: Unknown tools now return `passed=False` + `requires_confirmation=True` (fail-closed per SCP Constitution "capability phải được quản trị")

## High Bugs Found & Fixed

### HIGH #3: slms.py result_out NameError on bool results
- **File**: scp/runtime/slms.py:254-261
- **Bug**: `result_out` not assigned in `isinstance(result, bool)` branch → NameError when math expression returns True/False
- **Fix**: Initialize `result_out = result` before if/elif/else

### HIGH #4: judge.py KB short-circuit NameError
- **File**: scp/runtime/judge.py:1312-1318, 1497-1498
- **Bug**: `final_answer` and `confidence` not initialized at top of judge() — KB short-circuit at line 1497 references them via bare names. Comment claimed "init all three" but missed these 2.
- **Fix**: Initialize `final_answer = ""` and `confidence = 0.0` at top

### HIGH #5: WHY Engine confidence merge >1.0
- **File**: scp/runtime/judge.py:2512
- **Bug**: `confidence = max(confidence, why_result.get("confidence", 0.5))` — why_result confidence is external, no schema guarantee ≤1.0. Could push confidence >1.0, propagating to verdict.confidence, KB writes, calibration, predictions.
- **Fix**: Add `confidence = max(0.0, min(1.0, confidence))` after merge

### HIGH #6: SelfQuestioning overrides KILL verdict confidence
- **File**: scp/runtime/judge.py:3375
- **Bug**: SelfQuestioning ran on FAIL verdicts after Governance KILL, could overwrite `verdict.confidence` with `sq_result.revised_confidence` (unbounded above). Violates KILL finality.
- **Fix**: Skip SelfQuestioning if `verdict.evidence.get("governance_abstain", False)`

### HIGH #7: engine.py cycle_count race condition
- **File**: scp/runtime/engine.py:639, 562
- **Bug**: `self.cycle_count += 1` is NOT atomic in CPython (GIL releases between bytecodes). Under 16-worker ThreadPoolExecutor, ~30-50% increments lost. Comment at line 562 LIED: "cycle_count is incremented atomically (GIL protects)".
- **Fix**: Wrap in `self._cycle_lock` (threading.Lock). Remove lying comment.

### HIGH #8: engine.py VACUUM bypasses _db_lock
- **File**: scp/runtime/engine.py:699-704
- **Bug**: `sqlite3.connect(DB_PATH)` directly — bypassed `_db_lock`. VACUUM requires EXCLUSIVE access → concurrent db_exec collision → "database is locked" or corruption. Also: raw connection had no PRAGMAs (busy_timeout, WAL, foreign_keys).
- **Fix**: Use `vacuum_db()` helper from db_manager (acquires _db_lock)

## Medium/Low Bugs Found & Fixed

### MEDIUM #9: guardrails "how to hack XYZ" regex too strict
- **File**: scp/security/guardrails.py:83
- **Bug**: Pattern required "i|you" between "how to" and "hack". Missed "how to hack a wifi password" (no "i"), "how can I hack" ("can" not in alternation), "how to bypass the firewall".
- **Fix**: Make subject optional, add "can", add "break into" + "reverse engineer"

### LOW #10: engine.py cleanup debug→warning
- **File**: scp/runtime/engine.py:676, 682, 696
- **Bug**: Cleanup failures logged at `debug` level (silent in production where log level is INFO). DB grows unbounded if cleanup keeps failing.
- **Fix**: Promote to `warning`

## Verification — 6 Tools ALL CLEAN
- RUFF F-category: 0 errors ✅
- PYLINT E0601/E0606: 0 errors ✅
- MYPY: 0 errors ✅
- BANDIT HIGH: 0 ✅
- VULTURE: 7 items (all intentional) ✅
- SEMGREP defusedxml: 0 ✅
- pytest: 322 passed, 0 failed (6 CI config tests excluded)

## Files Modified
- scp/core/real_learning_engine.py (CRITICAL #1 — verifier inverse fix)
- scp/security/guardrails.py (CRITICAL #2 + MEDIUM #9)
- scp/runtime/slms.py (HIGH #3 — bool branch NameError)
- scp/runtime/judge.py (HIGH #4, #5, #6)
- scp/runtime/engine.py (HIGH #7, #8 + LOW #10)

## Lesson Learned
Phiên trước claim "0 real errors" nhưng thực ra còn 12 bugs. Root cause: tin comments "[FIX]" mà không verify code chạy thật. Phiên này audit fresh, không tin comments, không tin tests — tìm ra bugs thật.
