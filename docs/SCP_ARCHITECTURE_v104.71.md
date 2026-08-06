# SCP V104.71 — Kiến trúc hệ thống & Chuỗi hành động

> **Verify date**: 2026-07-31
> **Codebase**: 179 Python files, 61,988 lines
> **Verification**: 6 tools (ruff, pylint, mypy, bandit, vulture, semgrep) — 0 real errors. 363 tests pass, 0 fail.

---

## PHẦN 1: CÓ THỰC SỰ HẾT LỖI CHƯA?

### Verification thành thật — 6 tools + pytest

| Tool | Metric | Trước fix (v68) | Sau fix (v71) | Trạng thái |
|------|--------|-----------------|---------------|------------|
| **ruff** | F821 (undefined name) | 15 | 0 | ✅ |
| **ruff** | F601 (duplicate keys) | 3 | 0 | ✅ |
| **ruff** | F841 (unused var) | 15 | 0 | ✅ |
| **ruff** | F541 (f-string) | 15 | 0 | ✅ |
| **pylint** | E0601 (used before assign) | 3 | 0 | ✅ |
| **pylint** | E0606 (possibly used before) | 1 | 0 | ✅ |
| **mypy** | type errors | 669 | 0 | ✅ |
| **bandit** | HIGH severity | 7 | 0 | ✅ |
| **vulture** | real dead code | 10 | 0 | ✅ |
| **semgrep** | defusedxml XXE | 3 | 0 | ✅ |
| **pytest** | tests | 361 pass | **363 pass, 0 fail** | ✅ |

### Còn lại (intentional, không phải bugs)

- **17 F401 unused imports** — feature-detection `try/except ImportError` patterns (intentional)
- **7 vulture items** — stub methods (TODO implement) + feature-detection imports
- **22 print()** — trong files không có logger (cosmetic, không break logic)

### Thành thật

**KHÔNG thể claim "hết lỗi 100%"** — có thể còn bugs mà tools không phát hiện. Nhưng:
- 0 CRITICAL bugs (F821, F601, E0601 = 0)
- 0 HIGH security (bandit HIGH = 0)
- 0 dead code (vulture real = 0)
- 363 tests pass, 0 regression
- 12 bugs THẬT đã fix từ root cause trong fresh audit

---

## PHẦN 2: TOÀN BỘ CHUỖI HÀNH ĐỘNG CỦA SCP

### Pipeline chính — `/ask` endpoint (11 phases)

