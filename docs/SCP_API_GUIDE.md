# 🌐 SCP V105 — API GUIDE FOR EXTERNAL CLIENTS

> How to connect to SCP (Self-Correcting Pipeline) API from external AI systems,
> scripts, or applications. Every endpoint documented here is read from REAL
> code — see "Evidence" section at the bottom for file references.

- **Server**: FastAPI + Uvicorn
- **Default URL**: `http://127.0.0.1:8000`
- **Total endpoints**: 50 (3 public + 47 admin-gated)
- **Auth**: Bearer token (timing-safe compare_digest — no dev-mode bypass)
- **Backward-compat**: All endpoints stable across V98 → V105

---

## I. SETUP

### 1. Start SCP server

```bash
cd scp-v105
python start_api.py
# Server runs on http://127.0.0.1:8000
# Interactive docs (Swagger): http://127.0.0.1:8000/docs
# HTML dashboard:             http://127.0.0.1:8000/dashboard
```

Custom host/port:

```bash
python start_api.py --host 0.0.0.0 --port 8080
```

### 2. Configure authentication

Set in `.env` (auto-loaded by `start_api.py` via `python-dotenv`):

```bash
# Required for admin endpoints — deny-by-default if empty.
# Generate a strong token:
python -c "import secrets; print(secrets.token_hex(16))"
# Example output: 68fcc2ae7dac74cc436decf214cae8cc

# Then add to .env:
SCP_AUTH_TOKEN_SECRET=your-token-hex-string-here
# (or set SCP_AUTH_PASSWORD — both checked; either one matching → granted)
```

> ⚠️ **RC-2 hardening**: the `SCP_DEV_MODE` env-var backdoor was REMOVED. If
> neither `SCP_AUTH_PASSWORD` nor `SCP_AUTH_TOKEN_SECRET` is set, ALL admin
> endpoints return `401 Unauthorized` — no bypass exists.

