# SCP Vietnam v104.75 — v5.6 (Thread Explosion Fix + Endpoint Guide)

> **Bản build v5.6** — fix thread explosion + hướng dẫn test endpoints đúng.

---

## 🎯 CÁCH TEST SCP (QUAN TRỌNG)

### Server đang chạy nhưng /ask trả "Method Not Allowed"?

**Nguyên nhân:** `/ask` là **POST** endpoint (không phải GET). curl mặc định dùng GET.

### Server đang chạy nhưng /v105/autofix/stats trả "Unauthorized"?

**Nguyên nhân:** Endpoint cần `Authorization: Bearer <token>` header. Token = `SCP_AUTH_TOKEN_SECRET` trong .env.

---

## ✅ CÁCH TEST ĐÚNG

### Option 1: Dùng script PowerShell có sẵn

```powershell
cd C:\Users\check\Downloads\scp-vietnam
.\.venv\Scripts\Activate.ps1
.\test_scp_endpoints.ps1
```

Script test 8 endpoints: /, /health, /v98/status, POST /ask (3 câu hỏi), /v105/autofix/stats, dashboard URL.

### Option 2: Test thủ công bằng curl

```powershell
$BASE = "http://127.0.0.1:8000"
$TOKEN = "<YOUR_SCP_AUTH_TOKEN_SECRET>"  # từ .env

# 1. GET / (root — không cần auth)
curl "$BASE/"

# 2. GET /health
curl "$BASE/health"

# 3. GET /v98/status (không cần auth)
curl "$BASE/v98/status"

# 4. POST /ask (CẦN POST + JSON body + auth header)
curl -X POST "$BASE/ask" `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"question":"thủ đô Việt Nam là gì?","ai_answer":"","domain":"geography"}'

# 5. GET /v105/autofix/stats (CẦN auth header)
curl "$BASE/v105/autofix/stats" -H "Authorization: Bearer $TOKEN"

# 6. Mở dashboard
# Browser: http://127.0.0.1:8000/dashboard
# Browser: http://127.0.0.1:8000/docs (Swagger UI — test interactive)
```

---

## 🚨 BUG ĐÃ FIX TRONG V5.6

### Thread Explosion → Server Crash

**Triệu chứng:** Server start OK nhưng crash sau ~30 giây. `curl /ask` → "Failed to connect".

**Root cause:** `get_judge()` trong `scp/api_server.py` có init code nằm **NGOÀI** guard `if _judge is None:`:
- Mỗi request /ask → `get_judge()` → spawn thêm thread + start learning engine + reload patterns
- 262 lần "RealLearningEngine started" + 130 threads trong 27 giây → CPU 100% → crash

**Fix v5.6:**
1. Move toàn bộ init code vào `if _judge is None:` block — chỉ chạy 1 lần
2. Đổi `judge=get_judge()` (recursive) thành `judge=_judge` — no recursion

---

## 📊 SO SÁNH

| Metric | v5.5 | v5.6 |
|---|---|---|
| Server start | ✅ OK | ✅ OK |
| Server sau 30s | ❌ crash (thread leak) | ✅ **stable** |
| `/ask` endpoint | ❌ connection refused | ✅ **works** (POST + auth) |
| `/v105/autofix/stats` | ❌ connection refused | ✅ **works** (auth header) |

---

## 📦 TỔNG PATCHES (27 cái)

| Phiên bản | Patches |
|---|---|
| v5.1 | 6 (AutoFix bootstrap) |
| v5.2 | 5 (Tier-3 auto-approve) |
| v5.3 | 6 (Evolution Engine + 3 modules) |
| v5.4 | 6 (3 wirings + scanner fix + .env) |
| v5.5 | 3 (.env auto-load + pattern fixer + rate limit) |
| v5.6 | 1 (thread explosion fix) |
| **Tổng** | **27** |

---

## 📁 FILES MỚI TRONG V5.6

- `scp/api_server.py` — fix get_judge() thread explosion
- `test_scp_endpoints.ps1` — script test 8 endpoints (PowerShell)
- `QUICKSTART_V56.md` — file này

---

## ⚠️ LƯU Ý QUAN TRỌNG

1. **`/ask` là POST** — không phải GET. curl mặc định GET → "Method Not Allowed"
2. **`/v105/autofix/*` cần auth** — header `Authorization: Bearer <SCP_AUTH_TOKEN_SECRET>`
3. **Token trong .env:** `SCP_AUTH_TOKEN_SECRET=<YOUR_SCP_AUTH_TOKEN_SECRET>`
4. **Dashboard:** http://127.0.0.1:8000/dashboard (không cần auth)
5. **Swagger UI:** http://127.0.0.1:8000/docs (test interactive, có nút "Authorize")

---

*Build bởi Z.ai — 2026-08-01. v5.6 = v5.5 + thread fix + endpoint guide. Reality > Model.*
