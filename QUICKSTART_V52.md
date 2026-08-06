# SCP Vietnam v104.75 — v5.2 (Tier-3 Auto-Approve Edition)

> **Bản build hoàn chỉnh** — đã apply 11 patches (6 v5.1 AutoFix fixes + 5 v5.2 Tier-3 auto-approve).
> Zip này copy 1 lần là chạy, không cần ghép bản khác.

---

## 🎯 CÁCH CHẠY (chọn 1 trong 3)

### Cách 1: Bypass gate hoàn toàn (nhanh nhất, dev only)
```powershell
cd C:\Users\check\Downloads\scp-v104-v52
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:SCP_SKIP_STARTUP_GATE = "1"
$env:SCP_DEV_MODE = "1"
python start_api.py
# → Server start ngay, gate bypassed
```

### Cách 2: Để SCP TỰ FIX TẤT CẢ bugs (Tier 1/2/3, trừ relaxation) — KIỂM TRA KHẢ NĂNG
```powershell
cd C:\Users\check\Downloads\scp-v104-v52
.\.venv\Scripts\Activate.ps1

# TĂNG QUYỀN TẠM THỜI (tự hết sau 1h)
$env:SCP_AUTO_APPROVE_TIER3 = "1"
$env:SCP_SKIP_STARTUP_GATE = "0"       # để gate chạy
$env:SCP_DEV_MODE = "1"
# OPENROUTER_API_KEY đã có sẵn trong .env

# Chạy SCP tự fix
python -m scp.autofix.runner --ast-scan --verbose --max-bugs 50

# Xem log: "[TIER3-AUTO] Auto-approving Tier-3 bug..."
# Xem audit: type data\tier3_auto_audit.jsonl

# Khởi động server
python start_api.py
```

### Cách 3: GIẢM QUYỀN SCP lại (sau khi kiểm tra xong)
```powershell
# Tắt Tier-3 auto-approve
Remove-Item Env:SCP_AUTO_APPROVE_TIER3
# HOẶC
$env:SCP_AUTO_APPROVE_TIER3 = "0"

# Server giờ quay lại flow thường: Tier 3 → yêu cầu human approve
python start_api.py
```

---

## 🛡️ 7 SAFETY GUARDS (khi SCP_AUTO_APPROVE_TIER3=1)

| # | Guard | Mức |
|---|---|---|
| 1 | `SCP_AUTO_APPROVE_TIER3=1` env var (default 0) | Bật/tắt |
| 2 | ⏰ Auto-timeout 1h (env var tự hết hiệu lực) | Chống quên |
| 3 | 🚦 Rate limit 5 Tier-3 fixes/giờ | Chống chạy loose |
| 4 | 🛡️ **RELAXATION HARD LIMIT** — `is_relaxation=True` → KHÔNG bao giờ auto | CỨNG NHẤT |
| 5 | 🔄 Cooldown 1h cho cùng bug | Chống loop |
| 6 | 💾 Backup `.tier3bak` trước khi apply | Rollback được |
| 7 | 📝 Audit log riêng `data/tier3_auto_audit.jsonl` | Forensic |

**RELAXATION patterns KHÔNG bao giờ auto** (dù env=1):
- `lower threshold` / `raise confidence` / `looser` / `relax`
- `remove block` / `delete rule` / `skip detect`
- `allow attack` / `whitelist` / `bypass security`

---

## 📊 11 PATCHES ĐÃ APPLY

### v5.1 — AutoFix Bootstrap (6 patches)
| # | Fix | File |
|---|---|---|
| 1 | `SCP_SKIP_STARTUP_GATE` env var | api_server.py |
| 2 | `_fixes_this_cycle` chỉ tăng khi `patched=True` | engine.py |
| 3 | runner gọi `process_bug_with_llm` (LLM bridge) | runner.py |
| 4 | `--ast-scan` flag cho CLI | runner.py |
| 5 | `MAX_FIXES_PER_CYCLE` 10 → 200 | engine.py |
| 6 | pending filename +md5 hash (unique per bug) | code_evolution_agent.py |

### v5.2 — Tier-3 Auto-Approve (5 patches)
| # | Fix | File |
|---|---|---|
| 7 | `is_relaxation` field + check RELAXATION first | classifier.py |
| 8 | Constants: `MAX_TIER3_AUTO_PER_HOUR=5`, timeout 1h | engine.py |
| 9 | Init `_tier3_auto_timestamps`, `_tier3_auto_enabled_at` | engine.py |
| 10 | 3 methods: `_should_auto_approve_tier3`, `_auto_approve_tier3`, `_write_tier3_auto_audit` | engine.py |
| 11 | Stats endpoint expose `tier3_auto_*` fields | engine.py |

---

## 🔍 MONITORING

### Xem stats AutoFix
```powershell
curl http://127.0.0.1:8000/v105/autofix/stats -H "Authorization: Bearer <YOUR_SCP_AUTH_TOKEN_SECRET>"
```

Response:
```json
{
  "tier3_auto_enabled": true,
  "tier3_auto_used_this_hour": 3,
  "tier3_auto_remaining": 2,
  "tier3_auto_max_per_hour": 5,
  "tier3_auto_expires_in_seconds": 3245
}
```

### Xem audit Tier-3 auto fixes
```powershell
type data\tier3_auto_audit.jsonl
```

### Rollback 1 file (nếu SCP fix sai)
```powershell
# Mỗi file có backup .tier3bak (chỉ khi SCP_AUTO_APPROVE_TIER3=1)
Copy-Item scp\autofix\engine.py.tier3bak scp\autofix\engine.py -Force
```

---

## ⚠️ CẢNH BÁO

1. **PASS ≠ ĐÚNG** — sau khi SCP tự fix, chưa chứng minh SCP hoạt động đúng. Cần test `/ask` endpoint.
2. **Scanner vẫn dò 9/10 false positive** (scope-tracking kém) → SCP tốn LLM calls cho rác. Bug tiếp theo.
3. **1h tự tắt** — sau 1h, env var hết hiệu lực, phải set lại.
4. **5 fixes/giờ** — chặt hơn MAX_FIXES_PER_CYCLE=200.

---

## 📁 CẤU TRÚC ZIP

```
scp-vietnam/                 ← copy cả folder này vào C:\Users\check\Downloads\
├── scp/                     ← code chính (184 file .py)
│   ├── api_server.py        ← đã patch v5.1
│   ├── autofix/             ← đã patch v5.1 + v5.2
│   │   ├── classifier.py    ← đã patch v5.2
│   │   ├── engine.py        ← đã patch v5.1 + v5.2
│   │   ├── runner.py        ← đã patch v5.1
│   │   ├── llm_fix.py
│   │   └── permission.py
│   ├── core/
│   │   └── code_evolution_agent.py  ← đã patch v5.1
│   └── ... (security, meta, knowledge, ...)
├── start_api.py             ← entry point
├── requirements.txt
├── .env                     ← đã config API keys
├── QUICKSTART_V52.md        ← file này
└── ... (docs, benchmarks, tests)
```

---

*Build bởi Z.ai — 2026-08-01. PATCHES = REALITY (verify syntax + logic). Reality > Model.*