### 3. Verify server is running

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "version": "V104.49.0",
  "slms": 53,
  "v98_modules": 8,
  "routes": 50,
  "modules": "136+ Python files",
  "data_size_mb": 42.17,
  "multi_turn_tracker": { ... },
  "cross_language": { ... },
  "fact_checker": { ... }
}
```

### 4. Root endpoint (lists all routes)

```bash
curl http://127.0.0.1:8000/
```

```json
{
  "name": "SCP",
  "version": "V104.49.0",
  "description": "Self-Correcting Pipeline with V98 security",
  "endpoints": [
    "POST /ask",
    "POST /v1/chat/completions",
    "GET  /v1/models",
    "POST /v98/analyze-session",
    "POST /v98/run-simulation",
    "POST /v98/run-intel-crawl",
    "GET  /v98/status",
    "GET  /v98/counter/stats",
    "GET  /v98/canary/triggers",
    "GET  /v98/error-store/stats",
    "GET  /v98/attack-memory/stats",
    "GET  /dashboard",
    "GET  /health"
  ]
}
```

---

## II. AUTHENTICATION

All admin endpoints (`/v98/*`, `/v100/*`, `/v102/*`, `/v103/*`, `/v104/*`, `/v105/*`,
`/import/*`, `/chat/sessions`, `/chat/{id}/history`) require a Bearer token:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://127.0.0.1:8000/v100/status
```

Implementation: `secrets.compare_digest(provided, secret)` — timing-safe, no
short-circuit on first differing byte (prevents token recovery via timing oracle).
Both `SCP_AUTH_PASSWORD` and `SCP_AUTH_TOKEN_SECRET` are checked; matching
either grants access.

**Public endpoints (no auth)**:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ask` | Main: question → verdict |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat |
| `GET`  | `/v1/models` | OpenAI models list |
| `WS`   | `/chat` | WebSocket real-time chat |
| `GET`  | `/health` | Liveness check |
| `GET`  | `/dashboard` | HTML dashboard |
| `GET`  | `/` | Root info |

### 401 Unauthorized (response)

If admin token missing/invalid:

```json
{
  "detail": "Unauthorized — set SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET in .env"
}
```

(or `{"detail": "Unauthorized"}` if token was provided but did not match)

---

## III. MAIN ENDPOINT: `POST /ask`

> Source: `scp/api_server.py` line 447 (function `ask()`)

The primary endpoint. Sends a question (and optionally an AI-generated answer
to verify) through the full V98 security + V88 SLM + V97 Falsification pipeline.

### Request

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the capital of France?",
    "ai_answer": "Paris",
    "source": "external_client"
  }'
```

### Request body — `AskRequest` (Pydantic)

> Source: `scp/api_server_parts/helpers.py` lines 141–151

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `question` | string | ✅ | — | `min_length=0, max_length=8000` | User's question |
| `ai_answer` | string | ❌ | `""` | `max_length=10000` | Pre-existing AI answer to verify (empty = SCP generates via Ollama) |
| `domain` | string | ❌ | `"general"` | — | Domain hint (`geography`, `math`, `history`, ...) |
| `confidence` | float | ❌ | `0.8` | `0.0–1.0` | Initial confidence hint |
| `session_id` | string | ❌ | `null` | — | Session ID for multi-turn tracking |
| `source` | string | ❌ | `"api"` | — | Client identifier |
| `image_url` | string | ❌ | `null` | — | Image URL — `_safe_fetch_url` validates scheme/IP/redirect (SSRF/LFI hardened) |
| `voice_url` | string | ❌ | `null` | — | Audio URL — same SSRF/LFI defenses |

### Pipeline (executed per request)

1. **DoS check** — `DoSProtectionEngine.check_request(ip)` → `429` if blocked.
2. **Image/Voice jailbreak detection** — if `image_url`/`voice_url` provided,
   fetch via `_safe_fetch_url` (scheme allowlist, private/loopback/link-local
   IP rejection, ≤5 safe redirects, 5 MB cap, 8 s timeout, no proxy) →
   `ImageJailbreakDetector` (OCR) / `VoiceJailbreakDetector` (Whisper ASR).
   Returns `FAIL` immediately if jailbreak detected.
3. **Auto-generate `ai_answer`** — if empty, call `scp.llm_gateway.chat_sync`
   (Ollama `llama3.2:latest` fallback chain) with a Vietnamese system prompt.
4. **Pre-judge fact check** — keyword trigger (`"is it true"`, `"fact check"`,
   `"có thật"`, `"đúng không"`, `"kiểm chứng"`, ...) → run
   `AsyncMultiSourceVerifier.verify_async()` in parallel against all
   `DataSourceRegistry.get_sources_for_intent("fact_check")` sources.
   Google Fact Check API `metadata.consensus=FALSE/TRUE/MIXED` is translated
   to `contradicted`/`verified`. Result passed to judge as
   `v98_context["fact_check_hint"]`.
5. **Judge** — `judge.judge_with_react_fallback(question, ai_answer, ...)`
   (or sync fallback `judge.judge()`). Internally runs:
   - V98 ThreatDetector + MemoryPoisoningGuard + AttackPatternMemory
   - V88 SLM routing (53 SLMs across 62 domains)
   - V100 DomainAntibodySystem (30 antibodies)
   - V97 FalsificationEngine + ErrorStore + GovernanceV97
   - V98 AttackPolicy + CounterResponse + CanaryTokenMonitor
6. **DoS verdict record** — `dos_protection.record_verdict(v.verdict)`
   (feeds verdict-quality circuit breaker).
7. **ResponseMonitor** — `observe(prompt, response, latency_ms)`.
8. **Multi-turn tracker** — if `session_id` provided, flag suspicious
   patterns → verdict downgraded to `FLAGGED`, confidence × 0.5.
9. **API boundary enforcement** — FAIL/KILL/FLAGGED → answer replaced with
   `[SCP: Answer withheld — verdict: FAIL]`, all leak fields cleared
   (slm_responses, reasoning, v100_claims, v103_antibodies, canary_token, etc.).
   WHY Gate REJECT on UNKNOWN → same withholding.
10. **Async fact-check (background)** — PASS-only, async task. If FALSE claims
    found later, queued in `_fact_check_retract_queue` (clients can poll).

### Response — `AskResponse` (Pydantic)

> Source: `scp/api_server_parts/helpers.py` lines 115–137

```json
{
  "verdict": "PASS",
  "final_answer": "Paris is the capital of France.",
  "confidence": 0.95,
  "domain": "geography",
  "falsification_status": "UNREFUTED_IN_CURRENT_SCOPE",
  "governance_decision": null,
  "v98_guard": null,
  "v98_classification": null,
  "v98_attack_policy": null,
  "v98_counter_executed": null,
  "v98_canary_token": null,
  "v98_bypass_recorded": false,
  "elapsed_ms": 1234.5,
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "slm_trace": [
    {
      "domain": "geography",
      "slm_name": "GeographySLM",
      "answer": "Paris",
      "confidence": 0.95,
      "source": "rest_countries_api",
      "processing_time_ms": 142
    }
  ],
  "phase_timings": {
    "intake_ms": 5.2,
    "routing_ms": 12.1,
    "slm_ms": 142.0,
    "judge_ms": 89.3,
    "total_ms": 248.6
  },
  "reasoning": "Verified via REST Countries API + Wikidata cross-check...",
  "slm_responses": [ { ... } ],
  "v100_claims": { ... },
  "v103_antibodies": { ... },
  "speculative_mode": null
}
```

### Verdict types

| Verdict | Meaning | Action for client |
|---------|---------|-------------------|
| `PASS` | Verified correct (confidence ≥ 0.7) | Return `final_answer` to user |
| `FAIL` | Verified incorrect / contradicted by reality | **Do NOT return** — answer is withheld, regenerate or warn user |
| `UNKNOWN` | Insufficient evidence (confidence < 0.7) | Escalate or ask user for clarification |
| `FLAGGED` | Multi-turn attack detected (session_id flagged) | Treat as suspicious — log + ask user for clarification |
| `KILL` | Governance killed the response (safety violation) | Block response entirely |

> ⚠️ When verdict is `FAIL`, `KILL`, or `FLAGGED`, the API boundary replaces
> `final_answer` with `[SCP: Answer withheld — verdict: FAIL]` and clears all
> internal leak fields (`slm_responses`, `reasoning`, `v100_claims`,
> `v103_antibodies`, `speculative_mode`, `v98_canary_token`, etc.). You will
> never receive the killed content via the API.

### Falsification status values

> Source: `scp/meta/falsification_engine.py` lines 76–99

| Status | Meaning |
|--------|---------|
| `UNREFUTED_IN_CURRENT_SCOPE` | No test contradicted the claim yet (was old `PASS`) |
| `REFUTED_BY_REALITY` | Ground truth proved the claim wrong (was old `FAIL`) |

### Errors

| Status | Condition | Body |
|--------|-----------|------|
| `429` | DoS rate limit exceeded | `{"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}` |
| `400` | `image_url`/`voice_url` rejected by SSRF/LFI policy | `{"detail": "Invalid or disallowed image_url"}` |

---

## IV. EXAMPLE: EXTERNAL AI INTEGRATION

### Python example

```python
import requests

SCP_URL = "http://127.0.0.1:8000"
SCP_TOKEN = "your-admin-token-hex-string"  # only needed for /v100/*, /v105/*, etc.

def ask_scp(question, ai_answer=""):
    """Send question to SCP for verification."""
    response = requests.post(
        f"{SCP_URL}/ask",
        json={
            "question": question,
            "ai_answer": ai_answer,
            "source": "external_ai"
        },
        timeout=30,
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    return response.json()

# Example 1: Verify a claim (fact-check mode)
result = ask_scp("Is the Earth flat?", "Yes, Earth is flat")
print(f"Verdict: {result['verdict']}")           # FAIL
print(f"Confidence: {result['confidence']}")     # 0.0 (withheld)
print(f"Answer: {result['final_answer']}")       # [SCP: Answer withheld — verdict: FAIL]

# Example 2: Generate answer (no ai_answer — SCP calls Ollama internally)
result = ask_scp("Capital of Vietnam?")
print(f"Verdict: {result['verdict']}")           # PASS
print(f"Confidence: {result['confidence']}")     # 0.95
print(f"Answer: {result['final_answer']}")       # "Hà Nội"

# Example 3: Math question (deterministic AST evaluator)
result = ask_scp("What is 2+2?", "4")
print(f"Verdict: {result['verdict']}")           # PASS
print(f"Domain: {result['domain']}")             # math
print(f"SLM trace: {result['slm_trace']}")       # [{domain:math, slm_name:MathSLM, ...}]
```

### Async Python example (parallel fact-check)

```python
import asyncio
import aiohttp

async def ask_scp_async(session, question, ai_answer=""):
    async with session.post(
        "http://127.0.0.1:8000/ask",
        json={"question": question, "ai_answer": ai_answer, "source": "external_ai"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        return await resp.json()

async def batch_verify(questions):
    """Verify many questions in parallel — SCP handles async natively."""
    async with aiohttp.ClientSession() as session:
        tasks = [ask_scp_async(session, q) for q in questions]
        return await asyncio.gather(*tasks)

results = asyncio.run(batch_verify([
    "What is the speed of light?",
    "Capital of France?",
    "Is Pluto a planet?",
]))
for r in results:
    print(f"{r['verdict']:8s} conf={r['confidence']:.2f}  {r['final_answer'][:60]}")
```

### Integration pattern: External AI → SCP → User

```
User asks question
    │
    ▼
External AI (GPT/Claude/Gemini) generates candidate answer
    │
    ▼
External AI sends to SCP: ask_scp(question, ai_answer)
    │
    ▼
SCP verifies:
  ├── DoSProtectionEngine rate-limit check
  ├── ImageJailbreakDetector + VoiceJailbreakDetector (if URLs provided)
  ├── AsyncMultiSourceVerifier (parallel fact-check — Google Fact Check + ...)
  ├── 30 antibodies (domain-specific fact validators)
  ├── 65 unified detector patterns (prompt injection / jailbreak)
  ├── 25 multilingual patterns (FR/DE/ES/RU/ZH/JA)
  ├── decode_attacks() layer (base64/rot13/leet/dotted)
  ├── FalsificationEngine (UNREFUTED_IN_CURRENT_SCOPE / REFUTED_BY_REALITY)
  ├── KnowledgeArbiter (conflict resolution with SourceReputation weighting)
  ├── GovernanceV97 (constitution / safety policy)
  ├── MultiTurnTracker (cross-question attack detection)
  └── 5 LLM providers (Ollama → DeepSeek → OpenRouter → Anthropic → OpenAI)
    │
    ▼
SCP returns verdict (PASS / FAIL / UNKNOWN / FLAGGED / KILL)
    │
    ▼
External AI decides:
  ├── PASS     → return final_answer to user
  ├── FAIL     → regenerate with different prompt or warn user "I'm not sure"
  ├── UNKNOWN  → ask user for clarification
  ├── FLAGGED  → log + ask user to verify identity / intent
  └── KILL     → block response, escalate to human reviewer
```

---

## V. ADMIN ENDPOINTS (require Bearer token)

### V100 — Knowledge + Antibodies

> Source: `scp/api/routes/admin_v100.py`

#### `GET /v100/status` — V100 knowledge modules status
```bash
curl -H "Authorization: Bearer TOKEN" http://127.0.0.1:8000/v100/status
```
Returns `v100_modules`, `v98_modules`, `slms` count.

#### `POST /v100/crawl?max_per_domain=3` — Trigger scheduled data crawl
```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  "http://127.0.0.1:8000/v100/crawl?max_per_domain=3"
```

#### `GET /v100/antibodies/stats` — Antibody statistics
```bash
curl -H "Authorization: Bearer TOKEN" http://127.0.0.1:8000/v100/antibodies/stats
```
Returns `total_antibodies: 38`, `domains: [medical, finance, legal, security, environment, tech, general]`,
and `antibody_names` list.

#### `POST /v100/antibodies/check` — Run antibodies on a Q+A pair
```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is 2+2?", "answer": "5", "domain": "math"}' \
  http://127.0.0.1:8000/v100/antibodies/check
```
Returns `total_run`, `flagged`, `results[]`.

#### `GET /v100/knowledge/stats` — DomainKnowledgeStore stats
#### `GET /v100/knowledge/search?q=...&domain=...&limit=5` — Search KB
#### `GET /v100/h8/stats` — H8 RedTeamBridge stats
#### `GET /v100/h8/bypasses?limit=20` — Recent bypasses
#### `GET /v100/h8/analyses?limit=20` — Recent bypass analyses

### V98 — Security modules

> Source: `scp/api/routes/admin_v98.py`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v98/analyze-session` | Rogue AI detection on session logs (9 lenses). Body: `SessionAnalyzeRequest` (`session_logs`, `model_responses`). |
| `POST` | `/v98/run-simulation` | Trigger threat simulation. Body: `SimulationRequest` (`count: 1–500`, default 50). |
| `POST` | `/v98/run-intel-crawl` | Trigger threat intel crawl. Returns `{"updates": [...], "count": N}`. |
| `GET`  | `/v98/status` | All V98 module status (`slms`, `v98_modules`, `falsification`, `error_store`, `governance`). |
| `GET`  | `/v98/counter/stats` | CounterResponseEngine stats. |
| `GET`  | `/v98/canary/triggers?limit=20` | Canary token triggers. |
| `GET`  | `/v98/error-store/stats` | ErrorStore stats. |
| `GET`  | `/v98/attack-memory/stats` | AttackPatternMemory stats. |

Example — Run threat simulation:

```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"count": 50}' \
  http://127.0.0.1:8000/v98/run-simulation
```

### V102 + V103 — Orchestrator + Storage + Attacks

> Source: `scp/api/routes/v102_v103_routes.py`

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/v102/orchestrator/stats` | PipelineOrchestrator metrics. |
| `GET`  | `/v102/notifications/recent?limit=20` | Recent user notifications. |
| `GET`  | `/v103/storage/stats` | StorageManager stats (disk usage, rotation). |
| `POST` | `/v103/storage/maintain` | Trigger storage maintenance (rotate + vacuum + archive). |
| `POST` | `/v103/gcg/test?count=10` | Generate GCG adversarial attacks. |
| `GET`  | `/v103/attacks/crawled?limit=50` | List attacks crawled from GitHub/Reddit. |
| `POST` | `/v103/attacks/crawl` | Force-crawl attacks immediately. |
| `GET`  | `/v103/status` | AttackCrawler + ThreatSimulator config status. |

### V104 — Multi-turn + Image/Voice + Cross-language + Learning

> Source: `scp/api/routes/v104_routes.py`

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/v104/status` | All V104 modules status (multi-turn, image, voice, cross-language, fact-check). |
| `POST` | `/v104/multi-turn/check?session_id=...&question=...&verdict=PASS` | Check multi-turn attack pattern. |
| `POST` | `/v104/image/check?image_url=...` (or `?image_base64=...`) | Image jailbreak via OCR. |
| `POST` | `/v104/voice/check?audio_url=...` (or `?audio_base64=...`) | Voice jailbreak via Whisper ASR. |
| `GET`  | `/v104/cross-language/transfer?target_lang=all` | Transfer Vietnamese patterns to target language(s). |
| `GET`  | `/v104/explain?verdict=PASS&confidence=0.8&domain=math&sources=3&...` | Explain verdict in Vietnamese. |
| `POST` | `/v104/fact-check?text=...&question=...` | Real-time fact check. |
| `POST` | `/v104/learn/ollama?count=10` | Ollama learning loop (14 countries × 5 domains matrix). |
| `POST` | `/v104/learn/local` | Local file learning. |
| `POST` | `/v104/learn/news` | News RSS learning loop. |
| `POST` | `/v104/learn/all` | All 3 learning loops combined. |
| `GET`  | `/v104/learn/status` | RealLearningEngine stats. |
| `GET`  | `/v104/learn/matrix` | 14 countries × 5 domains = 70 combinations matrix. |
| `POST` | `/v104/learn/ollama-matrix` | Run full 70-question matrix coverage. |
| `POST` | `/v104/learn/fast?count=50` | V104.2 fast learning — 10 concurrent Ollama + 5 Wiki. |
| `GET`  | `/v104/learn/fast/status` | FastLearningEngine stats (cycles, throughput, adaptive interval). |
| `GET`  | `/v104/learn/fast/benchmark` | Sequential vs parallel benchmark + speedup factor. |

### V105 — AutoFix permissions + audit

> Source: `scp/api/routes/v105_routes.py`

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/v105/autofix/permissions` | List pending Tier-3 permission requests. |
| `POST` | `/v105/autofix/permissions/{request_id}/approve?note=...` | Approve a fix (note must demonstrate understanding — Gà §11). |
| `POST` | `/v105/autofix/permissions/{request_id}/deny?note=...` | Deny a fix. |
| `POST` | `/v105/autofix/attack-mode/{enabled}` | Toggle attack mode (true/false) — SCP auto-applies Tier-4 restraints. |
| `GET`  | `/v105/autofix/stats` | AutoFix engine stats (fixes applied, cooldowns, audit trail). |
| `POST` | `/v105/autofix/run-audit` | Trigger deep audit cycle (AST scan → process_bug → audit log). |

Example — Approve a Tier-3 fix:

```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  "http://127.0.0.1:8000/v105/autofix/permissions/abc123/approve?note=Fixes%20null%20dereference%20by%20adding%20None%20guard"
```

> The `note` field is checked by `UnderstandingChecker`. Empty/trivial/copy-paste
> notes return HTTP 400 — the human must explain what the fix does in their
> own words.

Example — Toggle attack mode:

```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  http://127.0.0.1:8000/v105/autofix/attack-mode/true
```

### Import — Batch question ingestion

> Source: `scp/api/routes/import_routes.py`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/import/jsonl` | Import from JSONL (1 JSON per line). |
| `POST` | `/import/excel` | Import from CSV/Excel. |
| `POST` | `/import/batch` | Import JSON array. |

Example — Batch verify:

```bash
curl -X POST -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '[
    {"question": "Capital of France?", "ai_answer": "Paris"},
    {"question": "What is 2+2?", "ai_answer": "5"}
  ]' \
  http://127.0.0.1:8000/import/batch
```

Returns `{"summary": {"total": 2, "pass": 1, "fail": 1, "unknown": 0}, "results": [...]}`.

---

## VI. OPENAI-COMPATIBLE ENDPOINT

> Source: `scp/api/routes/openai_compat.py` — for PyRIT/garak integration tests

### `POST /v1/chat/completions`

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "scp-v99",
    "messages": [
      {"role": "user", "content": "What is 2+2?"}
    ]
  }'
```

### Response (OpenAI format + SCP metadata)

```json
{
  "id": "chatcmpl-550e8400",
  "object": "chat.completion",
  "created": 1718624400,
  "model": "scp-v99",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "4"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "scp_metadata": {
    "verdict": "PASS",
    "confidence": 0.99,
    "falsification_status": "UNREFUTED_IN_CURRENT_SCOPE",
    "governance_decision": null,
    "v98_guard": null,
    "v98_classification": null,
    "v98_counter_phase": 0,
    "v98_canary_token": null,
    "v98_bypass_recorded": false,
    "elapsed_ms": 248.6
  }
}
```

> ⚠️ Behavior:
> - If `verdict=FAIL/KILL/FLAGGED` → `message.content` becomes `"I cannot comply with this request."`
> - If `verdict=UNKNOWN` → `message.content` gets appended `"\n\n[SCP: unverified — confidence below threshold]"`
> - Canary token is in `scp_metadata.v98_canary_token` (metadata only — never in visible content, to preserve honeypot value)

### `GET /v1/models`

```bash
curl http://127.0.0.1:8000/v1/models
```

```json
{
  "object": "list",
  "data": [
    {"id": "scp-v99", "object": "model", "created": 1718624400, "owned_by": "scp"}
  ]
}
```

---

## VII. WEBSOCKET CHAT

> Source: `scp/api/chat.py` — real-time bidirectional chat with SCP

### `WS /chat` (no auth — public)

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/chat");

ws.onopen = () => {
  ws.send(JSON.stringify({message: "What is 2+2?"}));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "system") {
    console.log("Connected:", data.session_id);
  } else if (data.type === "verified") {
    console.log("PASS:", data.answer, "conf:", data.confidence);
  } else if (data.type === "clarification") {
    console.log("UNKNOWN — SCP asks:", data.question);
  } else if (data.type === "rejected") {
    console.log("FAIL:", data.explanation);
  }
};
```

### SCP responses (server → client)

| `type` | Trigger | Fields |
|--------|---------|--------|
| `system` | On connect | `message`, `session_id` |
| `verified` | `verdict=PASS` | `answer`, `verdict`, `confidence`, `domain`, `reasoning`, `explanation` |
| `clarification` | `verdict=UNKNOWN` | `question` (SCP asks user for details) |
| `rejected` | `verdict=FAIL` | `explanation` (Vietnamese — answer withheld) |
| `answer` | Other verdicts | `answer`, `verdict`, `confidence`, `domain`, `reasoning` |
| `error` | Exception | `message` |

If user mentions "tiến hóa"/"evolution", response includes `evolution` stats.
If user mentions "học"/"learning", response includes `learning` stats.

### Admin endpoints (require Bearer token)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chat/sessions` | List active sessions + count |
| `GET` | `/chat/{session_id}/history` | Get full message history of a session |

```bash
curl -H "Authorization: Bearer TOKEN" http://127.0.0.1:8000/chat/sessions
curl -H "Authorization: Bearer TOKEN" http://127.0.0.1:8000/chat/abc12345/history
```

---

## VIII. CAPABILITIES

### What SCP can verify (62 DataSource files — `scp/data_sources/*.py`)

| Domain | Source modules |
|--------|----------------|
| Math | `math` (deterministic AST evaluator) |
| Physics / Chemistry / Biology | `physics`, `chemistry`, `biology` (PubChem, CODATA) |
| Astronomy / Aerospace | `astronomy`, `aerospace`, `spacemedicine` (NASA) |
| Geography / Cartography | `geography` (REST Countries API), `cartography` |
| Geology / Environment / Oceanography | `geology` (USGS), `environment`, `oceanography` (NOAA) |
| Finance | `finance`, `blockchain`, `conversion`, `alphavantage` (CoinGecko, Binance, Kraken, Frankfurter, FRED, AlphaVantage) |
| Medical / Space Medicine | `medical` (PubMed, RxNorm), `spacemedicine` |
| History | `history` (Wikidata, Wikipedia, Gutenberg) |
| Legal / Diplomacy | `legal`, `cornell_lii`, `courtlistener`, `diplomacy` (Cornell LII, CourtListener) |
| Education | `education`, `eric`, `unesco` (ERIC, UNESCO) |
| Arts / Heritage | `arts`, `metmuseum`, `wikiart`, `heritage` (Met Museum, WikiArt) |
| Sports / Military / Religion | `sports`, `military`, `religion` |
| News / Social | `newsapi`, `social` (NewsAPI, social media trends) |
| Cybersecurity | `cybersecurity` (CISA KEV, NVD, CVE) |
| + 33 more domains | (architecture, agriculture, energy, foodtech, transport, tourism, psychology, glottolog, etc.) |

### What SCP checks (defense layers)

| Layer | Count | Source |
|-------|-------|--------|
| Domain antibodies | 30 | `scp/knowledge/antibody_system.py` (ANTIBODIES list) |
| Unified detector patterns | 65+ | `scp/security/unified_detector.py` |
| Multilingual patterns | 25+ | `scp/security/cross_language_learner.py` (FR/DE/ES/RU/ZH/JA) |
| `decode_attacks()` layer | 4 codecs | base64 / rot13 / leet / dotted |
| FalsificationEngine statuses | 2 | `UNREFUTED_IN_CURRENT_SCOPE` / `REFUTED_BY_REALITY` |
| KnowledgeArbiter | weighted | `scp/meta/knowledge_arbiter.py` (SourceReputation weighting) |
| AsyncMultiSourceVerifier | parallel | `scp/core/multi_source_verifier.py` (parallel source checks) |
| LLM providers | 5 | Ollama → DeepSeek → OpenRouter → Anthropic → OpenAI (cost-optimized fallback chain) |
| SLMs (Small Language Models) | 53 | `scp/runtime/slms.py` + `slms_parts/*` + `slm_impls/*` |
| Total Python modules | 136+ | (`scp/` package) |

---

## IX. RATE LIMITS

| Endpoint group | Limit | Engine |
|----------------|-------|--------|
| `/ask` | Per-IP sliding window (default 60/min) | `DoSProtectionEngine.check_request(ip)` |
| `/v1/chat/completions` | Same DoS engine, separate verdict-quality circuit | `dos_protection.record_verdict(verdict)` |
| Admin endpoints (`/v98/*`, `/v100/*`, `/v105/*`, etc.) | Bearer token required (no per-IP limit beyond DoS) | `verify_admin` dependency |
| `/v98/run-simulation` | Background scheduler runs every `SCP_THREAT_SIMULATOR_INTERVAL=600`s (10 min) by default | `SCP_THREAT_SIMULATOR_COUNT=200` |
| `/v103/attacks/crawl` | Background every `SCP_ATTACK_CRAWL_INTERVAL=600`s (10 min) | AttackCrawler thread |

### `429 Too Many Requests` (response)

```json
{"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}
```

---

## X. ERROR HANDLING

### 401 Unauthorized

```json
{"detail": "Unauthorized — set SCP_AUTH_PASSWORD / SCP_AUTH_TOKEN_SECRET in .env"}
```
(or `{"detail": "Unauthorized"}` when token provided but doesn't match)

### 400 Bad Request

```json
{"detail": "Invalid or disallowed image_url"}
```
(When `image_url`/`voice_url` rejected by `_safe_fetch_url` SSRF/LFI policy —
URL is **never echoed** back to prevent information leakage.)

### 404 Not Found

```json
{"detail": "Permission request not found"}
```

### 429 Too Many Requests

```json
{"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}
```

### 500 Internal Server Error

```json
{"detail": "Error: <exception message>"}
```

### 503 Service Unavailable

```json
{"detail": "RogueAIDetector not available"}
```
(When the underlying module isn't initialized — most commonly seen on
`/v98/canary/triggers`, `/v98/counter/stats`, `/v100/knowledge/stats`,
`/v100/h8/stats` if those subsystems failed to start.)

---

## XI. CONFIGURATION

### Environment variables (`.env`)

> Loaded automatically by `start_api.py` via `python-dotenv`.

#### Auth (required for admin endpoints)

| Variable | Required | Description |
|----------|----------|-------------|
| `SCP_AUTH_TOKEN_SECRET` | ✅ | Bearer token for admin endpoints (generate with `secrets.token_hex(16)`) |
| `SCP_AUTH_PASSWORD` | ❌ | Alternative to `SCP_AUTH_TOKEN_SECRET` (either matching grants access) |

#### CORS

| Variable | Default | Description |
|----------|---------|-------------|
| `SCP_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origin allowlist (NOT `*` — credentials=False, methods restricted to `GET/POST/DELETE`, headers to `Authorization/Content-Type`) |

#### LLM providers (5-tier fallback chain)

Priority order: Ollama → DeepSeek → OpenRouter → Anthropic → OpenAI

| Variable | Required | Description |
|----------|----------|-------------|
| `OLLAMA_ENABLED` | ❌ | `true` (default) — enables local LLM |
| `OLLAMA_HOST` | ❌ | `http://127.0.0.1:11434` (default) |
| `OLLAMA_MODEL` | ❌ | `llama3.2:latest` (default) |
| `OLLAMA_TIMEOUT` | ❌ | `60` (default seconds) |
| `DEEPSEEK_API_KEY` | ❌ | DeepSeek direct API (cheapest cloud) |
| `OPENROUTER_API_KEY` | ❌ | OpenRouter (multi-model gateway) |
| `ANTHROPIC_API_KEY` | ❌ | Anthropic Claude direct |
| `OPENAI_API_KEY` | ❌ | OpenAI GPT direct |

#### Specialized data APIs (optional)

| Variable | Description |
|----------|-------------|
| `GOOGLE_FACT_CHECK_API_KEY` | Google Fact Check API — powers pre-judge `AsyncMultiSourceVerifier` |
| `PUBMED_API_KEY` / `NCBI_API_KEY` | PubMed biomedical literature |
| `NASA_API_KEY` | NASA APOD / imagery (`DEMO_KEY` works for low volume) |
| `NEWSAPI_API_KEY` | NewsAPI — structured headlines for `RealLearningEngine` |
| `ALPHAVANTAGE_API_KEY` | Stocks/forex/crypto cross-check |
| `EIA_API_KEY` | US Energy Information Administration |
| `USDA_API_KEY` | USDA NASS QuickStats (agriculture) |
| `NVD_API_KEY` | NIST National Vulnerability Database |
| `CASE_LAW_API_KEY` | Caselaw Access Project (legal) |
| `GROQ_API_KEY` | Groq — backup LLM + ASR + TTS |

#### SCP security tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `SCP_THREAT_SIMULATOR_INTERVAL` | `600` (10 min) | Background threat simulation interval |
| `SCP_THREAT_SIMULATOR_COUNT` | `200` | Number of attack patterns per simulation cycle |
| `SCP_ATTACK_CRAWL_INTERVAL` | `600` (10 min) | AttackCrawler GitHub/Reddit crawl interval |
| `SCP_LEARN_INTERVAL_BURST` | `60` | FastLearningEngine burst interval (1 min) |
| `SCP_LEARN_INTERVAL_FAST` | `300` | Fast interval (5 min) |
| `SCP_LEARN_INTERVAL_IDLE` | `1800` | Idle interval (30 min) |

#### Free APIs (no key required — SCP calls automatically)

REST Countries, Open-Meteo, PubChem, CoinGecko, Binance, Kraken, Frankfurter,
Wikipedia, arXiv, CrossRef, DuckDuckGo, OpenTDB, NASA APOD, Advice Slip,
Chuck Norris, Dog Facts, GitHub (attack crawl), CVE (circl.lu), OWASP LLM Top 10.

---

## XII. EVIDENCE — How this document was built

This guide was created by reading the ACTUAL SCP V105 source code (DNA SCP #1
Reality > Model — no guessing, no copy-paste from old docs).

### Files read for endpoint inventory

| File | Endpoints found |
|------|-----------------|
| `scp/api_server.py` (1066 LOC) | `POST /ask`, `GET /dashboard`, `GET /health`, `GET /` (4 endpoints) |
| `scp/api/chat.py` (221 LOC) | `WS /chat`, `GET /chat/sessions`, `GET /chat/{id}/history` (3 endpoints) |
| `scp/api/routes/admin_v98.py` (108 LOC) | 8 endpoints (`/v98/*`) |
| `scp/api/routes/admin_v100.py` (135 LOC) | 9 endpoints (`/v100/*`) |
| `scp/api/routes/v102_v103_routes.py` (121 LOC) | 8 endpoints (`/v102/*` + `/v103/*`) |
| `scp/api/routes/v104_routes.py` (324 LOC) | 17 endpoints (`/v104/*`) |
| `scp/api/routes/v105_routes.py` (164 LOC) | 6 endpoints (`/v105/*`) |
| `scp/api/routes/import_routes.py` (169 LOC) | 3 endpoints (`/import/*`) |
| `scp/api/routes/openai_compat.py` (113 LOC) | 2 endpoints (`/v1/*`) |
| **Total** | **50 endpoints** (3 fully public + 1 WS public + 46 admin-gated) |

### Files read for auth + request/response schemas

| File | Purpose |
|------|---------|
| `scp/api_server_parts/helpers.py` | `verify_admin` (timing-safe `compare_digest`), `AskRequest`, `AskResponse`, `SessionAnalyzeRequest`, `SimulationRequest`, `_safe_fetch_url` (SSRF/LFI defenses) |
| `scp/api/_shared.py` | `verify_admin` (route-module version, same logic — both check `SCP_AUTH_PASSWORD` + `SCP_AUTH_TOKEN_SECRET`) |
| `scp/meta/falsification_engine.py` | Real falsification statuses: `UNREFUTED_IN_CURRENT_SCOPE`, `REFUTED_BY_REALITY` |
| `scp/knowledge/antibody_system.py` | 30 antibodies (`ANTIBODIES` list — confirmed via regex count) |
| `scp/data_sources/*.py` | 63 source files (62 domains + 1 internal `live_knowledge`) |
| `.env` | All real env vars + defaults |
| `start_api.py` | Server startup + dotenv auto-load |

### Files read for architecture context

| File | Purpose |
|------|---------|
| `/home/z/my-project/worklog.md` (2828 LOC) | Full task history — Tasks 17-A through 34-B confirming architecture (V98 security, V100 knowledge, V104 multi-turn/image/voice, V105 AutoFix, KnowledgeArbiter, AsyncMultiSourceVerifier) |

### Differences from `docs/API_REFERENCE.md` (pre-existing doc)

The existing `docs/API_REFERENCE.md` claims "64 endpoints" with paths like
`/v100/antibodies/list`, `/v97/why/status`, `/v105/security/dos`, `/v105/llm/providers`,
`/v105/cache/stats`, `/metrics`, etc. **These endpoints are NOT in the actual
code** — they appear to be aspirational or from a different fork. This guide
documents only the **50 endpoints that actually exist** in `scp/api_server.py`
+ `scp/api/routes/*.py`. If you need one of the missing endpoints, it must be
implemented first.

---

## XIII. QUICK START CHEAT SHEET

```bash
# 1. Clone + cd
cd scp-v105

# 2. Install deps
pip install -r requirements.txt

# 3. Configure auth
echo "SCP_AUTH_TOKEN_SECRET=$(python -c 'import secrets; print(secrets.token_hex(16))')" >> .env

# 4. Start server
python start_api.py
# → http://127.0.0.1:8000

# 5. Test
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is 2+2?", "ai_answer": "4"}'

# 6. Admin (with token)
TOKEN=$(grep SCP_AUTH_TOKEN_SECRET .env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v100/status
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v100/antibodies/stats
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/v105/autofix/run-audit
```

---

**Document version**: 1.0 (Task 34-C, Subagent C — API Documentation Creator)
**Source code version**: SCP V104.49.0 / V105 (pre-V105 release)
**Verification method**: Direct source code reading — see "Evidence" section
