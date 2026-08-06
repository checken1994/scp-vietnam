# SCP Vietnam v104.75 — v5.5 (Pattern Fixer + .env Auto-load)

> **Bản build hoàn chỉnh v5.5** — fix 4 vấn đề từ log Gà chạy v5.4:
> 1. `.env` không được load → OPENROUTER_API_KEY rỗng
> 2. 50 bugs BareExceptPass gọi LLM mỗi cái → rate limit 20/giờ → 30 skip
> 3. STARTUP-GATE block vì 50 bugs "skipped"
> 4. Tier 3 auto-approve không giúp gì (bugs là Tier 1)

---

## 🆕 MỚI TRONG v5.5

### Fix 1: Auto-load .env
- `start_api.py` + `scp/autofix/runner.py` auto-load `.env` qua `python-dotenv`
- Fallback: manual parse nếu `python-dotenv` chưa install
- **Kết quả:** OPENROUTER_API_KEY, GROQ_API_KEY, etc. tự động có sẵn

### Fix 2: BareExceptPass Pattern Fixer (KHÔNG cần LLM)
- `_generate_bare_except_fix(bug)` trong `llm_fix.py`
- Pattern: `except Exception: pass` → `except Exception as e: logger.exception(...)`
- **Không gọi LLM** → 0 API calls → 0 rate limit hits
- **50 bugs → 50 pattern fixes** (instant, ~0ms)

### Fix 3: Tăng LLM rate limit 20 → 100/giờ
- `_MAX_LLM_FIXES_PER_HOUR = 100` (was 20)
- LLM giờ chỉ dùng cho bugs phức tạp (không phải BareExceptPass)
- 100/giờ dư cho 2 full scans

### Fix 4: process_bug_with_llm ưu tiên pattern
- Flow mới: predefined → pattern → LLM
- `fix_source` field: `predefined` | `pattern_bare_except` | `llm` | `llm_failed`

---

## 🎯 CÁCH CHẠY (đơn giản hơn v5.4)

```powershell
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt  # include python-dotenv

# Option A: Bypass gate (nhanh nhất)
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py

# Option B: Để SCP tự fix tất cả bugs (KHÔNG cần LLM key cho BareExceptPass!)
$env:SCP_SKIP_STARTUP_GATE = "0"
python -m scp.autofix.runner --ast-scan --verbose --max-bugs 50
# → 50 bugs BareExceptPass → 50 pattern fixes (0 LLM calls)
# → STARTUP-GATE PASS
python start_api.py

# Option C: Tăng quyền tối đa (v5.4 command, vẫn hoạt động)
$env:SCP_AUTO_APPROVE_TIER3 = "1"
$env:SCP_EVOLUTION_ENABLED = "1"
$env:SCP_SKIP_STARTUP_GATE = "0"
python -m scp.autofix.runner --ast-scan --verbose --max-bugs 50
python -m scp.autofix.runner --evolve --max-bugs 20 --verbose
python start_api.py
```

---

## 📊 SO SÁNH V5.4 vs V5.5

| Metric | v5.4 (log Gà) | v5.5 (expected) |
|---|---|---|
| OPENROUTER_API_KEY | ❌ rỗng (.env không load) | ✅ auto-load |
| Bugs fixed | 0/50 | 50/50 (pattern) |
| LLM calls | 50 (tất cả fail) | 0 (pattern fixer) |
| Rate limit hits | 30 | 0 |
| STARTUP-GATE | ❌ BLOCKED | ✅ PASS |
| Server start | ❌ RuntimeError | ✅ running |

---

## 📦 TỔNG PATCHES (26 cái)

| Phiên bản | Patches |
|---|---|
| v5.1 | 6 (AutoFix bootstrap) |
| v5.2 | 5 (Tier-3 auto-approve) |
| v5.3 | 6 (Evolution Engine + 3 modules) |
| v5.4 | 6 (3 wirings + scanner fix + .env) |
| v5.5 | 3 (.env auto-load + pattern fixer + rate limit) |
| **Tổng** | **26** |

---

## ⚠️ LƯU Ý

1. **`pip install -r requirements.txt`** — đã thêm `python-dotenv>=1.0.0`
2. **Pattern fixer an toàn** — chỉ thêm `logger.exception()`, không thay đổi logic
3. **LLM vẫn cần cho bugs phức tạp** — UndefinedName, SchemaMismatch, etc.
4. **PASS ≠ ĐÚNG** — server start được chưa chứng minh SCP hoạt động đúng. Cần test `/ask`

---

*Build bởi Z.ai — 2026-08-01. v5.5 = v5.4 + .env load + pattern fixer. Reality > Model.*
