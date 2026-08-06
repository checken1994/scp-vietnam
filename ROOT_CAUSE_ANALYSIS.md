# ROOT CAUSE ANALYSIS — 98% Zero-Confidence Answers

**Task ID**: 35-A (Subagent A — Zero-Confidence Investigator)
**Date**: Investigation of `error_store.jsonl` showing 985/1000 entries with `confidence=0.0` and reasoning "SLM returned no useful answer"
**Working directory**: `/home/z/my-project/scp_v105_fix/`

---

## 1. Symptom

`error_store.jsonl` shows 985/1000 entries returning:
- `verdict = UNKNOWN`
- `confidence = 0.0`
- `reasoning = "SLM returned no useful answer (confidence ~0)"`
- `final_answer = "[SCP] Tôi không có đủ dữ liệu để trả lời câu hỏi này. Đây là ngoài phạm vi kiến thức hiện tại của tôi."`

**Pattern observed**:
- ✅ `"What is 2+2?"` → PASS, confidence=0.99 (MathSLM works — deterministic AST)
- ❌ `"Thủ đô Việt Nam?"` → UNKNOWN, confidence=0.0 (GeographySLM, but was cached → returns str)
- ❌ `"Nước Pháp nằm ở châu lục nào?"` → UNKNOWN, confidence=0.0 (misrouted to general)
- ❌ `"P/E ratio của Apple?"` → UNKNOWN, confidence=0.0 (misrouted to general)
- ❌ `"Is earth flat?"` → UNKNOWN, confidence=0.0 (misrouted to general)

---

## 2. Root Cause — TWO BUGS

### 🔴 BUG #1 (PRIMARY for cached questions): SmartCache disk serialization corrupts SLMResponse

**File**: `scp/core/smart_cache.py`

**Code path**:
```
SLM.predict() → slm_cache_set(name, q, SLMResponse_obj, source)
                → SmartCache.set() → self._disk_set()
                  → json.dumps(value, default=str).encode("utf-8")
                    ↑ SLMResponse is NOT JSON-serializable → default=str fires
                    → stores STRING "SLMResponse(question=..., answer=..., confidence=0.7, ...)"
                    (NOT a real JSON dict)
```

On subsequent read:
```
SLM.predict() → slm_cache_get(name, q)
                → SmartCache.get() → self._disk_get()
                  → json.loads(blob.decode("utf-8"))
                    ↑ returns a STRING (not dict, not SLMResponse)
                → returns the STRING to SLM.predict()
                → SLM.predict() returns the STRING as if it were an SLMResponse
```

Then in `judgecore_mixin.py` `_call_slm()`:
```python
resp = slm.predict(question)   # resp is actually a STRING!
return {
    "answer": resp.answer,     # AttributeError: 'str' object has no attribute 'answer'
    "confidence": resp.confidence,
    ...
}
```

AttributeError is caught silently:
```python
except Exception as e:
    return {"domain": domain, "error": str(e), "confidence": 0.0}
```

Then `valid_responses` filter discards it:
```python
if "error" in r or not r.get("answer"):
    continue
```

→ `valid_responses = []` → `primary = None` → `confidence = 0` → UNKNOWN verdict with "SLM returned no useful answer (confidence ~0)".

### 🔴 BUG #2 (PRIMARY for fresh questions): SmartClassifier misroutes Vietnamese questions to "general"

**File**: `scp/core/smart_classifier.py` + `scp/runtime/judge_parts/judgeroute_mixin.py`

In `judgeroute_mixin.py::_route_question()`:
```python
if self.classifier:
    try:
        domains = self.classifier.classify_multi(question, max_domains=3)
        return domains   # ← ALWAYS returns here if classifier succeeded
    except Exception:
        ...
# Keyword-based routing NEVER runs (dead code)
```

`SmartClassifier.classify()` always succeeds (has fallback to "general" when confidence < 0.15). So the keyword-based routing rules in `_route_question` (which would correctly route "thủ đô" → geography, "châu lục" → geography, "earth flat" → reality) **never execute**.

`SmartClassifier.classify()` returns "general" with low confidence for many Vietnamese questions because:
- `_GEO_PATTERN` only matches English: `r'\b(?:capital|currency|population|area|language)s?\s+of\s+\w+'`
- Keyword scoring uses small keyword lists; Vietnamese questions without strong keyword overlap score < 0.15 → fallback to "general"

Once routed to "general", `GeneralSLM.predict()` only handles 6 specific English patterns ("What type of thing is X?", "Tell me about X", etc.). For questions it can't pattern-match, it returns:
```python
reasoning = "Could not extract entity from question"
confidence = 0.0
```

