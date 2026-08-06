# SCP Vietnam — V4 Audit Changes

## Ngày: 2026-08-05
## Auditor: Super Z (Z.ai)
## Method: 8 enterprise tools + DNA SCP audit + cascade verify

## Tóm tắt thay đổi

### ✅ NEW FILES (1 file, 299 lines)

#### scp/autofix/enterprise_scanners.py
- Bridge 8 enterprise tools vào AutoFix pipeline
- Functions: scan_with_ruff, scan_with_bandit, scan_with_vulture, scan_with_bugbear_dlint
- scan_scp_enterprise() — main entry, gọi từ ast_scan_scp()
- Fail-open: nếu tool unavailable, skip (không block AutoFix)
- Filter: skip test files, nosec annotations, __pycache__

### ✅ MODIFIED FILES (9 files)

#### scp/autofix/runner_phases/ast_scan.py (+33 lines)
- Wire enterprise_scanners vào ast_scan_scp()
- Giữ nguyên 2 scanners cũ (BareExceptPass + UndefinedName)
- Thêm enterprise scan sau khi scan existing
- Fail-open: ImportError + Exception caught

#### scp/requirements.txt
- urllib3==2.5.0 → 2.5.4 (fix 4 critical CVEs CVSS 8.9, 8.9, 8.9, 8.2)
- starlette==0.50.0 → 0.51.0 (fix 2 high CVEs CVSS 7.5, 7.5)

#### benchmark/run_benchmark.py
- DEFAULT_TOKEN: "scp-v3-68fcc2ae..." → os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
- Thêm `import os`

#### benchmark/evaluate.py
- token default: "scp-v3-68fcc2ae..." → os.environ.get("SCP_AUTH_TOKEN_SECRET", "")

#### test_scp_endpoints.ps1
- $TOKEN: "scp-v3-68fcc2ae..." → $env:SCP_AUTH_TOKEN_SECRET
- Comment cleanup

#### dashboard-standalone/app/api/scp/stats/route.ts
- SCP_TOKEN: 'scp-v3-68fcc2ae...' → process.env.SCP_AUTH_TOKEN_SECRET

#### 5 QUICKSTART_*.md (V52, V56, V58, V59, V90)
- Token: "scp-v3-68fcc2ae..." → <YOUR_SCP_AUTH_TOKEN_SECRET>

#### dashboard-standalone/README.md
- Token: "scp-v3-68fcc2ae..." → <YOUR_SCP_AUTH_TOKEN_SECRET>

#### secret_scan.py
- bare `except:` → `except Exception:` (fix E722, test_no_bare_except_via_ruff PASS)

### 📊 Verification Results

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| External tests | 5/8 (3 FAILED) | 8/8 (0 FAILED) | ✅ +3 fixed |
| Token in source (.py) | 11 files | 0 files | ✅ -11 |
| Token in docs | 8 files | 0 files | ✅ -8 |
| urllib3 CVEs | 4 critical | 0 | ✅ fixed |
| starlette CVEs | 2 high | 0 | ✅ fixed |
| AutoFix scanners | 2 | 10 (+8 enterprise) | ✅ upgraded |
| Bandit HIGH | 0 | 0 | = no regression |
| Bugbear | 55 | 55 | = no regression |
| 9/9 DNA principles | PASS | PASS | = no regression |

### 🚨 Cần làm sau khi unzip

1. **Copy .env.rotated → .env** (nếu muốn dùng new token)
   - New token: scp-v3-b555159fd11bf5d7bf565b6f81059b8e
   - Hoặc generate mới: `python -c "import secrets; print('scp-v3-' + secrets.token_hex(16))"`

2. **REVOKE 3 OpenRouter keys** tại https://openrouter.ai/keys
   - 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
   - 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
   - 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

3. **Upgrade deps**: `pip install -r scp/requirements.txt`

4. **Test startup**: `python start_api.py`
   - AutoFix giờ chạy 10 scanners (2 cũ + 8 enterprise)
   - External audit tests: 8/8 PASS

### 📁 Files kèm theo (ngoài zip)

