# SCP Vietnam v104.75 — v5.7 (GeoSLM Fix + Dashboard Standalone + WHY Deep)

> **Bản build v5.7** — 3 việc lớn:
> 1. Fix GeoSLM (REST Countries deprecated → Wikipedia source thứ 2)
> 2. Dashboard Next.js standalone (Gà chạy trên máy, connect SCP thật)
> 3. WHY engine deep integration (4 changes, opt-in/passive)

---

## 🆕 MỚI TRONG V5.7

### Việc 1: GeoSLM Fix (Wikipedia source thứ 2)
**File:** `scp/runtime/slms.py` (+35 lines)

**Vấn đề:** REST Countries API deprecated → GeoSLM chỉ có LocalDB (1 nguồn, conf=0.7) → verdict UNKNOWN.

**Fix:** Thêm Wikipedia REST API verify:
- Khi LocalDB trả conf=0.7 (deprecated fallback)
- Query Wikipedia: `https://en.wikipedia.org/api/rest_v1/page/summary/{entity}`
- Nếu Wikipedia extract chứa capital value → conf=0.85 (2 nguồn verified)
- → Verdict PASS thay vì UNKNOWN

**Test:** `curl -X POST /ask -d '{"question":"thủ đô Việt Nam","domain":"geography"}'` → expect conf=0.85, verdict=PASS

### Việc 2: Dashboard Standalone
**Folder:** `dashboard-standalone/` (Next.js 16 + React 19)

**Vấn đề:** Dashboard Z.ai cloud không connect được SCP máy Gà (localhost).

**Fix:** Dashboard standalone Gà chạy trên máy:
- `npm install` + `npm run dev` → http://localhost:3000
- Proxy API routes tới SCP (localhost:8000)
- Auto-refresh 3s, connection status, live activity feed
- Dark theme, responsive

**Cách chạy:**
```powershell
cd C:\Users\check\Downloads\scp-vietnam\dashboard-standalone
npm install
npm run dev
# Mở browser: http://localhost:3000
```

### Việc 3: WHY Engine Deep Integration (4 changes, all opt-in)
**Files:** `engine.py`, `judge.py`, `why_engine.py` (+208 lines net)

| # | Change | Env Var | Risk |
|---|---|---|---|
| 1 | WHY → Evolution reflect feedback | `SCP_EVOLUTION_ENABLED=1` | LOW |
| 2 | WHY pre-routing (guide SLM) | `SCP_WHY_PRE_ROUTE=1` | MED |
| 3 | WHY confidence adjust | `SCP_WHY_CONFIDENCE_ADJUST=1` | MED |
| 4 | WHY LLM semantic (thay regex) | `SCP_WHY_LLM_ENABLED=1` | HIGH (optional) |

**Tất cả OPT-IN** — default OFF = ZERO behavior change. Không break /ask.

---

## 🎯 CÁCH CHẠY

### Option A: Default (an toàn, giống v5.6)
```powershell
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py
# → Behavior y hệt v5.6, WHY deep disabled
```

### Option B: Bật WHY deep integration (từng bước)
```powershell
# Bật reflect feedback (WHY → Evolution)
$env:SCP_EVOLUTION_ENABLED = "1"

# Bật WHY pre-routing (guide SLM)
$env:SCP_WHY_PRE_ROUTE = "1"

# Bật WHY confidence adjust
$env:SCP_WHY_CONFIDENCE_ADJUST = "1"

# (Optional) Bật WHY LLM semantic
$env:SCP_WHY_LLM_ENABLED = "1"

python start_api.py
```

### Option C: Chạy dashboard standalone
```powershell
# Terminal 1: SCP server
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py

# Terminal 2: Dashboard
cd C:\Users\check\Downloads\scp-vietnam\dashboard-standalone
npm install
npm run dev
# Mở browser: http://localhost:3000
```

---

## 📊 TỔNG PATCHES (31 cái)

| Phiên bản | Patches |
|---|---|
| v5.1-v5.6 | 27 |
| v5.7 | 4 (GeoSLM + Dashboard + 3 WHY changes) |
| **Tổng** | **31** |

---

## ⚠️ LƯU Ý

1. **GeoSLM fix** — Wikipedia API có thể fail (network) → graceful fallback to LocalDB conf=0.7
2. **Dashboard** — cần Node.js 18+ (tải nodejs.org)
3. **WHY deep** — tất cả opt-in, test từng env var trước khi bật tất cả
4. **PASS ≠ ĐÚNG** — GeoSLM fix giúp PASS được câu hỏi địa lý, nhưng vẫn cần test

---

*Build bởi Z.ai — 2026-08-01. v5.7 = v5.6 + GeoSLM + Dashboard + WHY deep. Reality > Model.*