→ Again `valid_responses` filters this out → UNKNOWN with confidence=0.0.

---

## 3. Evidence

### Evidence A: SmartClassifier routing test

```
Q: Thủ đô Việt Nam?           → domain=geography  confidence=0.228  method=hybrid
Q: Nước Pháp nằm ở châu lục nào? → domain=general  confidence=0.021  method=fallback_low_confidence
Q: What is 2+2?               → domain=math      confidence=0.99   method=deterministic_math_pattern
Q: P/E ratio của Apple?       → domain=general   confidence=0.065  method=fallback_low_confidence
Q: Is earth flat?             → domain=general   confidence=0.080  method=fallback_low_confidence
```

4/5 questions routed to "general" with low confidence → GeneralSLM returns conf=0.0.

### Evidence B: Disk cache contents (verified)

```sql
SELECT cache_key, namespace, identifier, value_blob FROM smart_cache_disk LIMIT 3;
```

Output:
```
namespace = 'slm:GeoSLM'
identifier = 'Thủ đô Việt Nam?'
value_blob = b'"SLMResponse(question=\'Thủ đô Việt Nam?\', answer=\'thủ đô Việt Nam = Hà Nội\', confidence=0.7, domain=\'geography\', ...)"'
```

`json.loads(value_blob)` returns a STRING (the str() repr of SLMResponse), NOT a dict.

Total disk cache entries: **9** (all SLM cache, ALL stored as broken string repr, 0 stored as proper JSON dict).

### Evidence C: GeographySLM.predict() returns STRING for cached question

```python
slm = GeographySLM()
result = slm.predict("Thủ đô Việt Nam?")  # ← cached, returns STRING
# result has NO .answer attribute (it's a str)
# getattr(result, 'answer', 'NO ANSWER') returns 'NO ANSWER'
```

vs. uncached question:
```python
slm.predict("What is the capital of France?")  # ← not cached
# Returns proper SLMResponse: answer='thủ đô France = Paris', confidence=0.7
```

### Evidence D: GeneralSLM has no entity pattern for Vietnamese questions

```python
slm = GeneralSLM()
slm.predict("Nước Pháp nằm ở châu lục nào?")
# → answer='', confidence=0.0, reasoning='Could not extract entity from question'

slm.predict("P/E ratio của Apple?")
# → answer='', confidence=0.0, reasoning='Could not extract entity from question'

slm.predict("Is earth flat?")
# → answer='', confidence=0.0, reasoning='Could not extract entity from question'
```

### Evidence E: Where "SLM returned no useful answer" is logged

**File**: `scp/runtime/judge_parts/judgecore_mixin.py`, line 1138-1141:
```python
elif is_consistent:
    if confidence < 0.05:
        verdict_type = "UNKNOWN"
        reasoning = "SLM returned no useful answer (confidence ~0)"
```

`confidence < 0.05` triggers when `primary` is None (no valid SLM response) → `confidence = primary.get("confidence", 0) if primary else 0` (line 823) → 0.

---

## 4. Fix Proposal

### Fix #1 (LOW RISK — APPLIED): SmartCache SLMResponse serialization

**File**: `scp/core/smart_cache.py`

Modify `slm_cache_get` and `slm_cache_set` helpers:
- `slm_cache_set`: convert SLMResponse to dict via `dataclasses.asdict()` BEFORE storing. This makes `json.dumps` produce a real JSON dict, not a string repr.
- `slm_cache_get`: detect dict (proper deserialized) vs str (legacy broken) vs SLMResponse (in-memory). Convert dict → SLMResponse; ignore legacy str (treat as cache miss → will be re-cached properly).

**Why low risk**:
- Only touches the two SLM-cache helper functions, not the SmartCache class itself.
- Backward-compatible: legacy string entries are silently treated as misses (no errors).
- All 7 SLMs using `slm_cache_get/set` benefit at once (MathSLM, GeoSLM, BioSLM, RealitySLM, HistorySLM, LogicSLM, StatsSLM).
- No schema changes to SQLite.

### Fix #2 (MODERATE RISK — DOCUMENTED ONLY, NOT APPLIED): SmartClassifier routing

**File**: `scp/runtime/judge_parts/judgeroute_mixin.py`

**Proposed change**: When `SmartClassifier.classify_multi()` returns `["general"]` (or any list whose first element is "general"), do NOT bypass the keyword-based routing. Fall through to keyword routing, which has correct rules for Vietnamese ("thủ đô" → geography, "châu lục" → geography, "earth is flat" → reality, etc.).

