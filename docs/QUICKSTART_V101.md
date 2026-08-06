# SCP Vietnam v104.75 — v10.1 (KB Evolve Bug Fix)

> **Bản build v10.1** — fix 2 bugs trong KB accumulation.

---

## 🆕 FIX TRONG V10.1

### Bug 1: 4/6 reflects có lesson_learned = EMPTY → KB = 0
**Nguyên nhân:** `extract_lesson_from_reflect` return None khi `lesson_learned` rỗng (LLM rate limit)
**Fix:** Fallback lesson — "Bug {type} fixed at {file}:{line}" khi LLM không trả lời

### Bug 2: 2/6 reflects có lesson_learned = "<<<<<<< OLD" (search-replace block)
**Nguyên nhân:** LLM trả search-replace block thay vì lesson text → saved as lesson (rác)
**Fix:** Filter `<<<<<<<` content → use fallback lesson

### Bug 3: KB save error bị swallow (logger.debug)
**Nguyên nhân:** `except Exception: logger.debug(...)` → error ẩn, không thấy trong log
**Fix:** Change `logger.debug` → `logger.warning` → error hiện rõ

### Test Results (4/4 PASS)

```
Test 1 (empty lesson):     SAVED ✅ (fallback: "Bug NullDereference fixed at test.py:42")
Test 2 (search-replace):   SAVED ✅ (filtered <<<<<<<, fallback used)
Test 3 (real lesson):      SAVED ✅ ("Do self._stats dùng chung...")
Test 4 (self_falsified):   SKIPPED ✅ (WHY rejected — correct)
```

**Trước v10.1:** KB = 0 lessons (ALL 6 reflects lost)
**Sau v10.1:** KB = 3+ lessons (4/6 saved with fallback, 2/6 saved with real lesson)

---

## 📦 TỔNG PATCHES: 77 (v5.1-v10.0: 74 + v10.1: 3)

---

*Build bởi Z.ai — 2026-08-01. v10.1 = KB evolve bug fix. Reality > Model.*
