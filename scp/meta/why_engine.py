"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""
from __future__ import annotations

#!/usr/bin/env python3
"""
SCP V34 — WHY Engine.

Neo hỏi "Tại sao?" → WHY Engine biến câu hỏi thành quy trình kiểm chứng.

Nhiệm vụ của WHY Engine KHÔNG phải trả lời "Tại sao?", mà là:
    1. Câu hỏi này đang hỏi về đối tượng nào?          (target_identification)
    2. Muốn trả lời cần loại bằng chứng nào?           (evidence_type_selection)
    3. Điều gì sẽ được coi là chứng minh?              (proof_criteria)
    4. Điều gì có thể bác bỏ câu trả lời?              (falsification_criteria)

WHY Engine output = VerificationPlan (không phải answer).

Ví dụ:
    Neo: "Tại sao giá bitcoin hiện tại là $62000?"
    WHY Engine tạo VerificationPlan:
        target: bitcoin_price_usd
        evidence_type: real_time_market_data
        proof_criteria: ≥3 exchange APIs agree within 2%
        falsification_criteria: any exchange reports price differing >10%
        verification_strategy: multi_source_median
        sources_to_query: [Binance, Coinbase, Kraken, Bitstamp, KuCoin]

Sau đó RealityJudge thực thi plan → verdict.
"""

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any  # RC-1 FIX: added Optional (was F821 x13)

logger = logging.getLogger("scp.why_engine")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from scp.core.db_manager import db_exec, db_query_all, db_query_one, init_db
from scp.meta.why_sources.crypto import query_crypto as _why_query_crypto
from scp.meta.why_sources.frankfurter import query_frankfurter as _why_query_frankfurter
from scp.meta.why_sources.nasa import query_nasa as _why_query_nasa
from scp.meta.why_sources.open_meteo import query_open_meteo as _why_query_open_meteo
from scp.meta.why_sources.pubchem import query_pubchem as _why_query_pubchem
from scp.meta.why_sources.rest_countries import query_rest_countries as _why_query_rest_countries
from scp.meta.why_sources.wikidata import query_wikidata as _why_query_wikidata

# [Task 7-A/9-B] Source handlers extracted to meta/why_sources/ for modularity
from scp.meta.why_sources.wikipedia import query_wikipedia as _why_query_wikipedia

# [V5.3-WIRE] metawhy_monitor integration — PASSIVE (always runs, no env var).
# TẠI SAO: WHY engine hỏi "Tại sao?" liên tục. Nếu SCP cứ hỏi cùng 1 loại câu hỏi
# lặp đi lặp lại → có thể đang bị bias hoặc stuck in loop. MetaWhyMonitor theo dõi
# patterns + detect loops + alert human (passive — không block WHY engine).
# Lazy singleton + thread-safe — không bao giờ raise ra caller.
_METAWHY_MONITOR_SINGLETON = None
_METAWHY_MONITOR_LOCK = __import__("threading").Lock()


def _get_metawhy_monitor():
    """Lazy singleton for MetaWhyMonitor. Returns None if init failed."""
    global _METAWHY_MONITOR_SINGLETON
    if _METAWHY_MONITOR_SINGLETON is None:
        with _METAWHY_MONITOR_LOCK:
            if _METAWHY_MONITOR_SINGLETON is None:
                try:
                    from scp.meta.metawhy_monitor import MetaWhyMonitor
                    _data_dir = os.environ.get("SCP_DATA_DIR", "data")
                    _METAWHY_MONITOR_SINGLETON = MetaWhyMonitor(data_dir=_data_dir)
                    logger.info("[V5.3-WIRE] MetaWhyMonitor initialized — WHY patterns will be recorded passively")
                except Exception as e:
                    logger.warning(f"[V5.3-WIRE] MetaWhyMonitor init failed: {e} — monitoring disabled")
                    _METAWHY_MONITOR_SINGLETON = False
    return _METAWHY_MONITOR_SINGLETON if _METAWHY_MONITOR_SINGLETON is not False else None


# ============================================================
# SCHEMA
# ============================================================
def init_why_db():
    """Tạo WHY Engine tables."""
    db_exec("""
        CREATE TABLE IF NOT EXISTS why_verification_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            target TEXT,
            evidence_type TEXT,
            proof_criteria TEXT,
            falsification_criteria TEXT,
            verification_strategy TEXT,
            sources_to_query TEXT,
            status TEXT DEFAULT 'pending',
            verdict TEXT,
            executed_at TEXT
        )
    """)
    db_exec("CREATE INDEX IF NOT EXISTS idx_why_status ON why_verification_plans(status)")


