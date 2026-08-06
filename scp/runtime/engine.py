"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V14 — Multi-SLM + Reality Judge + Self-Healing Engine.

Nâng cấp từ V13:
  V13 = Reality Engine + Generic Pipeline + Self-Healing (single engine)
  V14 = Multi-SLM (Math/Biology/Finance) + Reality Judge (cross-check) + Enhanced Self-Healing

Kiến trúc V14:
                           ???????????????????????
                           ?    SCP V14 Gateway   ?
                           ?  (SCPV14 entry point) ?
                           ???????????????????????
                                      ?
                    ?????????????????????????????????????
                    ?                 ?                 ?
                    ?                 ?                 ?
              ????????????     ????????????     ????????????
              ? MathSLM  ?     ? BioSLM   ?     ? FinSLM   ?
              ? (toán)   ?     ? (sinh)   ?     ? (tài)    ?
              ????????????     ????????????     ????????????
                   ?                ?                ?
                   ???????????????????????????????????
                                    ?
                         ???????????????????????
                         ?   Reality Judge     ?
                         ?  - Cross-check SLMs ?
                         ?  - Verify với V13   ?
                         ?  - Confidence score  ?
                         ???????????????????????
                                    ?
                                    ?
                         ???????????????????????
                         ? Self-Healing Engine ?
                         ?  - 5 healing strategies ?
                         ?  - Monitor + heal    ?
                         ?  - ErrorHistory       ?
                         ???????????????????????

Usage:
    from v14_engine import SCPV14
    engine = SCPV14()
    result = engine.process("Tính 2+3", "2 + 3 = 5")
    print(result.verdict)  # PASS / FAIL / CONFLICT / PARTIAL
