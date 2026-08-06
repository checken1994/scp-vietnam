# SCP Vietnam v104.75 — v5.8 (7 API Sources + Data Optimization + Clean Install)

> **Bản build v5.8** — 4 việc lớn:
> 1. Wire 7 data sources với real APIs (medical, cybersecurity, biology, legal, energy, geology, agriculture)
> 2. Filter error_store spam (giảm 3MB)
> 3. Dedup bypasses.jsonl (giảm 6.8MB→1MB)
> 4. Wire source_reputation.sqlite (0 rows → active)
> + CLEAN_INSTALL.ps1 — fix "dính lỗi cũ" khi ghi đè

---

## 🆕 MỚI TRONG V5.8

### Việc 1: 7 Data Sources API (was local-only)

| Source | API | Env Var | Status |
|---|---|---|---|
| medical | PubMed E-utilities | `PUBMED_API_KEY` ✅ | Live tested |
| cybersecurity | circl.lu CVE + NVD | `NVD_API_KEY` (opt) | Live tested |
| biology | NCBI Taxonomy | `NCBI_API_KEY` ✅ | Code verified |
| legal | Caselaw Access Project | `CASE_LAW_API_KEY` (opt) | Code verified |
| energy | EIA v2 + OpenNEM | `EIA_API_KEY` (opt) | Code verified |
| geology | USGS FDSN | (free) | Live tested |
| agriculture | USDA NASS (NEW file) | `USDA_API_KEY` (opt) | Code verified |

**Pattern:** Local-first, API-fallback (không break existing tests).

### Việc 2: Filter error_store spam
- Reject questions <5 chars thực
- Reject pure emoji/symbols
- Reject >50% repeated substring ("Tại sao Tại sao Tại sao")
- Log rejected → `data/error_store_rejected.jsonl`
- **Giảm 3MB → ~500KB**

### Việc 3: Dedup bypasses.jsonl
- Hash check trước khi store (skip duplicates)
- cross_lang: keep English canonical, tag others `translated_variant`
- Script `scripts/dedup_bypasses.py` — one-time cleanup
- **Giảm 6.8MB → ~1MB** (47686 → ~6000 unique)

### Việc 4: Wire source_reputation.sqlite
- New table `source_domain_reputation` (additive, không break existing)
- `record_outcome(source, domain, was_correct)` — called after verdict
- `get_reputation(source, domain)` — returns 0.0-1.0
- SLMs có thể dùng reputation để weight sources

### Bonus: CLEAN_INSTALL.ps1
Fix "dính lỗi cũ" khi ghi đè:
1. Xóa `__pycache__/` (byte-code stale)
2. Xóa `.pyc` files
3. Backup + xóa `data/*.jsonl` (logs cũ)
4. Xóa backup files (`.v5bak`, `.v57bak`, etc.)
5. Verify cấu trúc thư mục

---

## 🎯 CÁCH CÀI ĐẶT SẠCH (QUAN TRỌNG!)

```powershell
# 1. Download scp_v104_v58_full.zip
# 2. Giải nén vào C:\Users\check\Downloads\scp-vietnam (THƯ MỤC MỚI!)

cd C:\Users\check\Downloads\scp-vietnam

# 3. CHẠY CLEAN INSTALL (xóa stale code + logs cũ)
.\CLEAN_INSTALL.ps1

# 4. Tạo venv + install
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 5. Dedup bypasses (one-time, giảm 6.8MB→1MB)
python scripts\dedup_bypasses.py --input data\bypasses.jsonl

# 6. Start server
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py

# 7. Test (terminal khác)
$BASE = "http://127.0.0.1:8000"
$TOKEN = "<YOUR_SCP_AUTH_TOKEN_SECRET>"

# Test medical (PubMed)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"aspirin mechanism","domain":"medical"}'

# Test cybersecurity (CVE)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"CVE-2021-44228","domain":"cybersecurity"}'

# Test geology (USGS earthquakes)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"recent earthquakes","domain":"geology"}'
```

---

## ❓ TẠI SAO GHI ĐÈ VẪN DÍNH LỖI CŨ?

3 nguyên nhân (script CLEAN_INSTALL.ps1 fix hết):

1. **`__pycache__` cũ** — Python compile `.py` → `.pyc`. Ghi đè `.py` nhưng Python dùng byte-code cũ.
2. **`data/*.jsonl` cũ** — Logs cũ (810 bugs, 148 fixes) từ lần chạy trước. Ghi đè code nhưng không xóa data → tưởng "dính bug".
3. **Thư mục sai** — Gà chạy `cd scp_v104` (cũ) thay vì `cd scp-vietnam` (mới từ zip).

---

## 📊 TỔNG PATCHES (35 cái)

| Phiên bản | Patches |
|---|---|
| v5.1-v5.7 | 31 |
| v5.8 | 4 (7 APIs + spam filter + dedup + reputation) |
| **Tổng** | **35** |

---

## ⚠️ LƯU Ý

1. **CLEAN_INSTALL.ps1** — chạy trước khi test v5.8 (fix stale code)
2. **Dedup script** — chạy 1 lần: `python scripts\dedup_bypasses.py --input data\bypasses.jsonl`
3. **API keys** — PubMed + NCBI có sẵn trong .env. EIA/USDA/NVD/CASE_LAW cần đăng ký (free)
4. **Reputation** — tự tích lũy sau mỗi /ask (kiểm tra: `sqlite3 data/scp_reputation.sqlite "SELECT COUNT(*) FROM source_domain_reputation"`)

---

*Build bởi Z.ai — 2026-08-01. v5.8 = v5.7 + 7 APIs + data optimization. Reality > Model.*
