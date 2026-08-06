# SCP Vietnam v104.75 — v5.9 (System-Wide Scanner + Routing Fix)

> **Bản build v5.9** — 2 việc lớn:
> 1. Mở rộng scanner toàn hệ thống (5 scanners mới + 3 autofix modes)
> 2. Fix routing 26 dead SLMs + CVE routing bug

---

## 🆕 MỚI TRONG V5.9

### Việc 1: 5 Scanners Mới + 3 Autofix Modes

**5 scanners mới** (folder `scp/autofix/scanners/`):
| Scanner | Detect | Bugs found |
|---|---|---|
| DeadSLMScanner | SLM init but never routed | 0 (v5.9 fix đã wire hết) |
| RoutingGapScanner | Domain có <3 keywords hoặc chỉ VN/EN | 2 (holiday, chuck_norris) |
| APIWiringScanner | API key trong .env nhưng không dùng | 4 (2 by-design, 2 real) |
| LogicFlowScanner | `if confidence == 0.7` (fragile exact match) | 1 (slms.py:980 — WIKIPEDIA BUG!) |
| SchemaMismatchScanner | Table definitions drift | 6 (knowledge, experiences, etc.) |

**3 autofix modes mới** (evolution.py):
- `fix_dead_slm` — LLM generate routing keywords
- `wire_api` — LLM generate API call code
- `fix_logic_flow` — Pattern fixer `==` → `>=` (no LLM)

**CLI mới:**
```bash
python -m scp.autofix.runner --full-scan --verbose
python -m scp.autofix.runner --full-scan-and-fix --max-bugs 50
python -m scp.autofix.runner --scan-logic --scan-schema
```

### Việc 2: Fix Routing 26 SLMs + CVE Bug

**Root cause thật (khác spec):**
- Math pattern `\d+\s*[+\-*/%^]\s*\d+` match "2021-44228" trong CVE → route math
- SmartClassifier intercepts trước rule-based routing → V5.9 blocks không fire

**Fix 3 chỗ:**

1. **smart_classifier.py** — CVE pattern check BEFORE math:
   ```python
   _CVE_PATTERN = re.compile(r'\bCVE-\d{4}-\d+\b', re.IGNORECASE)
   if _CVE_PATTERN.search(question):
       return ClassificationResult(domain="cybersecurity", confidence=0.99, ...)
   ```

2. **smart_classifier.py** — Remove single-char operators từ math keywords:
   ```python
   # OLD: ["tính", "calculate", "+", "-", "*", "/"]
   # NEW: ["tính", "calculate", "sqrt", "collatz", ...]
   ```

3. **judge.py** — Remove single-char operators từ rule-based math keywords (same fix)

4. **slms.py:980** — Fix Wikipedia trigger logic:
   ```python
   # OLD: if confidence == 0.7  (fragile exact match)
   # NEW: if confidence >= 0.7  (catches 0.7 and above)
   ```

**Test results (9/9 PASS):**
```
✅ CVE-2021-44228 lỗ hổng gì  → cybersecurity (was math!)
✅ CVE-2023-1234 exploit       → cybersecurity
✅ lỗ hổng bảo mật             → cybersecurity
✅ động đất gần đây            → geology
✅ vật lý lực hấp dẫn          → physics
✅ tính 2 + 2                  → math (still works)
✅ 2+2=?                       → math (still works)
✅ thủ đô Việt Nam             → geography
✅ aspirin thuốc               → medical
```

---

## 🎯 CÁCH CHẠY

### Test routing fixes
```powershell
$env:SCP_SKIP_STARTUP_GATE = "1"
python start_api.py

# Terminal 2:
$BASE = "http://127.0.0.1:8000"
$TOKEN = "<YOUR_SCP_AUTH_TOKEN_SECRET>"

# CVE (was math, now cybersecurity)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"CVE-2021-44228 lỗ hổng gì","domain":"cybersecurity"}'

# Geology (earthquakes)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"động đất gần đây","domain":"geology"}'

# Geography (Wikipedia verify should trigger now)
curl -X POST "$BASE/ask" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"thủ đô Việt Nam","domain":"geography"}'
```

### Run full system scan
```powershell
python -m scp.autofix.runner --full-scan --verbose
# → Shows all bugs: dead SLMs, routing gaps, API wiring, logic flow, schema mismatch
```

---

## 📊 TỔNG PATCHES (39 cái)

| Phiên bản | Patches |
|---|---|
| v5.1-v5.8 | 35 |
| v5.9 | 4 (5 scanners + 3 autofix + routing fix + Wikipedia fix) |
| **Tổng** | **39** |

---

## 🔥 ĐIỂM ĐÁNG CHÚ Ý

1. **LogicFlowScanner found Wikipedia bug** — `if confidence == 0.7` was fragile exact match → Wikipedia never triggered → GeoSLM stuck at conf 0.7 → verdict UNKNOWN
2. **CVE routing fixed** — pattern check BEFORE math, now routes cybersecurity correctly
3. **26 SLMs accessible** — physics, geology, cybersecurity, etc. giờ route đúng
4. **Full system scan** — SCP giờ detect được semantic bugs, không chỉ syntax

---

*Build bởi Z.ai — 2026-08-01. v5.9 = v5.8 + system-wide scanner + routing fix. WHY = quét toàn hệ thống. Reality > Model.*
