# SCP Vietnam v104.75 — v5.4 (Realtime Monitor + Wired Modules + Scanner Fix)

> **Bản build hoàn chỉnh v5.4** — thêm:
> - 3 module wired vào judge.py + data_partitioner.py + why_engine.py
> - Scanner scope-tracking fix (25 false positives → 0)
> - .env với API keys thật
> - Next.js dashboard realtime monitor (separate)

---

## 🆕 MỚI TRONG v5.4

### 1. 3 Module Wired (opt-in/passive)
| Module | Wire point | Env var | Default |
|---|---|---|---|
| `bypass_encrypt.py` | `data_partitioner.py:write_bypass()` | `SCP_ENCRYPT_BYPASSES=1` | OFF (plaintext) |
| `multi_llm_check.py` | `judge.py` Step 5.5 (sau adversary verify) | `SCP_MULTI_LLM_CHECK=1` | OFF (skip) |
| `metawhy_monitor.py` | `why_engine.py:execute_plan()` | (passive, no env) | ALWAYS ON |

**Pattern thiết kế:** Lazy singleton + double-checked locking + try/except fallback (wiring fail không break flow).

### 2. Scanner Scope-Tracking Fix (v5.3-SCANNER-FIX)
**Two-pass scanner:**
- Pass 1: `_ModuleNameCollector` — collect ALL module-level names
- Pass 2: `_UndefinedNameFinder` — check usage against collected names

**8 fixes:**
1. `except X as exc` — track `node.name`
2. Lambda args — `visit_Lambda`
3. Comprehension vars — `visit_ListComp/SetComp/DictComp/GeneratorExp`
4. Walrus `:=` — `visit_NamedExpr`
5. `with ... as var` — verified
6. Forward references — Pass 1 seeds module scope
7. Decorators — visit in enclosing scope
8. Type annotations — skipped

**Before/After:**
| Metric | Before | After |
|---|---|---|
| api_server.py UndefinedName | 25 (all FP) | **0** |
| False positive rate | 100% | **0%** |

### 3. .env với API keys thật
- OPENROUTER_API_KEY (3 keys)
- GROQ_API_KEY
- PUBMED_API_KEY
- SCP_AUTH_TOKEN_SECRET
- SCP_DEV_MODE=1
- SCP_ENABLE_CLOSED_LOOP=1

### 4. Next.js Dashboard Realtime Monitor (separate)
- **Không** trong zip này — chạy ở Z.ai sandbox
- Gà xem SCP activity realtime (mock data, sẵn sàng connect SCP thật)
- Auto-refresh 2.5s
- Hiển thị: AutoFix stats, Evolution stats, KB, Constitution, Safety Guards, Live Activity Feed

---

## 🎯 CÁCH CHẠY (TĂNG QUYỀN TỐI ĐA)

```powershell
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1

# Tăng quyền
$env:SCP_AUTO_APPROVE_TIER3 = "1"      # Tier 3 (1h)
$env:SCP_EVOLUTION_ENABLED = "1"       # Tier 4 (4h)
$env:SCP_ENCRYPT_BYPASSES = "1"        # Encrypt bypasses at-rest
$env:SCP_MULTI_LLM_CHECK = "1"         # Cross-check LLMs
$env:SCP_SKIP_STARTUP_GATE = "0"
$env:SCP_DEV_MODE = "1"

# SCP tự fix + evolve
python -m scp.autofix.runner --ast-scan --verbose --max-bugs 50
python -m scp.autofix.runner --evolve --max-bugs 20 --verbose

# Khởi động server
python start_api.py

# Xem stats
python -m scp.autofix.runner --evolution-stats

# GIẢM QUYỀN (khi ổn định)
Remove-Item Env:SCP_EVOLUTION_ENABLED
Remove-Item Env:SCP_AUTO_APPROVE_TIER3
Remove-Item Env:SCP_ENCRYPT_BYPASSES
Remove-Item Env:SCP_MULTI_LLM_CHECK
```

---

## 📊 TỔNG PATCHES (23 cái)

| Phiên bản | Patches | Files |
|---|---|---|
| v5.1 | 6 (AutoFix bootstrap) | api_server.py, engine.py, runner.py, code_evolution_agent.py |
| v5.2 | 5 (Tier-3 auto-approve) | classifier.py, engine.py |
| v5.3 | 6 (Evolution Engine + 3 modules + 3 SPEC) | evolution.py, runner.py, bypass_encrypt.py, multi_llm_check.py, metawhy_monitor.py |
| v5.4 | 6 (3 wirings + scanner fix + .env) | data_partitioner.py, judge.py, why_engine.py, runner.py, .env |
| **Tổng** | **23** | **11 files** |

---

## 🛡️ 9 SAFETY GUARDS (WHY-controlled, vẫn còn)

| # | Guard | Mức |
|---|---|---|
| 1 | `SCP_EVOLUTION_ENABLED=1` env var | Bật/tắt |
| 2 | ⏰ Auto-timeout 4h | Tự giảm quyền |
| 3 | WHY layer 1 (necessity) | "Tại sao cần?" |
| 4 | WHY layer 2 (falsification) | "Tại sao đúng?" |
| 5 | 🛡️ Constitution HARD LOCK | CỨNG NHẤT |
| 6 | 💾 Backup `.evolutionbak` | Rollback |
| 7 | 📝 `evolution_audit.jsonl` | Forensic |
| 8 | 📝 `evolution_rejected.jsonl` | WHY-rejected |
| 9 | 📝 `evolution_reflect.jsonl` | Reflect outputs |

---

## 🔍 MONITORING

### Evolution stats
```powershell
python -m scp.autofix.runner --evolution-stats
```

### Audit logs
- `data/evolution_audit.jsonl` — mọi evolution actions
- `data/evolution_reflect.jsonl` — reflect outputs (human review)
- `data/evolution_rejected.jsonl` — WHY-rejected actions
- `data/tier3_auto_audit.jsonl` — Tier 3 auto-approves
- `data/metawhy_patterns.jsonl` — WHY question patterns (passive, always logs)

### Dashboard (Z.ai sandbox)
- Xem SCP activity realtime: mock data + sẵn sàng connect SCP thật
- Auto-refresh 2.5s

---

## ⚠️ CẢNH BÁO THẬT

1. **3 module wired = opt-in** — phải set env var mới hoạt động
2. **metawhy_monitor = passive** — luôn log, không side effects
3. **Scanner fix** — 0 false positive trong api_server.py, nhưng có thể miss bugs thật ở files khác (cần test thêm)
4. **PASS ≠ ĐÚNG** — SCP chạy xong chưa chứng minh SCP thông minh hơn. Cần test `/ask` + so sánh KB trước/sau
5. **.env có keys thật** — KHÔNG commit Git. Zip này chỉ cho Gà.

---

*Build bởi Z.ai — 2026-08-01. v5.4 = v5.3 + wirings + scanner fix + .env. Reality > Model.*