@dataclass
class VerificationPlan:
    """Plan sinh ra bởi WHY Engine."""
    question: str
    target: str                          # Đối tượng cần kiểm chứng
    target_type: str                     # Loại đối tượng (entity/value/relationship/event)
    evidence_type: str                   # Loại bằng chứng cần thiết
    proof_criteria: str                  # Điều gì chứng minh
    falsification_criteria: str          # Điều gì bác bỏ
    verification_strategy: str           # Chiến lược verify
    sources_to_query: list[str]          # Sources cần query
    expected_answer_type: str            # numeric/string/boolean/temporal
    confidence_threshold: float          # Ngưỡng confidence để PASS
    reasoning: str                       # Giải thích plan


# ============================================================
# WHY ENGINE
# ============================================================
class WhyEngine:
    """
    WHY Engine — biến câu hỏi Neo thành VerificationPlan.

    4 bước:
        1. target_identification — phân tích câu hỏi, xác định đối tượng
        2. evidence_type_selection — chọn loại bằng chứng phù hợp
        3. proof_criteria_definition — định nghĩa điều kiện chứng minh
        4. falsification_criteria_definition — định nghĩa điều kiện bác bỏ
    """

    # Target type patterns
    TARGET_PATTERNS = {
        "math_expression": {
            "regex": r'(?:tính|calculate|compute)\s+(.+)|(\d+\s*[+\-*/^%]+\s*\d+)|(\d+\s*!)|sqrt\(|gcd\(',
            "type": "value",
            "evidence": "deterministic_calculation",
            "answer_type": "numeric",
        },
        "physical_constant": {
            "regex": r'(?:tốc độ ánh sáng|hằng số planck|số avogadro|gia tốc trọng trường|nhiệt độ sôi|nhiệt độ đóng băng|khối lượng trái đất|hằng số hấp dẫn)',
            "type": "entity",
            "evidence": "codata_constants",
            "answer_type": "numeric",
        },
        "molecular_weight": {
            "regex": r'(?:khối lượng phân tử|molecular weight|molar mass)\s+(.+)',
            "type": "entity",
            "evidence": "chemical_database",
            "answer_type": "numeric",
        },
        "weather_temperature": {
            "regex": r'(?:nhiệt độ|temperature).*(?:tại|ở|at|in)\s+(.+)',
            "type": "value",
            "evidence": "real_time_weather_api",
            "answer_type": "numeric",
        },
        "crypto_price": {
            "regex": r'(?:giá|price of)\s+(bitcoin|ethereum|solana|dogecoin|ripple|tether|monero|litecoin|cardano)',
            "type": "value",
            "evidence": "real_time_market_data",
            "answer_type": "numeric",
        },
        "currency_rate": {
            "regex": r'(?:chuyển đổi|convert)\s+\d+\s+([A-Z]{3})\s+sang\s+([A-Z]{3})',
            "type": "value",
            "evidence": "central_bank_rates",
            "answer_type": "numeric",
        },
        "geography_capital": {
            "regex": r'(?:thủ đô|capital)\s+(?:of\s+|của\s+)?(.+?)(?:\s+là|\?|$)',
            "type": "entity",
            "evidence": "geographic_database",
            "answer_type": "string",
        },
        "history_event": {
            "regex": r'(?:sự kiện|event|năm\s+\d{4})',
            "type": "event",
            "evidence": "historical_records",
            "answer_type": "string",
        },
        "history_person": {
            "regex": r'(?:ai là|who is|who was)\s+(.+)',
            "type": "entity",
            "evidence": "biographical_records",
            "answer_type": "string",
        },
        "biology_constant": {
            "regex": r'(?:nhiễm sắc thể|chromosome|nhiệt độ cơ thể|body temperature|nhịp tim|heart rate)',
            "type": "entity",
            "evidence": "biological_database",
            "answer_type": "numeric",
        },
        "logic_comparison": {
            "regex": r'\d+\s*[<>=!]+\s*\d+',
            "type": "value",
            "evidence": "deterministic_evaluation",
            "answer_type": "boolean",
        },
        "statistics_aggregate": {
            "regex": r'(?:trung bình|mean|median|phương sai|variance|min|max)',
            "type": "value",
            "evidence": "deterministic_calculation",
            "answer_type": "numeric",
        },
    }

    # Evidence type → sources + criteria
    EVIDENCE_STRATEGIES = {
        "deterministic_calculation": {
            "sources": ["PythonAST", "PythonMath"],
            "proof": "AST evaluator returns consistent result",
            "falsification": "Different evaluator returns different result",
            "strategy": "deterministic_ast",
            "confidence_threshold": 0.95,
        },
        "codata_constants": {
            "sources": ["CODATA table", "Wikipedia (cross-check)"],
            "proof": "Value matches CODATA reference within 0.01%",
            "falsification": "Value differs from CODATA by >1%",
            "strategy": "constant_lookup",
            "confidence_threshold": 0.95,
        },
        "chemical_database": {
            "sources": ["PubChem", "Wikidata"],
            "proof": "≥2 sources agree within 0.1%",
            "falsification": "Sources disagree by >5%",
            "strategy": "multi_source_weighted_avg",
            "confidence_threshold": 0.90,
        },
        "real_time_weather_api": {
            "sources": ["Open-Meteo", "Open-Meteo Archive"],
            "proof": "≥2 sources agree within 3°C",
            "falsification": "Sources disagree by >10°C",
            "strategy": "multi_source_median",
            "confidence_threshold": 0.85,
        },
        "real_time_market_data": {
            "sources": ["Binance", "Coinbase", "Kraken", "Bitstamp", "KuCoin"],
            "proof": "≥3 exchanges agree within 2%",
            "falsification": "Any exchange reports price differing >10%",
            "strategy": "multi_source_median",
            "confidence_threshold": 0.90,
        },
        "central_bank_rates": {
            "sources": ["Frankfurter", "open.er-api.com"],
            "proof": "≥2 sources agree within 1%",
            "falsification": "Sources disagree by >5%",
            "strategy": "multi_source_weighted_avg",
            "confidence_threshold": 0.90,
        },
        "geographic_database": {
            "sources": ["REST Countries API", "LocalDB", "Wikipedia"],
            "proof": "≥2 sources return same capital",
            "falsification": "Sources return different capitals",
            "strategy": "majority_vote",
            "confidence_threshold": 0.85,
        },
        "historical_records": {
            "sources": ["LocalDB", "Wikipedia"],
            "proof": "Wikipedia extract contains event keywords",
            "falsification": "Wikipedia returns not_found or mismatch",
            "strategy": "string_match",
            "confidence_threshold": 0.70,
        },
        "biographical_records": {
            "sources": ["LocalDB", "Wikipedia"],
            "proof": "Wikipedia extract contains person keywords",
            "falsification": "Wikipedia returns not_found",
            "strategy": "string_match",
            "confidence_threshold": 0.70,
        },
        "biological_database": {
            "sources": ["InternalKB"],
            "proof": "Value matches internal knowledge base",
            "falsification": "Value differs from KB",
            "strategy": "kb_lookup",
            "confidence_threshold": 0.85,
        },
        "deterministic_evaluation": {
            "sources": ["PythonAST"],
            "proof": "AST evaluator returns boolean result",
            "falsification": "Evaluator returns different boolean",
            "strategy": "deterministic_ast",
            "confidence_threshold": 0.95,
        },
    }

    def __init__(self):
        init_db()
        init_why_db()

    # ============================================================
    # STEP 1: TARGET IDENTIFICATION
    # ============================================================
    def identify_target(self, question: str) -> tuple[str, str, str]:
        """
        Step 1: Xác định đối tượng câu hỏi đang hỏi về.

        Returns:
            (target, target_type, evidence_type)
        """
        q_lower = question.lower()

        for _pattern_name, pattern_info in self.TARGET_PATTERNS.items():
            m = re.search(pattern_info["regex"], q_lower, re.IGNORECASE)
            if m:
                # Extract target entity if possible
                target = question.strip()
                if m.groups():
                    # Use first non-None group
                    for g in m.groups():
                        if g:
                            target = g.strip().rstrip("?").rstrip(".").strip()
                            break
                return target, pattern_info["type"], pattern_info["evidence"]

        # Fallback — generic
        return question[:50], "unknown", "wikipedia_search"

    # [V5.7-WHY] Change 4: LLM-based semantic question classification.
    # TẠI SAO: identify_target() uses hardcoded TARGET_PATTERNS (regex) — works
    # for known patterns ("giá bitcoin", "thủ đô của X") but misses semantic
    # variations ("BTC đang giao dịch ở mức nào?", "Paris là thủ đô nước nào?").
    # LLM understands question semantics, can classify novel phrasings.
    # SAFETY: opt-in via SCP_WHY_LLM_ENABLED=1 (default OFF — regex stays).
    # Falls back to regex on ANY failure (LLM call fails, JSON parse fails,
    # unknown evidence_type returned, etc.). Reuses _call_openrouter from
    # scp.autofix.llm_fix (same OPENROUTER_API_KEY + rate limit budget).
    # Returns: (target, target_type, evidence_type, answer_type) or None.
    def _classify_question_with_llm(self, question: str):
        """[V5.7-WHY] LLM-based question classification (opt-in, regex fallback).

        [ROOT-FIX 44-A] Now routes through gateway.chat_sync(task="why")
        → qwen2.5:7b (multilingual + factual). Previously called
        _call_openrouter directly (bypassed Ollama entirely even when
        Ollama was the user's preferred local provider).
        """
        try:
            from scp.llm_gateway import chat_sync
        except Exception as e:
            logger.debug(f"[V5.7-WHY] LLM gateway import failed: {e}")
            return None

        valid_evidence_types = list(self.EVIDENCE_STRATEGIES.keys()) + ["wikipedia_search"]
        valid_target_types = ("entity", "value", "relationship", "event")
        valid_answer_types = ("numeric", "string", "boolean", "temporal")

        prompt = (
            "Classify this question for fact-verification. "
            "Output ONLY a JSON object (no markdown fences, no explanation).\n\n"
            f"Question: {question}\n\n"
            "Return JSON with these fields:\n"
            '- "target": the entity/value the question asks about (short, <60 chars)\n'
            f'- "target_type": one of {list(valid_target_types)!r}\n'
            f'- "evidence_type": one of {valid_evidence_types!r}\n'
            f'- "answer_type": one of {list(valid_answer_types)!r}\n\n'
            'Example for "Tại sao giá bitcoin hiện tại là $62000?":\n'
            '{"target": "bitcoin_price_usd", "target_type": "value", '
            '"evidence_type": "real_time_market_data", "answer_type": "numeric"}\n\n'
            "Output ONLY the JSON object."
        )

        try:
            # [ROOT-FIX 44-A] task="why" → routes to qwen2.5:7b (multilingual + factual).
            response, _provider = chat_sync(prompt, task="why")
            if not response:
                return None
            # LLM may wrap JSON in markdown fences despite instruction — extract.
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if not json_match:
                logger.debug(f"[V5.7-WHY] LLM response has no JSON: {response[:200]}")
                return None
            parsed = json.loads(json_match.group(0))
            target = str(parsed.get("target", "")).strip()[:60]
            target_type = str(parsed.get("target_type", "")).strip().lower()
            evidence_type = str(parsed.get("evidence_type", "")).strip().lower()
            answer_type = str(parsed.get("answer_type", "")).strip().lower()
            # Validate — if any field is missing or unknown, fall back to regex.
            if not target or not evidence_type:
                logger.debug("[V5.7-WHY] LLM returned incomplete classification")
                return None
            if target_type not in valid_target_types:
                target_type = "entity"  # default rather than fail
            if evidence_type not in valid_evidence_types:
                logger.debug(
                    f"[V5.7-WHY] LLM returned unknown evidence_type: {evidence_type!r} — falling back to regex"
                )
                return None
            if answer_type not in valid_answer_types:
                answer_type = "string"  # default rather than fail
            logger.info(
                f"[V5.7-WHY] LLM classified: target={target!r}, type={target_type}, "
                f"evidence={evidence_type}, answer_type={answer_type}"
            )
            return (target, target_type, evidence_type, answer_type)
        except Exception as e:
            logger.debug(f"[V5.7-WHY] LLM classification failed: {e}")
            return None

    # ============================================================
    # STEP 2: EVIDENCE TYPE SELECTION
    # ============================================================
    def select_evidence_type(self, evidence_type: str) -> dict[str, Any]:
        """
        Step 2: Chọn loại bằng chứng phù hợp.

        Returns:
            {sources, strategy, confidence_threshold}
        """
        return self.EVIDENCE_STRATEGIES.get(evidence_type, {
            "sources": ["Wikipedia"],
            "proof": "Wikipedia extract supports claim",
            "falsification": "Wikipedia contradicts claim",
            "strategy": "wikipedia_search",
            "confidence_threshold": 0.60,
        })

    # ============================================================
    # STEP 3+4: PROOF + FALSIFICATION CRITERIA
    # ============================================================
    def define_criteria(self, evidence_type: str, target: str,
                        answer_type: str) -> tuple[str, str]:
        """
        Step 3+4: Định nghĩa proof + falsification criteria.

        Returns:
            (proof_criteria, falsification_criteria)
        """
        strategy = self.EVIDENCE_STRATEGIES.get(evidence_type, {})

        proof = strategy.get("proof", "Source confirms claim")
        falsification = strategy.get("falsification", "Source contradicts claim")

        # Customize based on answer type
        if answer_type == "numeric":
            proof = f"{proof} (within tolerance for {target})"
            falsification = f"{falsification} (exceeds tolerance for {target})"
        elif answer_type == "string":
            proof = f"{proof} (string match for {target})"
            falsification = f"{falsification} (no match for {target})"
        elif answer_type == "boolean":
            proof = f"{proof} (boolean evaluation for {target})"
            falsification = f"{falsification} (opposite boolean for {target})"

        return proof, falsification

    # ============================================================
    # MAIN — CREATE VERIFICATION PLAN
    # ============================================================
    def create_verification_plan(self, question: str) -> VerificationPlan:
        """
        WHY Engine entry point.

        Neo asks "Tại sao X?" → WHY Engine creates VerificationPlan.

        Args:
            question: Neo's question (vd "Tại sao giá bitcoin là $62000?")

        Returns:
            VerificationPlan with target, evidence, criteria
        """
        # Strip "Tại sao" prefix if present
        clean_q = re.sub(r'^tại\s+sao\s+', '', question, flags=re.IGNORECASE).strip()
        clean_q = re.sub(r'^why\s+', '', clean_q, flags=re.IGNORECASE).strip()

        # [V5.7-WHY] Change 4: LLM-first classification (opt-in), regex fallback.
        # TẠI SAO: identify_target() uses regex TARGET_PATTERNS — works for known
        # patterns but misses semantic variations. If SCP_WHY_LLM_ENABLED=1, try
        # LLM classification first; on any failure (no API key, parse error,
        # unknown evidence_type) fall back to regex. Default OFF = no behavior
        # change. LLM classification reuses _call_openrouter (same rate limit
        # budget as AutoFix LLM calls).
        _why_classifier_used = "regex"  # default — env OFF or LLM never tried
        _llm_was_attempted = False
        target = None
        target_type = None
        evidence_type = None
        answer_type = "string"

        if os.environ.get("SCP_WHY_LLM_ENABLED", "0") == "1":
            _llm_was_attempted = True
            try:
                _llm_result = self._classify_question_with_llm(clean_q)
                if _llm_result is not None:
                    target, target_type, evidence_type, answer_type = _llm_result
                    _why_classifier_used = "llm"
            except Exception as _llm_err:
                # Defensive: _classify_question_with_llm already handles errors
                # internally and returns None, but wrap again to be safe.
                logger.debug(f"[V5.7-WHY] LLM classifier exception (falling back to regex): {_llm_err}")

        # Regex fallback (default path when env not set, OR LLM failed/returned None)
        if target is None or evidence_type is None:
            target, target_type, evidence_type = self.identify_target(clean_q)
            # Determine answer type from regex patterns (existing logic)
            for _pattern_name, pattern_info in self.TARGET_PATTERNS.items():
                if re.search(pattern_info["regex"], clean_q.lower(), re.IGNORECASE):
                    answer_type = pattern_info["answer_type"]
                    break
            if _llm_was_attempted and _why_classifier_used == "regex":
                # LLM was tried but failed/returned None — mark for visibility
                _why_classifier_used = "llm_failed_regex_fallback"

        # Step 2: Select evidence type
        evidence_strategy = self.select_evidence_type(evidence_type)

        # Step 3+4: Define criteria
        proof_criteria, falsification_criteria = self.define_criteria(
            evidence_type, target, answer_type
        )

        plan = VerificationPlan(
            question=question,
            target=target,
            target_type=target_type,
            evidence_type=evidence_type,
            proof_criteria=proof_criteria,
            falsification_criteria=falsification_criteria,
            verification_strategy=evidence_strategy["strategy"],
            sources_to_query=evidence_strategy["sources"],
            expected_answer_type=answer_type,
            confidence_threshold=evidence_strategy["confidence_threshold"],
            reasoning=f"Target='{target}', type={target_type}, evidence={evidence_type}, "
                      f"strategy={evidence_strategy['strategy']}, "
                      f"classifier={_why_classifier_used} [V5.7-WHY]",
        )

        # Save to DB
        self._save_plan(plan)

        return plan

    def _save_plan(self, plan: VerificationPlan) -> None:
        """Save plan to DB. [V39] Skip for deterministic — they don't need plans."""
        # [V39] Don't save plans for deterministic domains — saves 60% DB writes
        if plan.evidence_type in ("deterministic_calculation", "deterministic_evaluation",
                                   "codata_constants", "biological_database"):
            return  # Skip DB write for deterministic
        try:
            ts = datetime.now().astimezone().isoformat()
            db_exec("""
                INSERT INTO why_verification_plans
                (timestamp, question, target, evidence_type, proof_criteria,
                 falsification_criteria, verification_strategy, sources_to_query,
                 status, verdict, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL)
            """, (
                ts, plan.question, plan.target, plan.evidence_type,
                plan.proof_criteria, plan.falsification_criteria,
                plan.verification_strategy,
                json.dumps(plan.sources_to_query),
            ))
        except Exception as e:
            logger.warning(f"WhyEngine save error: {e}")

    # ============================================================
    # EXECUTE PLAN — called by RealityJudge
    # ============================================================
    # [Task 10-C] WIRED: execute_plan extracted to why_execute_plan.py (Task 8-A).
    # WhyEngine module giảm 124 LOC — verification logic delegated.
    def execute_plan(self, plan: VerificationPlan, ai_answer: str) -> dict[str, Any]:
        """[Task 8-A] Delegate — implementation in scp.meta.why_execute_plan.

        See scp.meta.why_execute_plan.execute_plan for full docstring.
        Returns: {verdict, confidence, sources_queried, all_values, reasoning}
        """
        from scp.meta.why_execute_plan import execute_plan as _execute_plan_impl
        return _execute_plan_impl(self, plan, ai_answer)

    def _query_source(self, source_name: str, target: str, question: str) -> str | None:
        """Query a single source for the target value."""
        source_name_lower = source_name.lower().strip()

        try:
            # [FIX #21] TẠI SAO: LocalDB was listed in sources_to_query but _query_source
            # had no handler for it -> silent skip -> WHY engine never queried the trusted
            # local cache. Reality > Model: tested "capital of France" -> sources_to_query
            # includes "LocalDB" but sources_queried returned only ["REST Countries API",
            # "Wikipedia"] -> FAIL because LocalDB (which has the answer) was never asked.
            # Fix: add LocalDB handler that reads from GeographySLM's _local dict.
            if "localdb" in source_name_lower or "local db" in source_name_lower:
                return self._query_local_db(target, question)
            # Wikipedia
            if "wikipedia" in source_name_lower:
                return self._query_wikipedia(target, question)
            # PubChem
            # [Z.ai-FIX #23b] Thêm Wikidata handler (khai báo nhưng không có)
            elif "pubchem" in source_name_lower:
                return self._query_pubchem(target)
            elif "wikidata" in source_name_lower:
                return self._query_wikidata(target, question)
            # REST Countries
            elif "rest_countries" in source_name_lower or "rest countries" in source_name_lower:
                return self._query_rest_countries(target)
            # Open-Meteo (weather)
            elif "open-meteo" in source_name_lower or "open_meteo" in source_name_lower:
                return self._query_open_meteo(target, question)
            # Binance/CoinGecko (crypto)
            # [Z.ai-ROOT-FIX #23] TẠI SAO: audit Gà verify 5 sàn khai báo
            # (Binance, Coinbase, Kraken, Bitstamp, KuCoin) nhưng chỉ có nhánh
            # "binance"/"coingecko" → 4 sàn còn lại silent None.
            # Fix: tất cả 5 sàn → _query_crypto (dùng CoinGecko API làm fallback).
            elif any(x in source_name_lower for x in
                     ["binance", "coingecko", "coinbase", "kraken", "bitstamp", "kucoin"]):
                return self._query_crypto(target)
            # Frankfurter (currency)
            # [Z.ai-FIX #23] Thêm open.er-api.com (khai báo nhưng không có handler)
            elif "frankfurter" in source_name_lower or "open.er-api.com" in source_name_lower or "er-api" in source_name_lower:
                return self._query_frankfurter(target, question)
            # NASA APOD
            elif "nasa" in source_name_lower:
                return self._query_nasa(target)
            # Unknown source
            else:
                logger.debug(f"WHY: unknown source '{source_name}'")
                return None
        except Exception as e:
            logger.debug(f"WHY query_source '{source_name}' error: {e}")
            return None

    def _query_local_db(self, target: str, question: str) -> str | None:
        """[FIX #21] Query SCP's local DB (GeographySLM cache) for capital/population/area.

        TẠI SAO: LocalDB is the trusted cache populated by previous successful API
        calls. It has capitals for 50+ countries. When REST Countries API is
        deprecated (Bug #16/#22), LocalDB is the authoritative fallback.
        """
        try:
            # Import GeographySLM to access its _local dict
            from scp.runtime.slms import GeographySLM
            # Use a shared instance (don't create new one each call)
            if not hasattr(self, '_geo_slm_for_queries'):
                self._geo_slm_for_queries = GeographySLM()
            geo = self._geo_slm_for_queries

            target_lower = target.lower().strip()
            # Try direct match
            if target_lower in geo._local:
                data = geo._local[target_lower]
                q_lower = question.lower()
                if 'capital' in q_lower:
                    return data.get('capital', '')
                elif 'population' in q_lower:
                    return str(data.get('population', ''))
                elif 'area' in q_lower:
                    return str(data.get('area', ''))
                else:
                    return data.get('capital', '')  # default to capital
            # Try fuzzy match
            from scp.data_sources._matching import _token_boundary_match
            for key, data in geo._local.items():
                if _token_boundary_match(key, target_lower):
                    q_lower = question.lower()
                    if 'capital' in q_lower:
                        return data.get('capital', '')
                    elif 'population' in q_lower:
                        return str(data.get('population', ''))
                    elif 'area' in q_lower:
                        return str(data.get('area', ''))
                    else:
                        return data.get('capital', '')
            return None
        except Exception as e:
            logger.debug(f"WHY LocalDB query error: {e}")
            return None

    def _query_wikipedia(self, target: str, question: str) -> str | None:
        """[Task 7-A] Delegates to meta.why_sources.wikipedia for modularity.

        Original 63 LOC body extracted to standalone function — same behavior.
        """
        return _why_query_wikipedia(target, question)

    def _query_pubchem(self, target: str) -> str | None:
        """[Task 7-A] Delegates to meta.why_sources.pubchem for modularity.

        Original 15 LOC body extracted to standalone function — same behavior.
        """
        return _why_query_pubchem(target)

    def _query_wikidata(self, target: str, question: str) -> str | None:
        """[Task 9-B] Delegates to meta.why_sources.wikidata for modularity."""
        return _why_query_wikidata(target, question)

    def _query_rest_countries(self, target: str) -> str | None:
        """[Task 9-B] Delegates to meta.why_sources.rest_countries for modularity."""
        return _why_query_rest_countries(target)

    def _query_open_meteo(self, target: str, question: str) -> str | None:
        """[Task 9-B] Delegates to meta.why_sources.open_meteo for modularity."""
        return _why_query_open_meteo(target, question)

    def _query_crypto(self, target: str) -> str | None:
        """[Task 7-A] Delegates to meta.why_sources.crypto for modularity.

        Original 15 LOC body extracted to standalone function — same behavior.
        """
        return _why_query_crypto(target)

    def _query_frankfurter(self, target: str, question: str) -> str | None:
        """[Task 9-B] Delegates to meta.why_sources.frankfurter for modularity."""
        return _why_query_frankfurter(target, question)

    def _query_nasa(self, target: str) -> str | None:
        """[Task 9-B] Delegates to meta.why_sources.nasa for modularity."""
        return _why_query_nasa(target)

    def execute_pending_plans(self, limit: int = 10) -> dict:
        """[WHY-FIX Bước 3] Execute pending plans from DB (background scheduler).

        Returns: {executed, passed, failed, conflicts, unknowns}
        """
        try:
            pending = db_query_all(
                "SELECT id, question, target, evidence_type, proof_criteria, "
                "falsification_criteria, verification_strategy, sources_to_query, "
                "confidence_threshold FROM why_verification_plans "
                "WHERE status='pending' ORDER BY id LIMIT ?",
                (limit,)
            )
        except Exception as e:
            logger.warning(f"WHY execute_pending: query failed: {e}")
            return {"executed": 0, "error": str(e)}

        stats = {"executed": 0, "passed": 0, "failed": 0, "conflicts": 0, "unknowns": 0}
        for row in pending:
            try:
                plan = VerificationPlan(
                    question=row["question"],
                    target=row["target"],
                    target_type="entity",
                    evidence_type=row["evidence_type"],
                    proof_criteria=row["proof_criteria"],
                    falsification_criteria=row["falsification_criteria"],
                    verification_strategy=row["verification_strategy"],
                    sources_to_query=json.loads(row["sources_to_query"]) if row["sources_to_query"] else [],
                    expected_answer_type="string",
                    confidence_threshold=row["confidence_threshold"] or 0.5,
                    reasoning="",
                )
                result = self.execute_plan(plan, ai_answer="")  # No AI answer for background
                stats["executed"] += 1
                v = result.get("verdict", "UNKNOWN")
                if v == "PASS":
                    stats["passed"] += 1
                elif v == "FAIL":
                    stats["failed"] += 1
                elif v == "CONFLICT":
                    stats["conflicts"] += 1
                else:
                    stats["unknowns"] += 1
            except Exception as e:
                logger.debug(f"WHY execute_pending: row {row.get('id')}: {e}")

        logger.info(f"WHY Engine: executed {stats['executed']} pending plans — "
                    f"PASS={stats['passed']}, FAIL={stats['failed']}, "
                    f"CONFLICT={stats['conflicts']}, UNKNOWN={stats['unknowns']}")
        return stats

    # ============================================================
    # STATS
    # ============================================================
    def get_stats(self) -> dict[str, Any]:
        """Stats cho WHY Engine."""
        try:
            total = db_query_one("SELECT COUNT(*) as cnt FROM why_verification_plans")["cnt"]
            pending = db_query_one(
                "SELECT COUNT(*) as cnt FROM why_verification_plans WHERE status = 'pending'"
            )["cnt"]
            executed = total - pending

            # By evidence type
            by_type_rows = db_query_all(
                "SELECT evidence_type, COUNT(*) as cnt FROM why_verification_plans GROUP BY evidence_type"
            )
            by_type = {r["evidence_type"]: r["cnt"] for r in by_type_rows} if by_type_rows else {}

            return {
                "total_plans": total,
                "pending": pending,
                "executed": executed,
                "by_evidence_type": by_type,
                "target_patterns": len(self.TARGET_PATTERNS),
                "evidence_strategies": len(self.EVIDENCE_STRATEGIES),
            }
        except Exception as e:
            return {"error": str(e)}

    # ============================================================
    # [V8.0-WHY] 3 NEW WHY MODES — type inference + data flow + security threat
    # ============================================================
    # TẠI SAO: v5.7 WHY engine chỉ có 2 layer (necessity + falsification) cho
    # fact-verification. v8.0 mở rộng WHY sang 3 miền mới:
    #   - type_inference_why: WHY hỏi "Tại sao biến này là type X?" — trace type
    #     flow để detect type bugs (TypeContractScanner + TypeMismatch).
    #   - data_flow_why: WHY hỏi "Tại sao data đi từ A→B?" — trace data path để
    #     detect logic bugs (user input → SQL, missing sanitize).
    #   - security_threat_why: WHY hỏi "Tại sao code này unsafe?" — security
    #     threat analysis (CWE category, attack vector, fix verify).
    #
    # Design principles (giữ nguyên DNA SCP):
    #   - OPT-IN via env var (default OFF — don't break existing flow).
    #     * SCP_WHY_TYPE_INFERENCE=1
    #     * SCP_WHY_DATA_FLOW=1
    #     * SCP_WHY_SECURITY_THREAT=1
    #   - LLM analysis reuse `_call_openrouter` from scp.autofix.llm_fix (same
    #     API key + rate limit budget as AutoFix LLM calls).
    #   - Defensive defaults: on any failure (no API key, parse error, LLM
    #     timeout, JSON invalid), return valid dict with conservative defaults
    #     (confidence=0.0, fix_direction="add type check" or similar).
    #   - All return dict with `why_question` + analysis fields + `confidence`
    #     + `llm_used` (bool — True nếu LLM thật được gọi).
    #   - WHY = chốt kiểm soát — không phải env var cơ khí.
    # ============================================================

    def _v80_why_llm_call(self, prompt: str, max_tokens: int = 600) -> str | None:
        """[V8.0-WHY] Helper: call LLM via gateway with task="why".

        [ROOT-FIX 44-A] Routes through gateway.chat_sync(task="why")
        → qwen2.5:7b (multilingual + factual accuracy). Previously
        called _call_openrouter directly (bypassed Ollama entirely).

        Returns None on any failure (import error, API key missing, HTTP error,
        timeout). WhyEngine callers fall back to defensive default dict.
        """
        try:
            from scp.llm_gateway import chat_sync
            # [ROOT-FIX 44-A] task="why" → qwen2.5:7b (multilingual + factual).
            response, _provider = chat_sync(prompt, task="why")
            return response
        except Exception as e:
            logger.debug(f"[V8.0-WHY] LLM call failed: {e}")
            return None

    def _v80_why_extract_json(self, response: str) -> dict | None:
        """[V8.0-WHY] Helper: extract first JSON object from LLM response.

        LLM may wrap JSON in markdown fences, search-replace markers, or
        explanatory text — use regex to extract the outermost {...} block.
        Handles one level of nested braces (e.g. {"a": {"b": 1}}).

        Returns None on parse failure (caller falls back to default dict).
        """
        if not response:
            return None
        try:
            # Match a JSON object — allow one level of nested braces.
            m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if not m:
                return None
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(
                f"[V8.0-WHY] JSON parse failed: {e} — response: {response[:200]}"
            )
            return None

    def _v80_normalize_cwe(self, cwe_id: str) -> str:
        """[V8.0-WHY] Helper: normalize CWE id to bare digits.

        Accepts "CWE-89", "cwe-89", "CWE89", "89" — all return "89".
        Falls back to "0" if no digits found.
        """
        if not cwe_id:
            return "0"
        m = re.search(r'(\d+)', cwe_id)
        return m.group(1) if m else "0"

    # ------------------------------------------------------------
    # [V8.0-WHY] Mode 1: type_inference_why
    # ------------------------------------------------------------
    def type_inference_why(
        self,
        var_name: str,
        expected_type: str,
        actual_type: str,
        context: str,
    ) -> dict:
        """[Task 9-B] Delegates to meta.why_v80_modes.type_inference_why for modularity.

        Original ~110 LOC body extracted to standalone function — same behavior.
        Takes engine (self) as first arg for _v80_why_llm_call access.
        """
        from scp.meta.why_v80_modes import type_inference_why as _v80_type_inference
        return _v80_type_inference(self, var_name, expected_type, actual_type, context)

    # ------------------------------------------------------------
    # [V8.0-WHY] Mode 2: data_flow_why
    # ------------------------------------------------------------
    def data_flow_why(
        self,
        source: str,
        sink: str,
        path: list[str],
        context: str,
    ) -> dict:
        """[Task 9-B] Delegates to meta.why_v80_modes.data_flow_why for modularity.

        Original ~125 LOC body extracted to standalone function — same behavior.
        Takes engine (self) as first arg for _v80_why_llm_call access.
        """
        from scp.meta.why_v80_modes import data_flow_why as _v80_data_flow
        return _v80_data_flow(self, source, sink, path, context)

    # ------------------------------------------------------------
    # [V8.0-WHY] Mode 3: security_threat_why
    # ------------------------------------------------------------
    def security_threat_why(
        self,
        code_pattern: str,
        cwe_id: str,
        context: str,
    ) -> dict:
        """[Task 9-B] Delegates to meta.why_v80_modes.security_threat_why for modularity.

        Original ~115 LOC body extracted to standalone function — same behavior.
        Takes engine (self) as first arg for _v80_why_llm_call access.
        """
        from scp.meta.why_v80_modes import security_threat_why as _v80_security_threat
        return _v80_security_threat(self, code_pattern, cwe_id, context)


# ============================================================
# MAIN
# ============================================================
# [Task 10-C] WIRED: main() extracted to why_engine_cli.py (Task 8-A).
# WhyEngine module giảm 60 LOC — CLI logic delegated.
def main():
    """[Task 8-A] CLI delegate — implementation in scp.meta.why_engine_cli."""
    from scp.meta.why_engine_cli import main as _cli_main
    return _cli_main()


if __name__ == "__main__":
    main()