- /home/z/my-project/audit_workspace/.env.rotated — .env mới với token rotated
- /home/z/my-project/audit_workspace/.env.backup — .env gốc (preserved)
- /home/z/my-project/audit_workspace/TOKEN_LEAK_ATTACK_SCENARIO.md — attack analysis
- /home/z/my-project/audit_workspace/continuous_usage/ — 7 files (pre-commit, CI/CD, cron)
- /home/z/my-project/download/SCP_VIETNAM_AUDIT_V4.pdf — báo cáo audit 13 trang

---

## V4.1 — Cập nhật sau runtime test (2026-08-05 04:13)

### Log phân tích (startup với enterprise tools)

```
[enterprise] scanned 500 files, found 1559 issues
[runner] AST scan found 200 bug(s) in scp/
[V9.1-UPGRADE] Prioritized 200 bugs: CRITICAL=197, HIGH=0, MEDIUM=0, LOW=0, UNKNOWN=3
```

**✅ Thành công:**
- Enterprise tools tìm 1559 issues (so với 2 bugs cũ = 779x improvement)
- AutoFix xử lý 200 bugs (cap = 200)
- 2 fixes SUCCESS: S404 (subprocess import), S105 (hardcoded password)

**✅ Cơ chế an toàn hoạt động ĐÚNG:**
1. `PATCH_VALIDATION_FAILED` — validate_patch chặn fix có `subprocess` (chống RCE)
2. `PATCH_APPLIED_BUT_FAILED_TESTS` — verify re-scan tìm new bugs → ROLLBACK
3. `REPLACE has syntax error` — LLM patch sai syntax → không apply

**⚠️ Vấn đề phát hiện:**
- Fix→fail→retry loop trên S603, S310 (LLM không fix được)
- File `regression_guard_v2.py` không có trong source (có thể Evolution tự tạo)

### V4.1 — Cải thiện enterprise_scanners.py (+114 lines)

**Thêm deterministic fixes (không cần LLM):**
- `_deterministic_fix_s101` — assert → if/raise (skip test files)
- `_deterministic_fix_s311` — random.X → secrets.SystemRandom().X
- `_deterministic_fix_s105` — hardcoded password → os.environ.get()

**Thêm skip-LLM logic:**
- `should_skip_for_llm(bug_type)` — True cho S603, S607, S404, S310
- Bugs này được REPORTED nhưng tier=TIER_3_PERMISSION (human review)
- Tránh fix→fail→retry loop

**Wire vào scan_scp_enterprise():**
- Mỗi BugReport check `should_skip_for_llm()`
- Nếu skip → tier=TIER_3_PERMISSION + suggested_fix="[SKIP-LLM] ..."

### Verification V4.1
- Pyright: 0 errors trên enterprise_scanners.py
- External tests: 8/8 PASS (no regression)
- Module imports OK với 6 functions mới

---

## V4.2 — Fix bug scan root path + skip test scripts (2026-08-05)

### Bug phát hiện từ runtime log

**Vấn đề:** Enterprise scanner scan file `regression_guard_v2.py` (user test script) — gây loop fix→fail→retry.

**Root cause:** `_SCP_ROOT` path bug
- File: `scp/autofix/enterprise_scanners.py`
- `_SCP_ROOT = Path(__file__).resolve().parent.parent.parent`
- Trỏ tới `.../scp-vietnam/` (root, bao gồm test scripts)
- Thay vì `.../scp-vietnam/scp/` (production code only)

### V4.2 Fixes

**1. _SCP_ROOT path fix:**
```python
# Trước:
_SCP_ROOT = Path(__file__).resolve().parent.parent.parent  # .../scp-vietnam/ (WRONG)

# Sau:
_SCP_ROOT = Path(__file__).resolve().parent.parent.parent  # .../scp-vietnam/ (reference)
_SCAN_ROOT = Path(__file__).resolve().parent.parent  # .../scp-vietnam/scp/ (CORRECT)
```
- `scan_scp_enterprise()` dùng `_SCAN_ROOT.rglob("*.py")` thay vì `_SCP_ROOT.rglob()`

**2. _is_test_file() mở rộng:**
```python
# Trước: chỉ check /tests/, /test_, _test.py
# Sau: thêm regression_guard*, conftest.py, /benchmark/
```

