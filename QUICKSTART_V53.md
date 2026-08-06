# SCP Vietnam v104.75 — v5.3 (Evolution Engine Edition)

> **Bản build hoàn chỉnh v5.3** — thêm Evolution Engine (Tier 4, WHY-controlled).
> SCP giờ có khả năng tự tiến hóa: build module mới + reflect + evolve cycle.
> 3 module mới đã build: bypass_encrypt, multi_llm_check, metawhy_monitor.

---

## 🧠 5 KHẢ NĂNG TỰ TIẾN HÓA (vòng lặp khép kín)

```
WHY engine đặt câu hỏi "Tại sao?"
       ↓
Learning thu thập bằng chứng
       ↓
Audit tìm missing piece
       ↓
AutoFix/Evolution sửa + BUILD MỚI
       ↓
REFLECT: "Tại sao bug này xảy ra?" → update KB + scanner + classifier
       ↓
Lần sau: scanner chính xác hơn, classifier thông minh hơn, KB phong phú hơn
= CÀNG CHẠY LÂU, CÀNG THÔNG MINH
```

---

## 🆕 MỚI TRONG v5.3

### Evolution Engine (`scp/autofix/evolution.py` — 675 dòng)
3 modes mới (Tier 4, WHY-controlled):
1. **`build_module(spec)`** — Build module mới từ SPEC JSON
2. **`reflect(bug, fix)`** — Học "Tại sao bug xảy ra?" sau mỗi fix
3. **`evolve_cycle()`** — Vòng lặp khép kín: WHY → Audit → Fix → Reflect

### 3 module mới đã build (theo SPEC)
| Module | Vai trò | Dòng |
|---|---|---|
| `scp/security/bypass_encrypt.py` | Encrypt `data/bypasses/*.jsonl` at-rest (Fernet) | 215 |
| `scp/meta/multi_llm_check.py` | Cross-check 2-3 LLM providers — combat ảo giác đồng thuận | 244 |
| `scp/meta/metawhy_monitor.py` | Monitor WHY engine — detect loops + emergent patterns | 167 |

### SPEC files (cho SCP tự build lại nếu cần)
- `specs/bypass_encrypt.spec.json`
- `specs/multi_llm_check.spec.json`
- `specs/metawhy_monitor.spec.json`

---

## 🛡️ 9 SAFETY GUARDS (WHY-controlled, không cơ khí)

| # | Guard | Cách hoạt động |
|---|---|---|
| 1 | `SCP_EVOLUTION_ENABLED=1` env var | Bật/tắt (default 0) |
| 2 | ⏰ Auto-timeout 4h | Tự giảm quyền |
| 3 | WHY layer 1 (necessity) | "Tại sao cần?" — không trả lời được → skip |
| 4 | WHY layer 2 (falsification) | "Tại sao đúng? Bác bỏ được không?" — self-falsify → REJECT |
| 5 | 🛡️ Constitution HARD LOCK | WHY không questioning Constitution (10 principles + 9 DNA) |
| 6 | 💾 Backup `.evolutionbak` | Mỗi file touched có backup |
| 7 | 📝 Audit log `data/evolution_audit.jsonl` | Forensic |
| 8 | 📝 Rejected log `data/evolution_rejected.jsonl` | WHY-rejected actions |
| 9 | 📝 Reflect log `data/evolution_reflect.jsonl` | Reflect outputs (human review) |

**Rate limit:** 20 actions/giờ (WHY-controlled, cao hơn Tier 3 là 5)

---

## 🎯 CÁCH CHẠY

### Cách 1: Chạy thường (không evolution)
```powershell
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py
```

### Cách 2: Tăng quyền tạm thời — SCP tự fix bugs (Tier 3)
```powershell
$env:SCP_AUTO_APPROVE_TIER3 = "1"   # tự hết 1h
$env:SCP_SKIP_STARTUP_GATE = "0"
python -m scp.autofix.runner --ast-scan --verbose --max-bugs 50
python start_api.py
```

### Cách 3: TĂNG QUYỀN TỐI ĐA — SCP tự tiến hóa (Tier 4) 🔥
```powershell
# Tăng cả 2 quyền
$env:SCP_AUTO_APPROVE_TIER3 = "1"   # Tier 3 auto (1h timeout)
$env:SCP_EVOLUTION_ENABLED = "1"    # Tier 4 evolution (4h timeout)
$env:SCP_SKIP_STARTUP_GATE = "0"

# Xem evolution stats
python -m scp.autofix.runner --evolution-stats

# Build module mới từ SPEC
python -m scp.autofix.runner --build-module specs/bypass_encrypt.spec.json
python -m scp.autofix.runner --build-module specs/multi_llm_check.spec.json
python -m scp.autofix.runner --build-module specs/metawhy_monitor.spec.json

# Chạy evolution cycle (WHY → Audit → Fix → Reflect)
python -m scp.autofix.runner --evolve --max-bugs 20 --verbose

# Khởi động server
python start_api.py

# Xem audit logs
type data\evolution_audit.jsonl
type data\evolution_reflect.jsonl
type data\evolution_rejected.jsonl
```

