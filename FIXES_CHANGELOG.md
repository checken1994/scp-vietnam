# SCP V105 Fixes Changelog

> All fixes by Z.ai Code (1 Orchestrator + 3 subagent song song, không regression).

## fix8 (2026-08-03)
- Fixed 14 bandit HIGH (MD5→SHA256, nosec B310/B307/B102)
- Bandit HIGH: 14→0
- 75/75 tests PASS
- **Task 19-C:** Wire Tree of Thoughts into `judge()` reality-check
  (`scp/runtime/judge.py` — UNKNOWN verdict → ToT explores 3 reasoning
  framings; ≥2/3 CORRECT → upgrade to PASS)
- **Task 19-C:** Analyzed 3 user-uploaded files (scp_analyzer_v2.py,
  `.scp_analysis_cache.db`, `scp_dependency_graph.json`) — see
  `docs/USER_FILES_ANALYSIS.md`
- **Task 19-C:** Created `README.md`, `docs/API_REFERENCE.md`

## fix7 (2026-08-03)
- Wire 5 orphan classes (PlaybookRegistry, RequestShedder, RAGEngine, Vision, Voice)
- Delete 2 YAGNI (FineTuner, LanguageHandler)
- Tree of Thoughts pattern added (`scp/ai_patterns.py` — 4th AI pattern)
- 5 missing pieces Gà created (`scp/meta/`)
- 24 BFLA routes fixed (admin endpoints require `verify_admin`)

## fix6 (2026-08-03)
- 5 antibody bugs fixed (statute, URL, abbreviation, event_date, HTTP status)
- 54 antibody tests PASS

## fix5 (2026-08-03)
- 6 new antibodies (capital, formula, abbreviation, unit, event_date, http_status)
- 344 keywords (was 117), 7 languages
- 3 AI patterns (CoT + SelfConsistency + Reflection)

## fix1-fix4 (2026-08-02)
- 14 tasks: root cause fixes + tech debt + judge refactor + LLM wiring + CI/CD

---

## Legacy v1-v5 (older code, preserved for reference)

### v5 — Predictive Defense + DDoS Survival
- AttackPredictor (5 threat types, trained v2)
- EscalationManager (Dead Man's Switch, 30-min warning)
- 5 pre-authorized IR playbooks (DDoS/APT/SupplyChain/ZeroDay/PromptInjection)
- CISA KEV feed (1656 known exploited vulnerabilities)
- CircuitBreaker + RequestShedder (DDoS survival, 20/20 req PASS)
- Model change: llama-3.3-70b → deepseek-v4-flash (18× cheaper)

### v1-v4 — 27 bug fixes (P0/P1/P2/P3)
- 5 P0 showstoppers (STARTUP-GATE bypass, PredictiveEngine→Orchestrator,
  StartupOptimizer judge arg, ThreeTierCache type, scp-plugin sync)
- 7 P1 critical (re.search NameError, MathSLM routing, MD5→SHA256,
  F841 unused, UP006 typing, Empty SLM≠antibody fail, bypass detector actor)
- 6 P2 geography + WHY (GeoSLM verify, REST Countries, threshold 0.7→0.6,
  Geo routing, Ask mode trusted, WHY engine fixes)
- 3 P3 AutoFix + SmartCache (LLM-powered AutoFix, max_tokens+regex,
  line-number prefix, SmartCache skip FAIL, antibody STUB→real checks)
