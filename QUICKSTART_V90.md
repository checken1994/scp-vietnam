# SCP Vietnam v104.75 — v9.0 (WHY = PRIMARY CONTROL GATE)

> **Bản build v9.0** — WHY engine từ "cố vấn" → "CHỐT" (block-capable, default ON).
> Gà: "WHY hiện tại = cố vấn, không phải chốt. Hoàn thành nốt."

---

## 🆕 MỚI TRONG V9.0

### WHY Gate — Primary Control Gate (DEFAULT ON)

**Trước v9.0:**
- WHY = opt-in (env OFF = zero change)
- WHY = advisory (adjust confidence ±0.1)
- WHY không block gì cả

**v9.0:**
- WHY = DEFAULT ON (không cần env var)
- WHY = BLOCK-CAPABLE (can reject actions)
- WHY gates 6 components:

| # | Component | WHY can | File |
|---|---|---|---|
| 1 | Verdict | block PASS → UNKNOWN | judge.py |
| 2 | AutoFix | block fix | engine.py |
| 3 | Scanner | skip bugs | runner.py |
| 4 | Evolution | skip evolve | evolution.py |
| 5 | Learning | block KB save | brain.py |
| 6 | Prediction | block prediction | predictor.py |

### WHY Gate Architecture

```
Action requested → WHY Gate
  ├── Layer 1: Necessity ("Tại sao cần?")
  │   ├── Match pattern → ALLOW
  │   ├── LLM available → LLM analysis
  │   └── Fallback → conservative ALLOW
  │
  ├── Layer 2: Falsification ("Tại sao đúng? Bác bỏ được?")
  │   ├── Match reject pattern (relaxation, bypass) → REJECT
  │   ├── LLM self-falsified → REJECT
  │   └── Fallback → not falsified → ALLOW
  │
  └── Constitution HARD LOCK
      └── KILL → WHY cannot override → ALLOW (the KILL)
```

### Safety Guards (9, preserved from v8.0 + 1 new)

| # | Guard | Status |
|---|---|---|
| 1 | Default ON | ✅ NEW (v9.0) |
| 2 | WHY error = non-blocking | ✅ try/except |
| 3 | Constitution HARD LOCK | ✅ WHY cannot override KILL |
| 4 | Audit log | ✅ why_gate_audit.jsonl |
| 5 | LLM fallback → regex | ✅ graceful degradation |
| 6 | Regex fallback → UPHOLD | ✅ conservative |
| 7 | Singleton | ✅ shared stats |
| 8 | Lazy import | ✅ no circular |
| 9 | Rate limit (v8.0) | ✅ preserved |

---

## 📊 TEST RESULTS (WHY Gate)

```
Test 1 (normal verdict):     ALLOW ✅
Test 2 (relaxation):         REJECT ✅ (blocked — "lower threshold" = loosening)
Test 3 (security bypass):    REJECT ✅ (blocked — "bypass security")
Test 4 (constitution KILL):  ALLOW ✅ (HARD LOCK — WHY cannot override)
Test 5 (normal fix):         ALLOW ✅
```

---

## 🎯 CÁCH CHẠY

```powershell
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py

# WHY Gate DEFAULT ON — không cần env var!
# Mọi /ask, AutoFix, Evolution, Learning, Prediction đều qua WHY Gate

# Xem WHY Gate stats:
curl http://127.0.0.1:8000/v105/autofix/stats -H "Authorization: Bearer <YOUR_SCP_AUTH_TOKEN_SECRET>"

# Xem WHY Gate audit log:
type data\why_gate_audit.jsonl
```

---

## 📦 TỔNG PATCHES

| Phiên bản | Patches |
|---|---|
| v5.1-v8.0 | 55 |
| v9.0 | 7 (WHY Gate module + 6 component wirings) |
| **Tổng** | **62** |

---

## 🔥 TỔNG KẾT V9.0

```
v9.0 = v8.0 + WHY as PRIMARY CONTROL GATE:
  ✅ WHY default ON (không cần env)
  ✅ WHY block-capable (REJECT actions)
  ✅ WHY gates 6 components (verdict, autofix, scanner, evolution, learning, prediction)
  ✅ Constitution HARD LOCK preserved
  ✅ Audit log mọi WHY decisions
  ✅ Fallback: LLM → regex → UPHOLD (conservative)

WHY giờ = CHỐT, không phải cố vấn
```

---

*Build bởi Z.ai — 2026-08-01. v9.0 = WHY as Primary Control Gate. Reality > Model.*