### Cách 4: GIẢM QUYỀN lại (sau khi kiểm tra)
```powershell
Remove-Item Env:SCP_EVOLUTION_ENABLED
Remove-Item Env:SCP_AUTO_APPROVE_TIER3
# HOẶC đợi 4h (evolution) + 1h (tier3) tự hết
python start_api.py
```

---

## 📊 TỔNG PATCHES (17 cái, v5.1 + v5.2 + v5.3)

| Phiên bản | Patches | Files |
|---|---|---|
| v5.1 | 6 (AutoFix bootstrap) | api_server.py, engine.py, runner.py, code_evolution_agent.py |
| v5.2 | 5 (Tier-3 auto-approve) | classifier.py, engine.py |
| v5.3 | 6 (Evolution Engine + 3 modules + 3 SPEC) | evolution.py (NEW), runner.py, bypass_encrypt.py (NEW), multi_llm_check.py (NEW), metawhy_monitor.py (NEW) |
| **Tổng** | **17** | **8 files** |

---

## 🔍 MONITORING

### Evolution stats
```powershell
python -m scp.autofix.runner --evolution-stats
```
Response:
```json
{
  "enabled": true,
  "used_this_hour": 3,
  "remaining": 17,
  "max_per_hour": 20,
  "expires_in_seconds": 12345,
  "modules_built": 3,
  "reflects_done": 5,
  "evolves_completed": 1,
  "rejected_by_why": 2
}
```

### Audit logs
- `data/evolution_audit.jsonl` — mọi evolution actions
- `data/evolution_reflect.jsonl` — reflect outputs (human review)
- `data/evolution_rejected.jsonl` — WHY-rejected actions (learning)
- `data/tier3_auto_audit.jsonl` — Tier 3 auto-approves
- `data/autofix_audit.jsonl` — regular AutoFix

---

## ⚠️ CẢNH BÁO THẬT

1. **WHY = chốt kiểm soát, không phải env var** — env var bật/tắt, WHY decide từng action
2. **Constitution HARD LOCK** — WHY không bao giờ questioning Constitution (10 principles + 9 DNA)
3. **Reflect có thể học SAI** — WHY layer 2 (falsification) cố bắt, nhưng không đảm bảo 100%
4. **4h tự tắt** — nếu quên giảm quyền, env tự hết
5. **PASS ≠ ĐÚNG** — evolution chạy xong chưa chứng minh SCP thông minh hơn. Cần test `/ask` + so sánh KB trước/sau

---

## 📁 CẤU TRÚC ZIP

```
scp-vietnam/
├── scp/
│   ├── api_server.py              ← v5.1 patched
│   ├── autofix/
│   │   ├── engine.py              ← v5.1 + v5.2 patched
│   │   ├── runner.py              ← v5.1 + v5.3 patched (--build-module, --evolve)
│   │   ├── classifier.py          ← v5.2 patched
│   │   ├── llm_fix.py
│   │   ├── permission.py
│   │   └── evolution.py           ← v5.3 NEW (675 dòng, Tier 4)
│   ├── core/
│   │   └── code_evolution_agent.py ← v5.1 patched
│   ├── security/
│   │   └── bypass_encrypt.py      ← v5.3 NEW (Fernet at-rest encryption)
│   └── meta/
│       ├── why_engine.py
│       ├── self_questioning.py
│       ├── multi_llm_check.py     ← v5.3 NEW (cross-LLM check)
│       └── metawhy_monitor.py     ← v5.3 NEW (WHY pattern monitor)
├── specs/                          ← v5.3 NEW
│   ├── bypass_encrypt.spec.json
│   ├── multi_llm_check.spec.json
│   └── metawhy_monitor.spec.json
├── start_api.py
├── requirements.txt
├── .env                           ← có API keys
├── QUICKSTART_V53.md              ← file này
└── ... (docs, benchmarks, tests)
```

---

## 🤖 VAI TRÒ TỪNG BÊN

| Vai trò | Ai làm | v5.3 |
|---|---|---|
| Viết Evolution Engine | Z.ai (bootstrap exception) | ✅ |
| Viết 3 module | Z.ai theo SPEC (bootstrap — SCP sẽ tự build khi LLM key) | ✅ |
| Viết SPEC | Z.ai | ✅ |
| SCP tự build module mới | SCP (khi Gà set env + có LLM key) | ⏳ chờ Gà test |
| SCP tự reflect | SCP | ⏳ chờ Gà test |
| SCP tự evolve cycle | SCP | ⏳ chờ Gà test |
| Verify | Z.ai | ✅ syntax + logic + safety guards |

**Bootstrap exception:** Vì SCP chưa chạy được (cần fix v5.1+v5.2 trước), Z.ai viết 3 module theo SPEC để chứng minh flow. Khi Gà set `SCP_EVOLUTION_ENABLED=1` + có OPENROUTER_API_KEY, SCP có thể tự build module thứ 4, thứ 5...

---

*Build bởi Z.ai — 2026-08-01. PATCHES = REALITY. WHY = CHỐT. Reality > Model.*