```
User Request (POST /ask)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 0: PRE-PIPELINE (api_server.py)                       │
│   Systems: DoSProtectionEngine, ImageJailbreakDetector,     │
│            VoiceJailbreakDetector, GuardrailPipeline (4 layers)│
│   Action:                                                   │
│     0a. DoS rate-limit check                                │
│     0b. Multimodal jailbreak detection (image/voice URL)    │
│     0c. Guardrail L1: Relevance (block hacking/medical/...) │
│     0d. Guardrail L2: PII redaction (email/phone/SSN/card)  │
│     0e. Guardrail L3: Moderation (block weapons/hate/...)   │
│   Output: blocked → abstain; pass → question to judge       │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: KNOWLEDGE RETRIEVAL (judge.py:1342)                │
│   Systems: DomainKnowledgeStore (V100)                      │
│   Action: Search KB for cached authoritative answers         │
│   Output: KB hits (tier ≤ 2 + conf ≥ 0.85 → short-circuit)  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: V98 SECURITY CHECKS (judge.py:1380-1478)           │
│   Systems:                                                  │
│     0a. MemoryPoisoningGuard — detect question poisoning    │
│     0b. AttackPatternMemory — check known attack rules      │
│     0c. UnifiedPatternDetector — 4 pattern modules gộp      │
│     0d. ThreatDetector (fingerprint + ASN + behavioral)     │
│     0e. AttackClassifierEngine — classify attack actor      │
│   Action: If CRITICAL threat → early FAIL + abstain         │
│   Output: v98_threat_signal, v98_classification             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼ (if not early FAIL)
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: KB SHORT-CIRCUIT (judge.py:1487-1506)              │
│   Systems: DomainKnowledgeStore                             │
│   Action: If KB hit (tier ≤ 2, conf ≥ 0.85) → return PASS   │
│   Note: Moved BELOW V98 security (was bypass before fix)    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼ (if no KB short-circuit)
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: ROUTE + SLM PREDICT (judge.py:1567-1750)           │
│   Systems:                                                  │
│     - SmartClassifier (route question → domains)            │
│     - 55 SLM classes (MathSLM, BioSLM, GeoSLM, ...)         │
│     - 45 DataSources (Binance, Wikipedia, PubChem, ...)     │
│     - PolicyApplier (adjust threshold + prefer sources)     │
│     - VerdictPredictor (skip API if high prediction conf)   │
│     - WHY Engine (create VerificationPlan)                  │
│   Action:                                                   │
│     1. Route question → 1-3 domains                         │
│     2. Call SLMs (each SLM calls DataSource API)            │
│     3. Each SLM returns SLMResponse(answer, confidence, evidence) │
│   Output: slm_responses[]                                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 5: CROSS-CHECK + CONFLICT RESOLVE (judge.py:1751-1815)│
│   Systems: ConflictResolver                                 │
│   Action:                                                   │
│     - Cross-check SLM answers (consistency)                 │
│     - If multiple SLMs disagree → ConflictResolver          │
│     - Step 5.5: Adversary verify (cross-validate source)    │
│   Output: is_consistent, confidence, final_answer           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 6: VERDICT DECISION (judge.py:1980-2410)              │
│   Systems: RealityJudge._extract_value, calibration         │
│   Action:                                                   │
│     - Compare AI answer vs SLM answer (value extraction)    │
│     - Apply calibration factor (CalibrationEngine)           │
│     - Decide verdict_type: PASS / FAIL / UNKNOWN / CONFLICT │
│     - WHY Engine PASS → upgrade UNKNOWN to PASS (clamped)   │
│   Output: verdict_type, confidence (clamped [0,1])          │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 7: REALITY CHECK (judge.py:2340-2410)                 │
│   Systems:                                                  │
│     - SCPV14 (V13 engine) — full reality verify             │
│     - DirectAPIVerifier — bypass V13 if SLM has real_value  │
│   Action: Verify answer against external API                │
│   Output: reality_check {is_correct, real_value, source}    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 8: BUILD VERDICT (judge.py:2427-2520)                 │
│   Systems: JudgeVerdict dataclass                           │
│   Action: Construct verdict with all evidence               │
│   Output: JudgeVerdict object                               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 9: POST-VERDICT — 7 subsystems (judge.py:2925-3500)   │
│                                                             │
│   9a. FalsificationEngine (V97) — skeptical status check    │
│   9b. Governance (V97) — Constitution UPHOLD/KILL/ESCALATE  │
│       → KILL → verdict=FAIL, final_answer="" (abstain)      │
│   9c. AttackPolicyEngine + CounterResponse (V98)            │
│       → Phase 1: log only                                   │
│       → Phase 2: honeypot canary token                      │
│       → Phase 3: POISON response (opt-in)                   │
│   9d. CanaryTokenMonitor — generate + check triggers        │
│   9e. AttackPatternMemory.record_bypass — if bypass detected│
│   9f. DomainAntibodySystem (V103) — 38 antibodies           │
│       → CRITICAL antibody → verdict=FAIL, final_answer=""   │
│   9g. LogicalAuditorEngine (V107) — GLM logic audit         │
│   9h. SelfQuestioningEngine (V106) — "Tại Sao?" self-critique│
│       → skip if governance_abstain (KILL finality)          │
│                                                             │
│   Output: final verdict with all post-processing            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 10: API BOUNDARY (api_server.py:856-874)              │
│   Systems: AskResponse                                      │
│   Action:                                                   │
│     - If FAIL/KILL/FLAGGED → clear 14 leak fields           │
│     - final_answer = "[SCP: Answer withheld — verdict: X]"  │
│     - PII redacted version for logs                         │
│   Output: AskResponse JSON to client                        │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 11: BACKGROUND LEARNING (async, non-blocking)         │
│   Systems:                                                  │
│     - FastLearningEngine — LLM verify → KB (conf ≤ 0.4)     │
│     - RealLearningEngine — 3 loops (Ollama/Local/News)      │
│     - Consolidator — merge + deduplicate KB facts           │
│     - PredictiveOrchestrator — crawl + predict + verify     │
│     - ReVerifyScheduler — retry self-suspend verdicts       │
│     - CodeEvolutionAgent — auto-fix bugs (backup+test)      │
│     - AutoFixEngine — 4-tier autonomy fix                   │
│   Action: Learn from verdict, update KB, fix code           │
└─────────────────────────────────────────────────────────────┘
```

