# Task 10-C — Causal Chain Diagram

> **User hỏi:** "tất cả các file mà bạn vừa thêm đã nối vào chuỗi nhân quả chưa?"
> **Verdict:** ✅ Có — sau khi wire `why_engine_cli.py` + `why_execute_plan.py` và xóa `ask_route.py` (stale duplicate), 0 orphan file còn lại.

## Full causal chain — `/ask` HTTP → verdict

```
                                  HTTP POST /ask
                                        │
                                        ▼
                       ┌────────────────────────────────────┐
                       │  api_server.py (FastAPI app, 1251 LOC) │
                       │  @app.post("/ask") — inline handler  │  ← giữ inline (Task 9-B không extract)
                       │  AskRequest → V98 pipeline → verdict  │
                       └─────────────────┬────────────────────┘
                                         │
                                         ▼
                       ┌────────────────────────────────────┐
                       │  judge.judge() (orchestrator)       │
                       │  runtime/judge.py (4,575 LOC)       │
                       │  CC=42 (Task 9-C), 29 phase methods │
                       └─────────────────┬────────────────────┘
                                         │
        ┌─────────────────┬──────────────┼──────────────┬─────────────────────┐
        ▼                 ▼              ▼              ▼                     ▼
┌──────────────┐ ┌────────────────┐ ┌──────────┐ ┌──────────────┐  ┌──────────────────┐
│ _phase_route │ │ _phase_slm_call│ │ _phase_  │ │ _phase_      │  │ _phase_verdict_  │
│   (ctx)      │ │     (ctx)      │ │ retrieve │ │ reality_check│  │   decision(ctx)  │
└──────┬───────┘ └───────┬────────┘ └────┬─────┘ └──────┬───────┘  └────────┬─────────┘
       │                 │               │              │                   │
       ▼                 ▼               ▼              ▼                   ▼
┌──────────────┐ ┌────────────────┐ ┌──────────┐ ┌──────────────┐  ┌──────────────────┐
│ smart_       │ │ runtime/slms.py│ │ real_    │ │ reality_     │  │ builds verdict   │
│ classifier   │ │ (1527 LOC)     │ │ question_│ │ engine.py    │  │ + tolerance      │
│ .py          │ │ ↳ slm_base.py  ││ fetcher  │ │ ↳ llm_gateway│  │ + audit          │
│ (863 LOC)    │ │   (195 LOC) ★  │ │ .py      │ │   /client.py │  └──────────────────┘
│ ↳ judge_     │ │ ↳ slm_impls/*  │ │ (333 LOC)│ │   (Task 10-A)│
│   routing.py │ │   (4 modules ★)│ │ ↳ fetch/│ └──────────────┘
│   (927 LOC)★ │ └────────────────┘ │   (5) ★  │
└──────────────┘                    └──────────┘

       ┌─────────────────┬──────────────┬─────────────────────┐
       ▼                 ▼              ▼                     ▼
┌──────────────┐ ┌────────────────┐ ┌──────────┐  ┌──────────────────┐
│ _phase_      │ │ _phase_antibody│ │ _phase_  │  │ _phase_govern    │
│  why_gate    │ │    (ctx)       │ │  audit   │  │     (ctx)        │
│   (ctx)      │ └───────┬────────┘ └────┬─────┘  └────────┬─────────┘
└──────┬───────┘         │               │                 │
       │                 ▼               ▼                 ▼
       ▼           ┌──────────────┐  ┌──────────┐    ┌──────────────────┐
┌──────────────┐   │ knowledge/   │  │ data/    │    │ meta/governance_ │
│ meta/why_    │   │ antibody_    │  │ *.jsonl  │    │   v97.py         │
│   engine.py  │   │   system.py  │  │ audit log│    │ ↳ constitution.py│
│ (1103 LOC)   │   │ (11 antibodies│ └──────────┘    └──────────────────┘
│ ↳ why_engine_│   │   Task 7-B)  │
│   cli.py ★   │   └──────────────┘
│ ↳ why_execute│
│   _plan.py ★ │
│ ↳ why_v80_   │
│   modes.py ★ │
│ ↳ why_sources│
│   /* (8) ★   │
└──────────────┘

                       ↓ return verdict ↓
                       ┌────────────────────────────┐
                       │  verdict → AskResponse JSON │
                       │  → audit log → knowledge DB │
                       └────────────────────────────┘
```

★ = NEW file from Task 6-10 (verified wired by Task 10-C audit)

## External audit chain (independent — RC-10)

```
tests/external_audit/test_cascade.py (7 tests)  →  ruff F821/E722 + bandit HIGH
tests/external_audit/test_security.py (4 tests) →  scan() callers + DEV_MODE + token
tests/external_audit/conftest.py                →  pytest fixtures
tests/test_judge_characterization.py (10 tests) →  judge() direct call (TDD)
tests/test_golden_23.py (23 tests)              →  /ask HTTP behavioral
staging_wrapper/run_scp_staging.py              →  uvicorn server + monkey-patches
run_benchmark_9a.py                             →  23 golden benchmark runner
```

## CI/CD chain (Task 10-C — NEW)

```
GitHub push / PR
       │
       ▼
.github/workflows/scp-ci.yml
       │
       ├──► lint (ruff F821/E722 + bandit HIGH=0)
       │         ↳ RC-1 invariants (32→0 F821, 1→0 E722 from Task 6-A)
       │         ↳ RC-2/7-B (15→0 HIGH MD5 → SHA-256)
       │
       ├──► test (pytest)
       │         ↳ external_audit/ (11 tests, RC-10)
       │         ↳ test_judge_characterization.py (10 tests, Task 8-B/9-C TDD)
       │         ↳ test_golden_23.py (23 behavioral, optional — needs server)
       │
       ├──► modularity (files >1000 LOC <5)
       │         ↳ Task 7-A/8-A/9-B (-5,727 LOC across 5 files)
       │
       └──► complexity (judge() CC<50)
                 ↳ Task 9-C (CC 228→42, 13 phase methods)

Local dev:
.pre-commit-config.yaml → ruff + bandit + pytest (pre-commit hook)
Makefile                → make ci (local CI runner)
```

## File count summary (Task 6-10)

| Type            | Count | Wired? |
|-----------------|-------|--------|
| NEW .py files   | 44    | ✅ All wired (after Task 10-C fixes) |
| NEW test files  | 6     | ✅ Entry points (pytest) |
| NEW config files| 3     | ✅ Entry points (CI/Make) |
| Orphans found   | 3     | ✅ Fixed (2 wired + 1 deleted) |
| Orphans remaining | 0   | ✅ ZERO |