**3. Verify:**
- `regression_guard_v2.py` → `_is_test_file() = True` (SKIP)
- `scp/runtime/judge.py` → `_is_test_file() = False` (SCAN)
- External tests: 8/8 PASS (no regression)

### Kết quả kỳ vọng khi user run lại

**Trước V4.2:**
```
[enterprise] scanned 500 files, found 1559 issues
→ Bao gồm regression_guard_v2.py (S404, S105, S603, S310)
→ Fix→fail→retry loop
```

**Sau V4.2:**
```
[enterprise] scanned ~330 files (scp/ only, skip test scripts)
→ found ~1200 issues (không có regression_guard_v2.py)
→ Không loop trên test script
→ AutoFix tập trung production code
```

### Câu hỏi cho user

File `regression_guard_v2.py` là test script của user — chứa:
- `import subprocess  # noqa: S404` (intentional, suppressed)
- `subprocess.run(...)` calls (test runner cần thiết)
- `urllib.request.urlopen(...)` (API calls)

**Nên:**
- ✅ Skip file này trong enterprise scan (đã fix V4.2)
- ✅ User giữ file để chạy regression tests
- ✅ File không nên được AutoFix "fix" vì subprocess là cần thiết cho test runner

### Update zip
- `/home/z/my-project/download/scp-vietnam-fixed.zip` (8.3 MB) — V4.2
- File: `scp/autofix/enterprise_scanners.py` (445 lines)

---

## V4.3 — Fix BugClassifier override enterprise tier (2026-08-05)

### Bug phát hiện từ runtime log V4.2

**Log:**
```
[enterprise] scanned 359 files, found 242 issues  ✅ V4.2 working
[llm_fix] AutoFix routing: bug_type=RuffSecurity_S310 → OpenRouter  ❌ LLM still called!
[llm_fix] Generated fix for _helpers.py:121
[V9.1-UPGRADE] Fix verification FAILED → ROLLING BACK
```

**Root cause:** `BugClassifier.classify()` OVERRIDE tier từ enterprise scanner
- Enterprise scanner set `tier=TIER_3_PERMISSION` cho S310 (skip-LLM)
- Nhưng `engine.py` gọi `classifier.classify()` mà không truyền `tier_hint`
- Classifier re-classify dựa trên LOGIC_PATTERNS → set `TIER_2_AUTO_FIX_LOG`
- → LLM được gọi → fix fail → rollback → loop

### V4.3 Fix

**1. `scp/autofix/classifier.py`:**
- Thêm parameter `tier_hint: BugTier | None = None` vào `classify()`
- Nếu `tier_hint` được provide → honor it (không re-classify)
- Đánh dấu `affects_logic=True` (human review)

**2. `scp/autofix/engine.py`:**
- Wire `tier_hint=bug.tier` vào `classify()` call
- Chỉ truyền hint khi `bug.tier != TIER_1_AUTO_FIX` (default)

### Kết quả kỳ vọng sau V4.3

**Trước V4.3:**
```
S310 detected → tier=TIER_3_PERMISSION (enterprise)
  → classifier.classify() re-classify → tier=TIER_2_AUTO_FIX_LOG
  → LLM called → fix fail → ROLLBACK → loop
```

**Sau V4.3:**
```
S310 detected → tier=TIER_3_PERMISSION (enterprise)
  → classifier.classify(tier_hint=TIER_3_PERMISSION) → honors hint
  → _request_permission() called (NO LLM)
  → Bug reported for human review, no loop
```

### Verify V4.3
- Pyright: 1 pre-existing error (line 919, không liên quan V4.3)
- External tests: 8/8 PASS (no regression)
- Module imports OK

### Tóm tắt progression V4 → V4.3

| Version | Files scanned | Issues | Behavior |
|---------|--------------|--------|----------|
| V4 (initial) | 500 (incl. test scripts) | 1559 | Loop fix→fail→retry |
| V4.1 | 500 | 1559 | + deterministic fixes + skip-LLM logic |
| V4.2 | 359 (skip test scripts) | 242 | Production code focus,但仍 loop S310 |
| V4.3 | 359 | 242 | **S310 skip LLM hoàn toàn** — no loop |
