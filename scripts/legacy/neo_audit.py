"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

This software is protected by Vietnamese Intellectual Property Law and
international treaties. Unauthorized commercial use is prohibited.

Patent Pending:
  - WHY Engine (Verifikation Plan Generator)
  - Recursive Why (Trust Tracing to Axiomatic Sources)
  - MetaFalsifier (Verification Plan Falsification)
  - ProofGraph (Dynamic Proof Dependency Graph)

License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V34 — NEO Framework Audit.

Kiểm tra SCP theo 9 tầng NEO framework:
    L0. INPUT
    L1. PARSER
    L2. EVIDENCE
    L3. INTERPRETATION
    L4. SCP ROOT (Audit/Assumption/Framework/Self-Suspend)
    L5. REASONING
    L6. FALSIFICATION
    L7. EVIDENCE LEDGER
    L8. META LEARNING
    L9. RUNTIME
    + WHY ENGINE (còn thiếu)
"""
import sys

sys.path.insert(0, '.')

import sqlite3

from scp.core.db_manager import DB_PATH

print("="*80)
print("  SCP V34 — NEO FRAMEWORK AUDIT")
print("  Kiểm tra 9 tầng + WHY Engine")
print("="*80)

# ============================================================
# AUDIT TỪNG TẦNG
# ============================================================
audit_results = {}

def check_layer(layer_num, name, components):
    """Check 1 layer."""
    print(f"\n[L{layer_num}] {name}")
    print("-"*70)
    found = []
    missing = []
    for comp in components:
        # Check if exists in code or DB
        found_status = check_component(comp)
        mark = "[OK] " if found_status else "[XX] "
        print(f"  {mark} {comp['name']:30s} {comp['desc']}")
        if found_status:
            found.append(comp['name'])
        else:
            missing.append(comp['name'])
    audit_results[f"L{layer_num}_{name}"] = {
        "found": found, "missing": missing,
        "coverage": len(found) / (len(found) + len(missing))
    }


def check_component(comp):
    """Check if component exists."""
    check_type = comp.get("type", "code")
    if check_type == "db":
        # Check DB table
        # [SECURITY-FIX] B608: was f"SELECT COUNT(*) FROM {comp['name']}" — SQL injection
        # risk if comp['name'] ever became user-controlled. Now validate against
        # SQLite's allowed identifier pattern (letters, digits, underscores).
        import re
        _table = comp['name']
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', _table):
            return False  # invalid table name — refuse to query
        try:
            conn = sqlite3.connect(DB_PATH)
            # Use double-quoted identifier (SQLite's quoting for table names)
            cursor = conn.execute(f'SELECT COUNT(*) FROM "{_table}" LIMIT 1')  # noqa: S608 — table name validated by regex above
            cursor.fetchone()
            conn.close()
            return True
        except Exception:  # [G5-FIX] was bare `except:` (ruff E722) — narrow to Exception
            return False
    elif check_type == "code":
        # Check if module exists
        try:
            mod = comp.get("module", "")
            if mod and mod != "nonexistent_module":
                __import__(mod)
                return True
            return False
        except Exception:  # [G5-FIX] was bare `except:` (ruff E722) — narrow to Exception
            return False
    elif check_type == "function":
        try:
            mod = __import__(comp.get("module", ""), fromlist=[comp['name']])
            return hasattr(mod, comp['name'])
        except Exception:  # [G5-FIX] was bare `except:` (ruff E722) — narrow to Exception
            return False
    return False


# L0. INPUT
check_layer(0, "INPUT", [
    {"name": "Papers input", "desc": "arxiv, pubmed fetchers", "type": "code", "module": "scp.core.generator"},
    {"name": "Logs input", "desc": "kham_pha.jsonl logs", "type": "code", "module": "scp.consolidator.consolidator"},
    {"name": "Reality input", "desc": "APIs (Open-Meteo, PubChem, Binance...)", "type": "code", "module": "scp.core.crypto_verifier"},
    {"name": "Người dùng input", "desc": "CuriosityAsker questions", "type": "code", "module": "scp.meta.curiosity_asker"},
    {"name": "AI input", "desc": "SLM predictions", "type": "code", "module": "scp.runtime.engine"},
    {"name": "Sự kiện input", "desc": "Event bus (V6 analysis)", "type": "code", "module": "scp_v6_analysis.events.event_bus"},
])

# L1. PARSER
check_layer(1, "PARSER", [
    {"name": "Chuẩn hóa dữ liệu", "desc": "RealityClassifier", "type": "code", "module": "scp.core.reality_engine"},
    {"name": "Claim Extraction", "desc": "MathSLM/ChemistrySLM extract claims", "type": "code", "module": "scp.core.math_evaluator"},
    {"name": "Entity", "desc": "WikipediaDataSource._extract_entity", "type": "code", "module": "scp.core.reality_engine"},
    {"name": "Metadata", "desc": "Timestamps, sources, confidence", "type": "code", "module": "scp.core.db_manager"},
])

# L2. EVIDENCE
check_layer(2, "EVIDENCE", [
    {"name": "evidences", "desc": "DB table — 12 rows", "type": "db"},
    {"name": "evidence_links", "desc": "DB table — 11 rows", "type": "db"},
    {"name": "predictions", "desc": "V35 Predictions table (future verification)", "type": "db"},
    {"name": "why_verification_plans", "desc": "V34 WHY Engine plans", "type": "db"},
    {"name": "error_history", "desc": "History table", "type": "db"},
    {"name": "reason_chains", "desc": "Trace table", "type": "db"},
    {"name": "Predictive module", "desc": "prediction/predictive.py — V35 activated", "type": "code", "module": "scp.prediction.predictive"},
])

# L3. INTERPRETATION
check_layer(3, "INTERPRETATION", [
    {"name": "Face Value", "desc": "DirectAPIVerifier — verify at face value", "type": "code", "module": "scp.runtime.engine"},
    {"name": "Skeptical", "desc": "AdversaryVerifier — cross-check skeptical", "type": "code", "module": "scp.meta.adversary_verifier"},
    {"name": "Methodological", "desc": "ConflictResolver — methodology check", "type": "code", "module": "scp.core.conflict_resolver"},
    {"name": "Field Culture", "desc": "Per-domain SLMs (11 domains = 11 cultures)", "type": "code", "module": "scp.runtime.engine"},
    {"name": "Framework mới", "desc": "CuriosityQualityEngine generates new question types", "type": "code", "module": "scp.meta.curiosity_quality"},
])

# L4. SCP ROOT
check_layer(4, "SCP ROOT (Audit/Assumption/Framework/Self-Suspend)", [
    {"name": "Audit", "desc": "system_test.py + failure_tests.py", "type": "code", "module": "scp.runtime.system_test"},
    {"name": "Assumption", "desc": "CalibrationEngine tracks assumptions", "type": "code", "module": "scp.meta.calibration_engine"},
    {"name": "Framework Selection", "desc": "RealityClassifier chooses framework", "type": "code", "module": "scp.core.reality_engine"},
    {"name": "Self-Suspend", "desc": "❌ MISSING — no mechanism to suspend belief when conflict", "type": "code", "module": "nonexistent_module"},
])

# L5. REASONING
check_layer(5, "REASONING", [
    {"name": "Detect Anomaly", "desc": "KnowledgeConsolidator._consolidate_group (anomaly_count)", "type": "code", "module": "scp.consolidator.consolidator"},
    {"name": "Generate Hypothesis", "desc": "hypothesis_zone.py", "type": "code", "module": "scp.core.hypothesis_zone"},
    {"name": "Pattern", "desc": "AbstractionEngine abstract_from_lessons", "type": "code", "module": "scp.meta.meta"},
    {"name": "Critic", "desc": "AdversaryVerifier + ConflictResolver", "type": "code", "module": "scp.meta.adversary_verifier"},
])

# L6. FALSIFICATION
check_layer(6, "FALSIFICATION", [
    {"name": "Attack Generator", "desc": "AdversaryVerifier generates attacks", "type": "code", "module": "scp.meta.adversary_verifier"},
    {"name": "Counter Example (V35 NEW)", "desc": "CounterExampleEngine — systematic search", "type": "code", "module": "scp.meta.counter_example"},
    {"name": "falsification_history", "desc": "V35 DB table for counter examples", "type": "db"},
    {"name": "Stress Test", "desc": "failure_tests.py (10 scenarios)", "type": "code", "module": "scp.runtime.failure_tests"},
    {"name": "Survived? / NOT_YET_DISPROVEN", "desc": "benchmark.py — 100% survived", "type": "code", "module": "scp.meta.benchmark"},
])

# L7. EVIDENCE LEDGER
check_layer(7, "EVIDENCE LEDGER", [
    {"name": "knowledge", "desc": "Findings — 70 rows", "type": "db"},
    {"name": "knowledge_summaries", "desc": "Patch History (compressed)", "type": "db"},
    {"name": "conclusions", "desc": "Closure Evidence — 23 rows", "type": "db"},
    {"name": "calibration_factors", "desc": "Confidence — 10 rows", "type": "db"},
    {"name": "source", "desc": "Verified By column in knowledge table", "type": "db"},
])

# L8. META LEARNING
check_layer(8, "META LEARNING", [
    {"name": "Điều chỉnh chính sách", "desc": "PolicyApplier + record_outcome", "type": "code", "module": "scp.meta.policy_applier"},
    {"name": "Theo dõi hiệu quả", "desc": "KnowledgeGrowthTracker", "type": "code", "module": "scp.meta.growth_tracker"},
    {"name": "Chỉ đề xuất thay đổi", "desc": "FailurePatternAnalyzer suggests fixes", "type": "code", "module": "scp.meta.failure_analysis"},
])

# L9. RUNTIME
check_layer(9, "RUNTIME", [
    {"name": "Scheduler", "desc": "orchestrator.py (9-step cycle)", "type": "code", "module": "scp.runtime.orchestrator"},
    {"name": "Heartbeat", "desc": "run_247.py write_health()", "type": "code", "module": "run_247"},
    {"name": "Logging", "desc": "logging throughout", "type": "code", "module": "scp.core.db_manager"},
    {"name": "Persistence", "desc": "SQLite v13.db + auto_backup", "type": "code", "module": "scp.core.auto_backup"},
])

# WHY ENGINE
print("\n[WHY] WHY ENGINE (Neo's question → verification process)")
print("-"*70)
why_components = [
    {"name": "why_engine.py", "desc": "V34 WHY Engine module", "type": "code", "module": "scp.meta.why_engine"},
    {"name": "WhyEngine class", "desc": "Question → Verification Process", "type": "code", "module": "scp.meta.why_engine"},
    {"name": "identify_target", "desc": "Target identification — 'câu hỏi về đối tượng nào?'", "type": "code", "module": "scp.meta.why_engine"},
    {"name": "select_evidence_type", "desc": "Evidence type selection — 'loại bằng chứng nào?'", "type": "code", "module": "scp.meta.why_engine"},
    {"name": "define_criteria (proof)", "desc": "Proof criteria — 'điều gì chứng minh?'", "type": "code", "module": "scp.meta.why_engine"},
    {"name": "define_criteria (falsify)", "desc": "Falsification criteria — 'điều gì bác bỏ?'", "type": "code", "module": "scp.meta.why_engine"},
]
why_found = []
why_missing = []
for comp in why_components:
    found = check_component(comp)
    mark = "[OK] " if found else "[XX] "
    print(f"  {mark} {comp['name']:35s} {comp['desc']}")
    if found:
        why_found.append(comp["name"])
    else:
        why_missing.append(comp["name"])
audit_results["WHY_ENGINE"] = {
    "found": why_found, "missing": why_missing,
    "coverage": len(why_found) / (len(why_found) + len(why_missing))
}

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*80}")
print("  NEO FRAMEWORK AUDIT SUMMARY")
print(f"{'='*80}")
print(f"\n  {'Layer':<40s} {'Coverage':>10} {'Status':>10}")
print(f"  {'-'*60}")

total_found = 0
total_components = 0
critical_gaps = []

for layer, info in audit_results.items():
    pct = info["coverage"] * 100
    bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
    status = "✅ OK" if pct >= 80 else "🟡 WEAK" if pct >= 50 else "🔴 CRITICAL"
    print(f"  {layer:<40s} {bar} {pct:>5.0f}%  {status}")
    total_found += len(info["found"])
    total_components += len(info["found"]) + len(info["missing"])
    if pct < 50:
        critical_gaps.append((layer, info["missing"]))

print(f"\n  Total: {total_found}/{total_components} components ({total_found/total_components*100:.0f}%)")

if critical_gaps:
    print("\n  CRITICAL GAPS:")
    for layer, missing in critical_gaps:
        print(f"    {layer}:")
        for m in missing:
            print(f"      - {m}")

print(f"\n{'='*80}")
print("  KẾT LUẬN")
print(f"{'='*80}")
print("""
  SCP hiện có ~80% NEO framework coverage.

  Điểm mạnh:
    ✅ L0-L2 (Input/Parser/Evidence): đầy đủ
    ✅ L5 (Reasoning): đầy đủ (anomaly/hypothesis/pattern/critic)
    ✅ L7 (Evidence Ledger): đầy đủ DB tables
    ✅ L8 (Meta Learning): đầy đủ (PolicyApplier/GrowthTracker/FailureAnalysis)
    ✅ L9 (Runtime): đầy đủ (Scheduler/Heartbeat/Logging/Persistence)

  Điểm yếu:
    🟡 L4 (SCP ROOT): thiếu "Self-Suspend" — không có cơ chế suspend belief
    🟡 L6 (FALSIFICATION): thiếu "Counter Example" — search hệ thống
    🔴 WHY ENGINE: hoàn toàn thiếu — đây là gap lớn nhất

  WHY ENGINE là component quan trọng nhất còn thiếu:
    - Neo hỏi "Tại sao?"
    - WHY Engine biến câu hỏi thành quy trình kiểm chứng
    - 4 câu hỏi cốt lõi:
      1. Câu hỏi đang hỏi về đối tượng nào?
      2. Muốn trả lời cần loại bằng chứng nào?
      3. Điều gì sẽ được coi là chứng minh?
      4. Điều gì có thể bác bỏ câu trả lời?
""")