```python
if self.classifier:
    try:
        domains = self.classifier.classify_multi(question, max_domains=3)
        # [FIX] Only short-circuit if we got a SPECIFIC domain (not "general" fallback).
        # TẠI SAO: SmartClassifier fallback to "general" with low confidence
        # bypasses the keyword-based routing rules below, which correctly handle
        # Vietnamese patterns ("thủ đô", "châu lục", "earth flat", etc.).
        if domains and domains[0] not in ("general", "unknown"):
            self._ROUTE_CACHE[cache_key] = (time.time(), domains)
            return domains
        # else: fall through to keyword routing (which may produce better result)
    except Exception as e:
        logger.debug(f"[V95] SmartClassifier failed: {e}, using fallback")
# ... keyword routing now runs
```

**Why NOT applied automatically**:
- Could change routing for many questions, including ones that currently work.
- Needs regression testing on the existing 7/7 behavioral tests.
- Better suited for orchestrator to apply + run full test suite.

---

## 5. Verification (after Fix #1 applied)

### Before fix:
```
Q: Thủ đô Việt Nam?  (cached)
  answer: 'NO ANSWER'  (str repr returned, no .answer attribute)
  confidence: NO CONF
  source: NO EVIDENCE
```

### After fix (verified):
```
Q: Thủ đô Việt Nam?  (cached)
  answer: 'thủ đô Việt Nam = Hà Nội'
  confidence: 0.7
  source: LocalDB-Cached
```

**5/5 verification tests passed** (`python3 /home/z/my-project/scp_v105_fix/verify_fix_35a.py`):

```
TEST: Cached question returns proper SLMResponse
  [PASS] cached Q → SLMResponse(answer='thủ đô Việt Nam = Hà Nội', conf=0.7)

TEST: Cache roundtrip preserves SLMResponse data
  [PASS] roundtrip preserves SLMResponse(answer='test = 42', conf=0.85)

TEST: Disk cache stores proper JSON dict
  [PASS] disk cache stores proper JSON dict (keys=['question', 'answer', 'confidence', 'domain'])

TEST: End-to-end judge() for cached question
  [PASS] judge() → verdict=PASS, conf=0.485

TEST: Legacy broken string entry treated as miss
  [PASS] legacy broken string entry → treated as cache miss (None)
```

End-to-end results for the 5 evidence questions:

| Question | Before fix | After fix |
|----------|------------|-----------|
| `"Thủ đô Việt Nam?"` (cached) | UNKNOWN conf=0.0 | **PASS conf=0.485** ✅ |
| `"What is 2+2?"` | PASS conf=0.99 | PASS conf=0.99 (unchanged) ✅ |
| `"What is the capital of France?"` | UNKNOWN conf=0.0 (Bug #1) | **PASS conf=0.500** ✅ |
| `"capital of Vietnam"` | UNKNOWN conf=0.0 (Bug #1) | **PASS conf=0.235** ✅ |
| `"Nước Pháp nằm ở châu lục nào?"` | UNKNOWN conf=0.0 (Bug #2) | UNKNOWN conf=0.0 (Bug #2 still present) ⚠️ |
| `"P/E ratio của Apple?"` | UNKNOWN conf=0.0 (Bug #2) | UNKNOWN conf=0.0 (Bug #2 still present) ⚠️ |
| `"Is earth flat?"` | UNKNOWN conf=0.0 (Bug #2) | UNKNOWN conf=0.0 (Bug #2 still present) ⚠️ |

**Bug #1 fully fixed** — all 4 questions previously broken by cache serialization now return non-zero confidence.

**Bug #2 still present** — 3 questions still return conf=0.0 because SmartClassifier misroutes them to "general" (fallback_low_confidence), bypassing keyword routing that would correctly route them to geography/reality. Orchestrator should apply Fix #2 after running full regression suite.

---

## 6. Summary

| Bug | Cause | Impact | Fix |
|-----|-------|--------|-----|
| #1 | SmartCache disk serializes SLMResponse via `json.dumps(default=str)` → stores string repr | All cached SLM responses return STRING → AttributeError → conf=0.0 | **APPLIED** in `smart_cache.py` |
| #2 | SmartClassifier misroutes Vietnamese questions to "general", bypassing keyword routing | All fresh non-English/non-pattern questions route to GeneralSLM → no entity pattern → conf=0.0 | **DOCUMENTED** — needs orchestrator approval |

**Combined effect** explains the 98% zero-confidence rate:
- Any question that's been cached before → Bug #1 → conf=0.0
- Any question not matching English patterns in SmartClassifier → Bug #2 → conf=0.0
- Only questions matching deterministic patterns (`2+2`, `capital of X`) survive both bugs.
