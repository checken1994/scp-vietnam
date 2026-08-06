# SCP V105 API Reference

> 64 endpoints total. 100% RBAC enforced (BFLA fixed in fix7).

## Endpoints (64 total)

### Public (3)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ask` | Main endpoint — verify an AI answer |
| `GET` | `/health` | Health check (liveness) |
| `GET` | `/metrics` | Prometheus metrics |

### Admin (61 — require `verify_admin` Bearer token)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v98/status` | V98 security status |
| `GET` | `/v100/antibodies/stats` | Antibody statistics (17 antibodies, 344 keywords) |
| `GET` | `/v100/antibodies/list` | List all antibodies |
| `GET` | `/v100/antibodies/{name}` | Get single antibody details |
| `GET` | `/v105/autofix/stats` | AutoFix statistics |
| `GET` | `/v105/autofix/history` | AutoFix history log |
| `GET` | `/v105/patterns` | List 4 AI patterns (CoT/SelfConsistency/Reflection/ToT) |
| `GET` | `/v105/meta/status` | 5 missing pieces Gà status |
| `GET` | `/v97/why/status` | WHY gate status |
| `GET` | `/v97/why/audit` | WHY gate audit log |
| `GET` | `/v100/governance/constitution` | Constitution text |
| `GET` | `/v100/governance/violations` | Governance violations log |
| `GET` | `/v105/security/dos` | DoS protection status |
| `GET` | `/v105/security/shedder` | Request shedder status |
| `GET` | `/v105/security/playbooks` | 5 IR playbooks |
| `GET` | `/v105/security/predictor` | Attack predictor status |
| `GET` | `/v105/security/escalation` | Escalation manager status |
| `GET` | `/v105/security/cisa-kev` | CISA KEV feed (1656 vulns) |
| `GET` | `/v105/llm/gateway/stats` | LLM gateway stats |
| `GET` | `/v105/llm/providers` | List LLM providers (Ollama + OpenRouter) |
| `GET` | `/v105/cache/stats` | Cache statistics |
| `GET` | `/v105/cache/keys` | List cache keys |
| `DELETE` | `/v105/cache/{key}` | Delete cache entry |
| `GET` | `/v105/experience/log` | Experience log |
| `GET` | `/v105/experience/errors` | Error history |
| `GET` | `/v105/experience/lessons` | Learned lessons |
| `GET` | `/v105/benchmark/run` | Run benchmark |
| `GET` | `/v105/benchmark/results` | Benchmark results |
| `GET` | `/v105/diagnostics/startup` | Startup diagnostics |
| `GET` | `/v105/diagnostics/deps` | Dependency graph |
| `GET` | `/v105/diagnostics/orphans` | Orphan files |
| ... | ... | (32 more admin routes — see `scp/api_server.py` for full list) |

> **Note:** Full list of all 64 routes is in `scp/api_server.py`. The above
> are the most commonly used endpoints.

## `/ask` — Main endpoint

### Request

```http
POST /ask HTTP/1.1
Host: localhost:8000
Authorization: Bearer <TOKEN>
Content-Type: application/json

{
    "question": "Thủ đô Việt Nam?",
    "ai_answer": "Hà Nội",
    "domain": "geography",
    "confidence": 0.9
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `question` | string | yes | User question (max 2000 chars) |
| `ai_answer` | string | no | AI answer to verify (if empty, SLM-only mode) |
| `domain` | string | no | Domain hint (`geography`, `math`, `history`, ...) |
| `confidence` | float | no | Initial confidence (0.0-1.0, default 0.5) |
| `image_url` | string | no | Image URL (VisionHandler describes → ai_answer) |
| `voice_url` | string | no | Voice URL (VoiceHandler transcribes → ai_answer) |

### Response (200 OK)

```json
{
    "verdict": "PASS",
    "final_answer": "Hà Nội",
    "confidence": 0.9,
    "domain": "geography",
    "elapsed_ms": 478,
    "reasoning": "Antibody #3 (capital_city) matched. V13 reality check PASS (REST Countries API).",
    "evidence": {
        "primary_domain": "geography",
        "consistency": true,
        "slm_count": 3,
        "valid_slm_count": 3
    },
    "reality_check": {
        "is_correct": true,
        "verdict": "PASS",
        "real_value": "Hà Nội",
        "source": "rest_countries_api",
        "reason": "REST Countries API confirms capital of Vietnam is Hanoi"
    }
}
```

### Response (429 — Request Shedder)

```json
{
    "error": "request_shed",
    "reason": "Circuit OPEN — shedding low-priority requests",
    "retry_after_ms": 5000
}
```

### Response (401 — Unauthorized)

```json
{
    "error": "invalid_token",
    "reason": "Bearer token missing or invalid"
}
```

## Verdicts

| Verdict | Meaning | Trigger |
|---------|---------|---------|
| `PASS` | Answer verified correct | SLM + reality check both agree with ai_answer |
| `FAIL` | Answer wrong or harmful | Reality check returns real_value ≠ ai_answer |
| `UNKNOWN` | Insufficient evidence (DNA #5) | No SLM hit, no reality check source |
| `KILL` | Governance veto (safety/hallucination) | WHY gate or constitution blocks the answer |
| `CONFLICT` | Conflicting evidence | Independent sources disagree (adversary found different value) |
| `SPECULATIVE` | Speculative mode (low confidence) | Speculative mode enabled, confidence < threshold |
| `PARTIAL` | Partial match | Some facts correct, some incorrect |

## Authentication

All admin endpoints require a Bearer token in the `Authorization` header:

```http
Authorization: Bearer <TOKEN>
```

The token is validated against `SCP_AUTH_TOKEN_SECRET` environment variable.
For dev mode, set:

```bash
export SCP_AUTH_TOKEN_SECRET="test_token_12345"
export SCP_DEV_MODE=1
```

## AI Patterns (4)

| Pattern | When invoked | Effect |
|---------|--------------|--------|
| **Chain-of-Thought (CoT)** | Always (prompt augmentation) | Step-by-step reasoning in system prompt |
| **Self-Consistency** | When LLM verify is called | Sample N=3 times, vote majority verdict |
| **Reflection** | When verdict = FAIL/UNKNOWN | Self-critique + retry once (Reflexion) |
| **Tree of Thoughts (ToT)** | When verdict = UNKNOWN | Explore 3 reasoning framings (semantic / factual / counterfactual); ≥2/3 CORRECT → upgrade to PASS |

> **fix8/Task 19-C:** ToT is now wired into `judge()` reality-check section
> (`scp/runtime/judge.py:2785`). Previously ToT was defined in
> `scp/ai_patterns.py` but never invoked.

## Rate Limiting

- **DoSProtectionEngine** — per-IP request rate tracking, circuit breaker
- **RequestShedder** — priority-based shedding when circuit OPEN
  - `critical` — never shed (auth, health)
  - `normal` — shed when circuit OPEN > 5s
  - `low` — shed immediately when circuit OPEN

## Error Codes

| HTTP | Error | Meaning |
|------|-------|---------|
| 200 | — | Success |
| 400 | `invalid_request` | Malformed JSON or missing required field |
| 401 | `invalid_token` | Bearer token missing or invalid |
| 403 | `forbidden` | Token valid but lacks admin scope |
| 429 | `request_shed` | Circuit OPEN, request shed |
| 429 | `dos_block` | DoS protection triggered |
| 500 | `internal_error` | Unexpected server error (logged) |
| 503 | `llm_unavailable` | LLM gateway offline (Ollama + OpenRouter both down) |