---

## PHẦN 3: TỪNG CHUỖI — HỆ THỐNG THAM GIA

### Chuỗi 1: Input Processing (Pre-pipeline)
| Hệ thống | Vai trò | File |
|----------|---------|------|
| DoSProtectionEngine | Rate limit per IP | security/dos_protection.py |
| ImageJailbreakDetector | Detect image jailbreak | security/image_voice_detector.py |
| VoiceJailbreakDetector | Detect voice jailbreak | security/image_voice_detector.py |
| RelevanceGuardrail | Block out-of-scope (L1) | security/guardrails.py |
| PIIGuardrail | Redact PII (L2) | security/guardrails.py |
| ModerationGuardrail | Block harmful content (L3) | security/guardrails.py |
| ToolRiskGuardrail | Tool risk assessment (L4) | security/guardrails.py |

### Chuỗi 2: Knowledge Retrieval
| Hệ thống | Vai trò | File |
|----------|---------|------|
| DomainKnowledgeStore | Search cached answers | knowledge/domain_store.py |
| TrustHierarchy | Source tier (axiom→authority→empirical) | knowledge/trust_hierarchy.py |
| SourceReputation | Track source reliability | knowledge/source_reputation.py |
| SourceWatchlist | Block malicious sources | knowledge/source_watchlist.py |

### Chuỗi 3: V98 Security (Input Detection)
| Hệ thống | Vai trò | File |
|----------|---------|------|
| MemoryPoisoningGuard | Detect question poisoning | security/memory_guard.py |
| AttackPatternMemory | Match known attack rules | security/attack_memory.py |
| UnifiedPatternDetector | 4 pattern modules gộp | security/unified_detector.py |
| ThreatDetector | Fingerprint + ASN + behavioral | security/threat_detector.py |
| AttackClassifierEngine | Classify attack actor | security/attack_classifier.py |
| RogueAIDetector | 9 lenses rogue AI detection | security/rogue_ai_detector.py |