"""

import os
import re
import sys

# Import V13 components (base layer)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import logging

from scp.core.healing_engine import (
    HealthStateMachine,
    KnowledgeMemory,
    RecoveryQueue,
)
from scp.core.scp_v14 import SCPV14 as SCPV13

logger = logging.getLogger("scp.v14")
from scp.runtime.engine_parts.direct_api_verifier import DirectAPIVerifier

# [V90] Extracted modules
from scp.runtime.healing_v14 import V14SelfHealingEngine
from scp.runtime.judge import JudgeVerdict, RealityJudge

# [Task 10-B Modularity Refactor B] Re-export extracted helpers — backward compat.
# DirectAPIVerifier + _run_periodic_cleanup moved to engine_parts/antibody_adapter.
# Cache/process_batch/entity extractors moved to engine_parts/slm_coordinator.
# engine_parts split reverted — DirectAPIVerifier stays in engine.py

# Detail constants
DETAIL_NONE = ""
DETAIL_NO_CHECKER = "NO_CHECKER"
DETAIL_NO_DATA = "NO_DATA"
DETAIL_API_ERROR = "API_ERROR"
DETAIL_TIMEOUT = "TIMEOUT"
DETAIL_NO_NUMBER = "NO_NUMBER"

# Import Brain components

# Import Meta-Cognition


# ============================================================
# [Task 10-B] DirectAPIVerifier + _run_periodic_cleanup extracted to
# engine_parts/antibody_adapter.py — re-exported above for backward compat.
# ============================================================

# ============================================================
# SCP V14 — ENTRY POINT
# ============================================================
from scp.runtime.engine_parts.scpv14_process_mixin import SCPV14ProcessMixin


class SCPV14(SCPV14ProcessMixin):
    """
    SCP V14 — Multi-SLM + Reality Judge + Self-Healing Engine.

    V14 = V13 (base) + Multi-SLM (Math/Bio/Finance) + Reality Judge + Enhanced Healing

    Usage:
        engine = SCPV14()
        result = engine.process("Tính 2+3", "2 + 3 = 5")
        print(result.verdict)  # PASS / FAIL / CONFLICT / PARTIAL
    """

    def __init__(self):
        # V13 base engine
        self.v13 = SCPV13()
        # [FIX v26] Direct API Verifier — bypass V13 Regex
        # [EXEC-2 R1] MUST set on INNER self.v13 (core SCPV14) — judge.py:2358
        # reads `self.v13.direct_verifier`. Setting on outer runtime SCPV14 only
        # (as before) left the inner instance's direct_verifier=None, so
        # DirectAPIVerifier NEVER ran for chemistry/weather/currency/crypto/
        # physics/history/geography domains.
        self.v13.direct_verifier = DirectAPIVerifier()
        self.direct_verifier = self.v13.direct_verifier  # backward-compat alias
        # Reality Judge (coordinates SLMs + V13)
        self.judge = RealityJudge(v13_engine=self.v13)
        # V14 Self-Healing (enhanced) — pass judge reference for reduce_error strategy
        self.healing = V14SelfHealingEngine(judge=self.judge)
        # [V89] Wire the original healing subsystems that were imported but never used
        # These write to health_counters, health_states, recovery_issues, knowledge_memory
        self.health_state = HealthStateMachine()
        self.recovery_queue = RecoveryQueue()
        self.knowledge_memory = KnowledgeMemory()

        # [OK] Wire core path for imports
        core_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core')
        if core_path not in sys.path:
            sys.path.insert(0, core_path)

        # [OK] Wire KnowledgeStore (DB1 — main_kb learning)
        self.knowledge_store = None
        try:
            from scp.brain.brain import KnowledgeStore, init_knowledge_db
            init_knowledge_db()
            self.knowledge_store = KnowledgeStore()
        except Exception as e:
            logger.warning(f"KnowledgeStore init failed (non-fatal): {e}")

        # [OK] Wire HypothesisZone (DB2 — partial entries + conflicts)
        self.hypothesis_zone = None
        self.hypothesis_store = None
        try:
            from scp.core import hypothesis_zone
            self.hypothesis_zone = hypothesis_zone
            self.hypothesis_store = hypothesis_zone.HypothesisStore
        except Exception as e:
            logger.warning(f"HypothesisZone init failed (non-fatal): {e}")

        # [OK] Wire RealityAnchor (SHA-256 ground truth verification)
        self.reality_anchor = None
        try:
            from scp.core.anchor import RealityAnchor
            self.reality_anchor = RealityAnchor()
        except Exception as e:
            logger.warning(f"RealityAnchor init failed (non-fatal): {e}")

        # [OK] Wire AntibodyEngine (closure word detection)
        self.antibody = None
        try:
            from scp.core.antibody import AntibodyEngine
            self.antibody = AntibodyEngine()
            # [Z.ai-P0-FIX #24] TẠI SAO: adapter cũ gán `is_closure = scan(text)`
            # nhưng scan() returns True = OK (no closure), False = HAS closure.
            # → "is_closure" = True khi KHÔNG có closure → ĐẢO NGƯỢC logic!
            # Hậu quả: "Hà Nội" → FAIL (bị hiểu là closure), "Obviously true" → PASS.
            # Audit Gà verify: Thủ đô VN → FAIL conf=0.0, 2+3 → FAIL conf=0.0.
            # Fix: dùng scan_detailed() trực tiếp — giữ đầy đủ is_closure, confidence,
            # reason, mitigated. Không qua scan() (V13-compat wrapper gây inversion).
            self.antibody.analyze_claim = lambda text: self.antibody.scan_detailed(text)
        except Exception as e:
            logger.warning(f"AntibodyEngine init failed (non-fatal): {e}")

        # [OK] Wire ExperienceEngine (lessons -> policies)
        self.experience = None
        try:
            from scp.experience.experience import ExperienceEngine
            self.experience = ExperienceEngine()
        except Exception as e:
            logger.warning(f"ExperienceEngine init failed (non-fatal): {e}")

        # [OK] Wire KnowledgeConsolidator (knowledge summaries)
        self.consolidator = None
        try:
            from scp.consolidator.consolidator import KnowledgeConsolidator
            self.consolidator = KnowledgeConsolidator()
        except Exception as e:
            logger.warning(f"KnowledgeConsolidator init failed (non-fatal): {e}")

        # [OK] Wire PredictiveOrchestrator (predictions + verification)
        self.predictive = None
        try:
            from scp.prediction.predictive import PredictiveOrchestrator
            self.predictive = PredictiveOrchestrator()
        except Exception as e:
            logger.warning(f"PredictiveOrchestrator init failed (non-fatal): {e}")

        # [OK] Wire Phase 0 (audit trail — 5 tables)
        self.phase0 = None
        try:
            from scp.core.phase0 import Phase0Store, init_phase0_schema
            init_phase0_schema()
            self.phase0 = Phase0Store
        except Exception as e:
            logger.warning(f"Phase 0 init failed (non-fatal): {e}")

        # [P0] Wire MetaCognitionEngine (self-awareness + learning)
        self.meta = None
        try:
            from scp.meta.meta import MetaCognitionEngine
            self.meta = MetaCognitionEngine()
        except Exception as e:
            logger.warning(f"MetaCognitionEngine init failed (non-fatal): {e}")

        # [PERF] SMART CACHE - Cache verdicts to avoid reprocessing same questions
        self._cache_ttl = 3600  # 1 hour TTL
        self._cache_hits = 0
        self._cache_misses = 0
        # [FIX LEAK] Use SQLite cache instead of dict — 0 RAM growth
        self._init_sqlite_cache()
        pass  # [FIX LEAK] No more in-memory cache

        # [QUALITY] AI Parser - Normalize AI answers
        self._parser = None
        try:
            from scp.foundation.parser import AIParser
            self._parser = AIParser()
        except Exception as e:
            logger.warning(f"AIParser init failed (non-fatal): {e}")

        # [PERF] BATCH API - Process multiple API calls together
        self._batch = None
        try:
            from scp.foundation.batch import BatchAPIProcessor
            self._batch = BatchAPIProcessor()
        except Exception as e:
            logger.warning(f"BatchAPIProcessor init failed (non-fatal): {e}")

        # [MULTI-DOMAIN V44] DataSource Registry - 13 sources across 11 domains
        self._registry = None
        try:
            from scp.data_sources import get_registry, register_all_sources
            self._registry = register_all_sources(get_registry())
            stats = self._registry.get_stats()
            logger.info(f"[REGISTRY V44] Loaded {stats['total_sources']} data sources, "
                        f"{stats['total_intents']} intents")
        except Exception as e:
            logger.warning(f"DataSourceRegistry init failed: {e}")

        # [PERF] PRE-FETCH - Load common data into cache on startup
        self._prefetch_common_data()

        # Stats
        self.cycle_count = 0

    def _init_sqlite_cache(self):
        """[FIX LEAK] Initialize SQLite verdict cache — 0 RAM growth.

        [Task 19-B / Mục 15] Real implementation: in-memory SQLite keeps
        verdict cache bounded — no growth across requests.
        Schema stores pickled JudgeVerdict + timestamp (for TTL eviction,
        to avoid stale cached verdicts bypassing updated security rules).
        """
        import sqlite3
        try:
            self._cache_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._cache_conn.execute(
                "CREATE TABLE IF NOT EXISTS verdicts ("
                "  key TEXT PRIMARY KEY,"
                "  verdict TEXT,"
                "  ts REAL DEFAULT (strftime('%s','now'))"
                ")"
            )
            self._cache_conn.commit()
            # [SECURITY] TTL in seconds — short so security rule updates
            # propagate quickly. Picked 60s as a balance: burst traffic benefits
            # from cache, but stale bypass doesn't outlive a typical deploy.
            self._cache_ttl = getattr(self, "_cache_ttl", 60)
        except Exception as e:
            logger.warning(f"[engine] sqlite cache init failed (non-fatal): {e}")
            self._cache_conn = None

    def _get_sqlite_cache(self, cache_key: str):
        """[FIX LEAK] Get cached verdict from SQLite — 0 RAM.

        [Task 19-B / Mục 15] Returns deserialized JudgeVerdict or None.
        Uses JSON (NOT pickle — bandit B301) and checks TTL so stale
        entries are evicted (security rule updates propagate quickly).
        """
        if not hasattr(self, "_cache_conn") or self._cache_conn is None:
            return None
        try:
            import json
            import time
            cur = self._cache_conn.execute(
                "SELECT verdict, ts FROM verdicts WHERE key=?", (cache_key,)
            )
            row = cur.fetchone()
            if not row:
                return None
            blob, ts = row
            # TTL check
            ttl = getattr(self, "_cache_ttl", 60)
            if ts is not None and (time.time() - float(ts)) > ttl:
                # Stale — evict and miss
                try:
                    self._cache_conn.execute(
                        "DELETE FROM verdicts WHERE key=?", (cache_key,)
                    )
                    self._cache_conn.commit()
                except Exception as e:
                    logger.warning(f"Silent except: {e}")
                return None
            # Deserialize via JSON — safe (no arbitrary code execution)
            data = json.loads(blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob)
            return self._deserialize_verdict(data)
        except Exception as e:
            logger.warning(f"[engine] sqlite cache get failed: {e}")
            return None

    def _set_sqlite_cache(self, cache_key: str, verdict):
        """[FIX LEAK] Save verdict to SQLite cache — 0 RAM.

        [Task 19-B / Mục 15] JSON-serialize the verdict (safe, no pickle)
        and store as TEXT with current timestamp.
        """
        if not hasattr(self, "_cache_conn") or self._cache_conn is None:
            self._init_sqlite_cache()
            if not getattr(self, "_cache_conn", None):
                return
        try:
            import json
            import time
            data = self._serialize_verdict(verdict)
            # [DNA-FIX] Removed default=str — _serialize_verdict already converts to dict.
            # If serialization fails, REJECT (don't silently corrupt with str() repr).
            blob = json.dumps(data, ensure_ascii=False)
            ts = time.time()
            self._cache_conn.execute(
                "INSERT OR REPLACE INTO verdicts (key, verdict, ts) VALUES (?, ?, ?)",
                (cache_key, blob, ts),
            )
            self._cache_conn.commit()
        except Exception as e:
            logger.warning(f"[engine] sqlite cache set failed: {e}")

    @staticmethod
    def _serialize_verdict(verdict) -> dict:
        """Convert a JudgeVerdict (or compatible) to a plain dict for JSON storage."""
        # Prefer dataclass asdict — JudgeVerdict has to_dict() helper
        if hasattr(verdict, "to_dict"):
            try:
                return verdict.to_dict()
            except Exception as e:
                logger.warning(f"Silent except: {e}")
        if hasattr(verdict, "__dict__"):
            return {k: v for k, v in vars(verdict).items() if not k.startswith("_")}
        return {"value": str(verdict)}

    @staticmethod
    def _deserialize_verdict(data: dict):
        """Reconstruct a JudgeVerdict from a dict. Falls back to returning the dict."""
        try:
            # Only pass fields that JudgeVerdict accepts (avoid TypeError on extras)
            import dataclasses as _dc

            from scp.runtime.judge import JudgeVerdict
            valid = {f.name for f in _dc.fields(JudgeVerdict)}
            kwargs = {k: v for k, v in data.items() if k in valid}
            return JudgeVerdict(**kwargs)
        except Exception:
            # If reconstruction fails, return the dict — callers should handle
            # gracefully (most only read .verdict, .confidence, .domain)
            class _BareVerdict:
                pass
            bv = _BareVerdict()
            for k, v in data.items():
                try:
                    setattr(bv, k, v)
                except Exception as e:
                    logger.warning(f"Silent except: {e}")
            return bv

    def _prefetch_common_data(self):
        """Pre-fetch common data into cache on startup.

        [Task 19-B / Mục 15] No-op for now — data_sources handle their own
        caching, and aggressive prefetch was a V13-era optimization that
        conflicted with the leak-fix SQLite cache. Keeping the hook so future
        optimizations can plug in without touching __init__.
        """
        return None

    # ============================================================
    # [V72] BATCH PROCESSING — Process N questions in parallel
    # ============================================================
    def process_batch(self, questions: list[tuple[str, str, str]],
                       max_workers: int = 16) -> list[JudgeVerdict]:
        """[V72→V74] Process a batch of questions IN PARALLEL.

        [Task 19-B / Mục 15] Real implementation using ThreadPoolExecutor.
        Each item is a (question, ai_answer, source) tuple.
        Falls back to sequential on any concurrency error (e.g. SQLite
        connection restricted to one thread).
        """
        if not questions:
            return []
        # Sequential fast path for tiny batches (avoids thread overhead)
        if len(questions) <= 2 or max_workers <= 1:
            return [self.process(q, a, s) for q, a, s in questions]
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results: list[JudgeVerdict | None] = [None] * len(questions)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = {
                    ex.submit(self.process, q, a, s): i
                    for i, (q, a, s) in enumerate(questions)
                }
                for fut in as_completed(futs):
                    idx = futs[fut]
                    try:
                        results[idx] = fut.result()
                    except Exception as e:
                        logger.warning(f"[engine] batch item {idx} failed: {e}")
                        # Construct a fallback FAIL verdict so length is preserved
                        results[idx] = self._batch_fallback_verdict(
                            questions[idx], str(e)
                        )
            # Replace any None with fallback
            return [r if r is not None else self._batch_fallback_verdict(questions[i], "unknown")
                    for i, r in enumerate(results)]
        except Exception as e:
            logger.warning(f"[engine] batch parallel failed, fallback sequential: {e}")
            return [self.process(q, a, s) for q, a, s in questions]

    @staticmethod
    def _batch_fallback_verdict(qas: tuple[str, str, str], reason: str) -> "JudgeVerdict":
        """Build a safe FAIL verdict when a batch worker crashes."""
        try:
            q, a, s = qas
        except Exception:
            q = a = ""
        try:
            return JudgeVerdict(
                question=q, ai_answer=a,
                verdict="FAIL", confidence=0.0,
                domain="unknown",
                reality_check={"reason": f"batch_error: {reason}"},
            )
        except Exception:
            # Last-resort: bare object with verdict attr (callers should not
            # depend on JudgeVerdict being a real instance).
            class _BareVerdict:
                verdict = "FAIL"
                confidence = 0.0
                domain = "unknown"
                question = q
                ai_answer = a
                reality_check = {"reason": f"batch_error: {reason}"}
            return _BareVerdict()  # type: ignore[return-value]


    @staticmethod
    def _extract_entity(question: str, domain: str) -> str:
        """Heuristic: extract entity from question (domain-agnostic).

        [Task 19-B / Mục 15] Real implementation: strips question words
        (EN + VN stop-words) and returns up to 5 content words.
        Falls back to first 50 chars when no content words survive.
        """
        if not question:
            return ""
        # Remove punctuation but keep alphanumerics + spaces + Vietnamese diacritics
        q = re.sub(r"[?.!:;,\-'\"()]", " ", question).strip()
        words = q.split()
        if not words:
            return question[:50]
        stop = {
            # English
            "what", "is", "the", "a", "an", "of", "who", "when", "where",
            "why", "how", "are", "was", "were", "do", "does", "did",
            "can", "could", "should", "would", "in", "on", "at", "to",
            # Vietnamese
            "cái", "gì", "là", "của", "ai", "khi", "đâu", "tại",
            "sao", "như", "thế", "nào", "những", "các", "một", "và",
            "hoặc", "cho", "về", "ở", "được",
        }
        entity_words = [w for w in words if w.lower() not in stop and len(w) >= 2]
        if entity_words:
            return " ".join(entity_words[:5])
        # Fallback: original truncated
        return question[:50]

    @staticmethod
    def _domain_to_attribute(domain: str, question: str = "") -> str:
        """Map domain to knowledge attribute. Question helps infer when domain=unknown.

        [Task 19-B / Mục 15] Real implementation: explicit mapping table
        with question-keyword hints when domain is empty/"unknown".
        """
        mapping = {
            "geography": "capital",
            "chemistry": "formula",
            "biology": "definition",
            "physics": "formula",
            "history": "date",
            "technology": "code",
            "medical": "dosage",
            "finance": "price",
            "math": "result",
            "legal": "statute",
        }
        if domain and domain in mapping:
            return mapping[domain]
        # Infer from question keywords when domain is unknown/empty
        if question:
            ql = question.lower()
            if any(k in ql for k in ("capital", "thủ đô", "country", "nước")):
                return "capital"
            if any(k in ql for k in ("formula", "công thức", "compound")):
                return "formula"
            if any(k in ql for k in ("price", "giá", "cost")):
                return "price"
            if any(k in ql for k in ("when", "năm", "year", "date")):
                return "date"
            if any(k in ql for k in ("what is", "là gì", "định nghĩa", "definition")):
                return "definition"
        return mapping.get(domain, "general")

    def get_report(self) -> dict:
        """V14 full report."""
        v13_report = self.v13.get_report()
        return {
            "v14_cycle_count": self.cycle_count,
            "v14_judge_stats": self.judge.get_stats(),
            "v14_healing_stats": self.healing.get_stats(),
            "v13_report": v13_report,
        }

    def get_error_history(self, limit=10):
        return self.v13.get_error_history(limit)

    def get_similar_errors(self, question, limit=5):
        return self.v13.get_similar_errors(question, limit)

    def start_auto_explore(self, interval_minutes=10, questions_per_run=5):
        """Auto-explore — V14 tự chạy khám phá định kỳ."""
        self.v13.start_auto_explore(interval_minutes, questions_per_run)

    def stop_auto_explore(self):
        self.v13.stop_auto_explore()