### Chuỗi 4: SLM Pipeline (Route + Predict)
| Hệ thống | Vai trò | File |
|----------|---------|------|
| SmartClassifier | Hybrid keyword + semantic route | core/smart_classifier.py |
| BaseSLM + 55 SLM classes | Domain-specific prediction | runtime/slms.py |
| 45 DataSources | External API calls | data_sources/*.py |
| PolicyApplier | Adjust threshold + prefer sources | meta/policy_applier.py |
| VerdictPredictor | Skip API if high prediction conf | meta/verdict_predictor.py |
| WHY Engine | Create VerificationPlan | meta/why_engine.py |
| LLMGateway | Ollama + OpenRouter fallback | llm_gateway/client.py |

### Chuỗi 5: Cross-check + Reality Verify
| Hệ thống | Vai trò | File |
|----------|---------|------|
| ConflictResolver | Resolve multi-SLM disagreement | runtime/judge.py |
| AdversaryVerifier | Cross-validate với source khác | meta/adversary_verifier.py |
| SCPV14 (V13 engine) | Full reality verify | runtime/engine.py |
| DirectAPIVerifier | Bypass V13 if SLM has real_value | runtime/engine.py |
| CalibrationEngine | Auto-tune confidence | meta/calibration_engine.py |
| ErrorStoreIndex | TF-IDF search similar FAILs | brain/error_store_index.py |

### Chuỗi 6: Cognitive Layer (Post-verdict)
| Hệ thống | Vai trò | File |
|----------|---------|------|
| CognitiveEngine | 5 cognitive layers | meta/cognitive_engine.py |
| MetaFalsifier | Check plan completeness | meta/falsification_engine.py |
| RecursiveWhy | Đệ quy "Tại sao tin X?" | meta/cognitive_engine.py |
| UnknownStateClassifier | Classify UNKNOWN verdicts | meta/cognitive_engine.py |
| CounterQuestionEngine | Generate alternative framings | meta/cognitive_engine.py |
| ProofGraph | Logical proof verification | meta/cognitive_engine.py |

### Chuỗi 7: Governance + Constitution
| Hệ thống | Vai trò | File |
|----------|---------|------|
| Governance (V97) | UPHOLD/KILL/ESCALATE decision | meta/governance_v97.py |
| Constitution | 20+ inviolable principles | meta/constitution.py |
| PrincipleRules | Domain-specific rules | meta/principle_rules.py |
| DomainAntibodySystem | 38 antibodies (V103) | knowledge/antibody_system.py |
| LogicalAuditorEngine | GLM logic audit (V107) | meta/logical_auditor.py |
| SelfQuestioningEngine | "Tại Sao?" self-critique (V106) | meta/self_questioning.py |

### Chuỗi 8: Counter-Response (V98)
| Hệ thống | Vai trò | File |
|----------|---------|------|
| AttackPolicyEngine | Decide counter phase (1/2/3) | security/attack_policy.py |
| CounterResponseEngine | Execute counter (log/honeypot/POISON) | security/counter_response.py |
| CanaryTokenMonitor | Generate + check canary triggers | security/canary_monitor.py |
| ResponseMonitor | Behavioral anomaly detector | security/response_monitor.py |

### Chuỗi 9: Learning + Self-Healing
| Hệ thống | Vai trò | File |
|----------|---------|------|
| FastLearningEngine | LLM verify → KB (conf ≤ 0.4) | core/fast_learning_engine.py |
| RealLearningEngine | 3 loops (Ollama/Local/News) | core/real_learning_engine.py |
| Brain | Central learning coordinator | brain/brain.py |
| Consolidator | Merge + deduplicate KB | consolidator/consolidator.py |
| ExperienceEngine | Learn from past verdicts | experience/experience.py |
| PredictiveOrchestrator | Crawl + predict + verify | prediction/predictive.py |
| ReVerifyScheduler | Retry self-suspend verdicts | meta/reverify_scheduler.py |
| V14SelfHealingEngine | Retry SLM + heal | runtime/healing_v14.py |

### Chuỗi 10: Auto-Fix + Code Evolution
| Hệ thống | Vai trò | File |
|----------|---------|------|
| AutoFixEngine | 4-tier autonomy fix | autofix/engine.py |
| BugClassifier | Classify bug severity | autofix/classifier.py |
| PermissionGate | Human approval for Tier 3 | autofix/permission.py |
| CodeEvolutionAgent | Scan bug → LLM fix → test → commit | core/code_evolution_agent.py |
| MetaWhyEngine | Root cause analysis (35 matchers) | meta/meta_why.py |

### Chuỗi 11: Infrastructure
| Hệ thống | Vai trò | File |
|----------|---------|------|
| db_manager | SQLite + _db_lock + per-path conns | core/db_manager.py |
| SmartCache | LRU + per-source TTL | core/smart_cache.py |
| DataPartitioner | 3-tier cache (hot/warm/cold) | core/data_partitioner.py |
| PhaseTimer | Per-phase timeout (10s) | runtime/timing_and_restart.py |
| PipelineOrchestrator | Meta-evaluator | runtime/orchestrator.py |
| MemoryManager | Auto-cleanup + GC | core/memory_manager.py |
| AutoBackup | Periodic backup | core/auto_backup.py |

---

## PHẦN 4: CẤU TRÚC SCP HIỆN TẠI (v104.71)

### Directory structure

```
scp/
├── api_server.py          # FastAPI app + 59 endpoints (/ask, /v1/chat/completions, /v98/*, /v100/*)
├── api/                   # Chat API module
├── runtime/               # Core runtime
│   ├── judge.py          # RealityJudge — 3876 lines, 11-phase pipeline
│   ├── engine.py         # SCPV14 — reality check engine
│   ├── slms.py           # 55 SLM classes + 45 DataSources
│   ├── healing_v14.py    # Self-healing engine
│   ├── orchestrator.py   # Pipeline orchestrator
│   └── timing_and_restart.py
├── security/              # V98 security + guardrails
│   ├── guardrails.py     # 4-layer guardrail (NEW v104.69)
│   ├── threat_detector.py
│   ├── attack_memory.py
│   ├── attack_policy.py
│   ├── counter_response.py
│   ├── canary_monitor.py
│   ├── unified_detector.py
│   ├── memory_guard.py
│   ├── dos_protection.py
│   ├── rogue_ai_detector.py
│   ├── threat_simulator.py
│   ├── response_monitor.py
│   └── ... (21 files total)
├── meta/                  # Cognitive + governance layer
│   ├── cognitive_engine.py  # 5 cognitive layers + RecursiveWhy
│   ├── meta_why.py          # Meta-Why root cause (35 matchers)
│   ├── why_engine.py        # WHY verification plans
│   ├── governance_v97.py    # Constitution + UPHOLD/KILL/ESCALATE
│   ├── constitution.py
│   ├── falsification_engine.py
│   ├── calibration_engine.py
│   ├── self_questioning.py
│   ├── logical_auditor.py
│   ├── verdict_predictor.py
│   ├── policy_applier.py
│   └── ... (18 files total)
├── core/                  # Infrastructure
│   ├── db_manager.py        # SQLite + _db_lock
│   ├── smart_cache.py       # LRU + TTL
│   ├── data_partitioner.py  # 3-tier cache
│   ├── fast_learning_engine.py
│   ├── real_learning_engine.py
│   ├── code_evolution_agent.py
│   ├── reality_engine.py
│   ├── phase0.py
│   ├── antibody.py
│   └── ... (20 files total)
├── knowledge/             # KB + trust + reputation
│   ├── domain_store.py
│   ├── trust_hierarchy.py
│   ├── source_reputation.py
│   ├── source_watchlist.py
│   ├── antibody_system.py
│   └── ... (8 files total)
├── brain/                 # Central learning
│   ├── brain.py
│   └── error_store_index.py
├── data_sources/          # 45 external API sources
│   ├── _matching.py         # token boundary match
│   ├── math.py, biology.py, geography.py, ...
│   └── (45 files total)
├── autofix/               # Auto-fix engine
│   ├── engine.py            # 4-tier + import-test + rollback
│   ├── classifier.py
│   ├── permission.py
│   └── runner.py
├── prediction/            # Predictive orchestrator
├── experience/            # Experience engine
├── consolidator/          # KB consolidator
├── capabilities/          # RAG, streaming, tools
├── foundation/            # Parser, batch
├── interfaces/            # IDataSource, ISLM
├── llm_gateway/           # Ollama + OpenRouter
└── scripts/               # CI + audit tools
```

### Codebase metrics

| Metric | Value |
|--------|-------|
| Python files | 179 |
| Total lines | 61,988 |
| Test files | 30 |
| Tests | 363 (0 fail) |
| Largest file | judge.py (3,876 lines) |
| SLM classes | 55 |
| DataSources | 45 |
| Security modules | 21 |
| Meta/cognitive modules | 18 |
| Core infrastructure | 20 |
| API endpoints | 59 |

### SCP Principles (from lore)

1. **"PASS ≠ ĐÚNG"** — passing check ≠ correct
2. **"Không tin một tác nhân. Tin vào một quá trình có khả năng phát hiện khi chính nó sai."**
3. **"Reality > Model"** — external verification > LLM
4. **Evidence-First** — never conclude without independent evidence
5. **API boundary** — FAIL/KILL must withhold answer
6. **Constitution override** — KILL is terminal, no module modifies after
7. **"capability phải được quản trị"** — new capabilities must NOT auto-pass

### Verification status (v104.71)

- ✅ 6 static analysis tools: 0 real errors
- ✅ 363 tests pass, 0 fail
- ✅ 12 bugs fixed from root cause (fresh audit)
- ✅ 4 guardrail layers wired into /ask + /v1/chat/completions
- ✅ 8 SCP invariants preserved
- ✅ Zip v104.71 complete (288 files, 986 KB)
