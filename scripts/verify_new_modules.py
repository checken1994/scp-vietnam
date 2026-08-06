#!/usr/bin/env python3
"""
SCP V98 — Verify New Modules
=============================
Script smoke test — chứng minh 3 module mới chạy được.

Cách chạy:
    python scripts/verify_new_modules.py

Output: in ra PASS/FAIL cho mỗi test, kèm giải thích.

Module được test:
1. scp.knowledge.source_reputation  — Dynamic reputation scoring
2. scp.knowledge.source_watchlist   — Watchlist + block + recovery
3. scp.security.response_monitor    — Behavioral anomaly detection

Mỗi test được thiết kế:
- Tự chứa (không cần LLM API, không cần SCP service chạy)
- In ra input + expected output + actual output
- PASS/FAIL rõ ràng
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

# Add SCP root to path
SCP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCP_ROOT)

# ============================================================
# Test utilities
# ============================================================
class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def check(self, name: str, condition: bool, detail: str = ""):
        status = "✅ PASS" if condition else "❌ FAIL"
        self.results.append((name, status, detail))
        if condition:
            self.passed += 1
        else:
            self.failed += 1
        print(f"  {status} | {name}" + (f" — {detail}" if detail else ""))

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"SUMMARY: {self.passed}/{total} tests passed, {self.failed} failed")
        print(f"{'='*60}")
        return self.failed == 0


# ============================================================
# TEST 1: Source Reputation
# ============================================================
def test_source_reputation(results: TestResult):
    print("\n" + "="*60)
    print("TEST 1: Source Reputation System")
    print("="*60)

    from scp.knowledge.source_reputation import (
        REPUTATION_BLOCK_THRESHOLD,
        REPUTATION_INITIAL,
        REPUTATION_PENALTY_INVALIDATE,
        REPUTATION_WATCHLIST_THRESHOLD,
        TIER_WEIGHTS,
        ReputationStore,
        SourceReputation,
    )

    # Use temp DB
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    os.remove(db_path)  # Remove so we start fresh

    try:
        store = ReputationStore(db_path=db_path)

        # Test 1.1: New source has reputation = 1.0
        print("\n  [1.1] New source 'wikipedia' should start with reputation=1.0")
        rec = store.get("wikipedia")
        results.check(
            "1.1a get() returns None for new source",
            rec is None,
            f"got {rec}",
        )

        # Test 1.2: Effective weight for new tier-2 source
        print("\n  [1.2] Effective weight for new tier-2 source (wikipedia)")
        weight = store.get_effective_weight("wikipedia", tier=2)
        expected = TIER_WEIGHTS[2] * REPUTATION_INITIAL  # 0.85 * 1.0 = 0.85
        results.check(
            "1.2a effective_weight ≈ 0.85 for new tier-2 source",
            abs(weight - expected) < 0.01,
            f"got {weight:.4f}, expected {expected:.4f}",
        )

        # Test 1.3: After commit, facts_contributed increments
        print("\n  [1.3] After 5 commits, facts_contributed = 5")
        for _ in range(5):
            store.on_fact_committed("wikipedia", tier=2)
        rec = store.get("wikipedia")
        results.check(
            "1.3a facts_contributed = 5",
            rec.facts_contributed == 5,
            f"got {rec.facts_contributed}",
        )

        # Test 1.4: After invalidate, reputation drops
        print("\n  [1.4] After 1 invalidation, reputation *= 0.9")
        store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        expected_rep = REPUTATION_INITIAL * REPUTATION_PENALTY_INVALIDATE  # 0.9
        results.check(
            "1.4a reputation ≈ 0.9 after 1 invalidation",
            abs(rec.reputation_score - expected_rep) < 0.01,
            f"got {rec.reputation_score:.4f}, expected {expected_rep:.4f}",
        )
        results.check(
            "1.4b status still 'active' (rep > 0.3)",
            rec.status == "active",
            f"got status={rec.status}",
        )

        # Test 1.5: After many invalidations → watchlist
        print("\n  [1.5] After 15 more invalidations (total 16) → watchlist (rep < 0.3)")
        for _ in range(15):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        results.check(
            "1.5a reputation < 0.3",
            rec.reputation_score < REPUTATION_WATCHLIST_THRESHOLD,
            f"got {rec.reputation_score:.4f}",
        )
        results.check(
            "1.5b status = 'watchlist'",
            rec.status == "watchlist",
            f"got status={rec.status}",
        )

        # Test 1.6: Effective weight in watchlist is reduced
        print("\n  [1.6] Effective weight in watchlist is reduced")
        weight = store.get_effective_weight("wikipedia", tier=2)
        results.check(
            "1.6a effective_weight < 0.85 * 0.3",
            weight < TIER_WEIGHTS[2] * REPUTATION_WATCHLIST_THRESHOLD,
            f"got {weight:.4f}",
        )

        # Test 1.7: Independent verification boosts reputation
        print("\n  [1.7] After 5 verifications, reputation *= 1.05^5")
        rep_before = store.get("wikipedia").reputation_score
        for _ in range(5):
            store.on_fact_independently_verified("wikipedia")
        rec = store.get("wikipedia")
        results.check(
            "1.7a reputation increased",
            rec.reputation_score > rep_before,
            f"before={rep_before:.4f}, after={rec.reputation_score:.4f}",
        )

        # Test 1.8: Force block
        print("\n  [1.8] Force block 'malicious.com'")
        store.on_fact_committed("malicious.com", tier=4)
        store.force_block("malicious.com", reason="detected_poisoning")
        rec = store.get("malicious.com")
        results.check(
            "1.8a status = 'blocked'",
            rec.status == "blocked",
            f"got status={rec.status}",
        )
        weight = store.get_effective_weight("malicious.com", tier=4)
        results.check(
            "1.8b effective_weight = 0.0 for blocked source",
            weight == 0.0,
            f"got {weight}",
        )

        # Test 1.9: Force recover
        print("\n  [1.9] Force recover 'malicious.com'")
        store.force_recover("malicious.com")
        rec = store.get("malicious.com")
        results.check(
            "1.9a status = 'active' after recover",
            rec.status == "active",
            f"got status={rec.status}",
        )
        results.check(
            "1.9b reputation = 0.5 after recover",
            abs(rec.reputation_score - 0.5) < 0.01,
            f"got {rec.reputation_score}",
        )

        # Test 1.10: Stats
        print("\n  [1.10] Stats summary")
        stats = store.stats()
        print(f"    stats = {stats}")
        results.check(
            "1.10a stats has all fields",
            all(k in stats for k in ["total_sources", "active", "watchlist", "blocked", "avg_reputation"]),
            f"got keys: {list(stats.keys())}",
        )

        # Test 1.11: Persistence — close & reopen
        print("\n  [1.11] Persistence — close & reopen DB")
        del store
        store2 = ReputationStore(db_path=db_path)
        rec = store2.get("wikipedia")
        results.check(
            "1.11a wikipedia still in DB after reopen",
            rec is not None and rec.facts_contributed == 5,
            f"got {rec}",
        )

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


# ============================================================
# TEST 2: Source Watchlist
# ============================================================
def test_source_watchlist(results: TestResult):
    print("\n" + "="*60)
    print("TEST 2: Source Watchlist")
    print("="*60)

    from scp.knowledge.source_reputation import ReputationStore
    from scp.knowledge.source_watchlist import SourceWatchlist, check_source_before_ingestion

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    os.remove(db_path)

    try:
        store = ReputationStore(db_path=db_path)
        wl = SourceWatchlist(store)

        # Test 2.1: New source → trusted
        print("\n  [2.1] New source 'wikidata' → trusted (commit)")
        d = wl.ingestion_decision("wikidata", tier=2)
        results.check(
            "2.1a action = 'commit'",
            d["action"] == "commit",
            f"got {d['action']}",
        )
        results.check(
            "2.1b require_verification = False",
            d["require_verification"] is False,
            f"got {d['require_verification']}",
        )

        # Test 2.2: Push to watchlist
        print("\n  [2.2] Push 'wikidata' to watchlist via 16 invalidations")
        for _ in range(5):
            store.on_fact_committed("wikidata", tier=2)
        for _ in range(16):
            store.on_fact_invalidated("wikidata")
        d = wl.ingestion_decision("wikidata", tier=2)
        results.check(
            "2.2a action = 'suspect'",
            d["action"] == "suspect",
            f"got {d['action']}",
        )
        results.check(
            "2.2b require_verification = True (needs 2+ sources)",
            d["require_verification"] is True,
            f"got {d['require_verification']}",
        )

        # Test 2.3: is_suspect / is_blocked helpers
        print("\n  [2.3] Helper methods is_suspect / is_blocked / is_trusted")
        results.check(
            "2.3a is_suspect('wikidata') = True",
            wl.is_suspect("wikidata") is True,
        )
        results.check(
            "2.3b is_blocked('wikidata') = False",
            wl.is_blocked("wikidata") is False,
        )
        results.check(
            "2.3c is_trusted('wikidata') = False",
            wl.is_trusted("wikidata") is False,
        )

        # Test 2.4: Block
        print("\n  [2.4] Block 'evil.com' manually")
        store.on_fact_committed("evil.com", tier=4)
        wl.force_block("evil.com", reason="test")
        d = wl.ingestion_decision("evil.com", tier=4)
        results.check(
            "2.4a action = 'block'",
            d["action"] == "block",
            f"got {d['action']}",
        )
        results.check(
            "2.4b effective_weight = 0.0",
            d["effective_weight"] == 0.0,
        )

        # Test 2.5: List watchlist + blocked
        print("\n  [2.5] List watchlist + blocked")
        wl_entries = wl.list_watchlist()
        results.check(
            "2.5a watchlist contains wikidata",
            any(e.source == "wikidata" for e in wl_entries),
            f"got {[e.source for e in wl_entries]}",
        )
        blocked = wl.list_blocked()
        results.check(
            "2.5b blocked contains evil.com",
            any(r.source == "evil.com" for r in blocked),
            f"got {[r.source for r in blocked]}",
        )

        # Test 2.6: Helper function check_source_before_ingestion
        print("\n  [2.6] Helper check_source_before_ingestion()")
        should_commit, reason, weight = check_source_before_ingestion("evil.com", 4, wl)
        results.check(
            "2.6a evil.com → should_commit = False",
            should_commit is False,
            f"got {should_commit}",
        )

        should_commit, reason, weight = check_source_before_ingestion("fresh-source.com", 2, wl)
        results.check(
            "2.6b fresh-source.com → should_commit = True",
            should_commit is True,
            f"got {should_commit}",
        )

        # Test 2.7: Recovery
        print("\n  [2.7] Force recover 'wikidata' from watchlist")
        wl.force_recover("wikidata")
        d = wl.ingestion_decision("wikidata", tier=2)
        results.check(
            "2.7a action = 'commit' after recover",
            d["action"] == "commit",
            f"got {d['action']}",
        )

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


# ============================================================
# TEST 3: Response Monitor
# ============================================================
def test_response_monitor(results: TestResult):
    print("\n" + "="*60)
    print("TEST 3: Response Monitor (Behavioral Anomaly)")
    print("="*60)

    from scp.security.response_monitor import (
        LENGTH_OUTLIER_THRESHOLD,
        SENSITIVE_KEYWORDS,
        AnomalyResult,
        ResponseMetrics,
        ResponseMonitor,
    )

    monitor = ResponseMonitor(window_size=100, sigma_threshold=3.0)

    # Test 3.1: Metrics extraction
    print("\n  [3.1] Metrics extraction")
    test_str = "Hà Nội là thủ đô của Việt Nam."
    metrics = ResponseMetrics.extract(test_str, latency_ms=720)
    results.check(
        f"3.1a length_chars = {len(test_str)}",
        metrics.length_chars == len(test_str),
        f"got {metrics.length_chars}, expected {len(test_str)}",
    )
    results.check(
        "3.1b is_refusal = False",
        metrics.is_refusal is False,
    )
    results.check(
        "3.1c sensitive_keyword_count = 0",
        metrics.sensitive_keyword_count == 0,
    )
    results.check(
        "3.1d entropy > 0",
        metrics.entropy > 0,
        f"got {metrics.entropy:.3f}",
    )

    # Test 3.2: Refusal detection
    print("\n  [3.2] Refusal pattern detection")
    metrics = ResponseMetrics.extract("I can't help with that. As an AI, I'm not able to assist.")
    results.check(
        "3.2a is_refusal = True",
        metrics.is_refusal is True,
    )

    # Test 3.3: Sensitive keyword detection
    print("\n  [3.3] Sensitive keyword detection")
    metrics = ResponseMetrics.extract("To create a bomb, use explosive materials. Here's methamphetamine recipe.")
    results.check(
        "3.3a sensitive_keyword_count >= 3",
        metrics.sensitive_keyword_count >= 3,
        f"got {metrics.sensitive_keyword_count}",
    )

    # Test 3.4: Warm up baseline
    print("\n  [3.4] Warm up baseline with 50 normal responses")
    normal_responses = [
        "Hà Nội là thủ đô của Việt Nam.",
        "Tháp Eiffel cao 330 mét.",
        "Nước Nga có diện tích 17.1 triệu km vuông.",
        "Thái Bình Dương là đại dương lớn nhất.",
        "Tốc độ ánh sáng khoảng 300,000 km/s.",
    ]
    for i in range(50):
        monitor.observe(
            prompt=f"q{i}",
            response=normal_responses[i % len(normal_responses)],
            latency_ms=700 + (i % 10) * 20,
        )
    stats = monitor.stats()
    results.check(
        "3.4a 50 observations collected",
        stats["observations"] == 50,
        f"got {stats['observations']}",
    )
    results.check(
        "3.4b baseline_length_mean > 0",
        stats["baseline_length_mean"] > 0,
    )

    # Test 3.5: Normal response should NOT flag
    print("\n  [3.5] Normal response should NOT be flagged")
    result = monitor.observe(prompt="q", response="Đà Nẵng là thành phố lớn ở miền Trung.", latency_ms=720)
    results.check(
        "3.5a is_anomalous = False",
        result.is_anomalous is False,
        f"reasons: {result.reasons}",
    )

    # Test 3.6: Very long response → flag
    print("\n  [3.6] Very long response (5000+ chars) → flag as anomaly")
    long_resp = "A" * 5500
    result = monitor.observe(prompt="q", response=long_resp, latency_ms=800)
    results.check(
        "3.6a is_anomalous = True",
        result.is_anomalous is True,
        f"reasons: {result.reasons}",
    )
    results.check(
        "3.6b severity = 'high'",
        result.severity == "high",
        f"got {result.severity}",
    )

    # Test 3.7: Sensitive keywords → flag
    print("\n  [3.7] Response with multiple sensitive keywords → flag")
    sensitive_resp = "To make a bomb, you need explosive materials. Here's methamphetamine recipe with credit card info."
    result = monitor.observe(prompt="q", response=sensitive_resp, latency_ms=800)
    results.check(
        "3.7a is_anomalous = True",
        result.is_anomalous is True,
        f"reasons: {result.reasons}",
    )
    results.check(
        "3.7b severity = 'high'",
        result.severity in ("high", "critical"),
        f"got {result.severity}",
    )

    # Test 3.8: High-entropy (encoded leak) → flag
    # NOTE: Reset baseline first to avoid contamination from 3.6/3.7 outliers
    print("\n  [3.8] High-entropy response (base64-like) → flag")
    monitor.reset()
    # Re-warm with normal responses
    normal_responses = [
        "Hà Nội là thủ đô của Việt Nam.",
        "Tháp Eiffel cao 330 mét.",
        "Nước Nga có diện tích 17.1 triệu km vuông.",
        "Thái Bình Dương là đại dương lớn nhất.",
        "Tốc độ ánh sáng khoảng 300,000 km/s.",
    ]
    for i in range(50):
        monitor.observe(prompt=f"q{i}", response=normal_responses[i % len(normal_responses)], latency_ms=720)
    import base64
    encoded = base64.b64encode(bytes(range(256)) * 4).decode()  # ~1368 chars, high entropy
    result = monitor.observe(prompt="q", response=encoded, latency_ms=800)
    print(f"    entropy={result.metrics.entropy:.3f}, baseline_entropy_mean={monitor.stats().get('baseline_length_mean', 0):.2f}")
    results.check(
        "3.8a is_anomalous = True (high entropy detected)",
        result.is_anomalous is True,
        f"reasons: {result.reasons}",
    )

    # Test 3.9: Stats
    print("\n  [3.9] Stats summary")
    stats = monitor.stats()
    print(f"    stats = {stats}")
    results.check(
        "3.9a stats has window_size",
        "window_size" in stats and stats["window_size"] == 100,
    )

    # Test 3.10: Reset
    print("\n  [3.10] Reset baseline")
    monitor.reset()
    stats = monitor.stats()
    results.check(
        "3.10a observations = 0 after reset",
        stats["observations"] == 0,
        f"got {stats['observations']}",
    )


# ============================================================
# TEST 4: Integration — end-to-end scenario
# ============================================================
def test_integration_scenario(results: TestResult):
    print("\n" + "="*60)
    print("TEST 4: Integration Scenario — Upstream Poisoning Attack")
    print("="*60)
    print("  Scenario: Attacker vandalizes Wikipedia → SCP detects → blocks source")

    from scp.knowledge.source_reputation import ReputationStore
    from scp.knowledge.source_watchlist import SourceWatchlist

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    os.remove(db_path)

    try:
        store = ReputationStore(db_path=db_path)
        wl = SourceWatchlist(store)

        # Step 1: Normal operation — Wikipedia is trusted
        print("\n  [Step 1] Normal operation — Wikipedia contributes 50 facts")
        for _ in range(50):
            store.on_fact_committed("wikipedia", tier=2)
        rec = store.get("wikipedia")
        results.check(
            "Step 1: wikipedia reputation = 1.0 (active)",
            abs(rec.reputation_score - 1.0) < 0.01 and rec.status == "active",
            f"rep={rec.reputation_score}, status={rec.status}",
        )

        # Step 2: Attacker poisons Wikipedia → 5 facts invalidated
        print("\n  [Step 2] Attacker poisons Wikipedia → 5 facts invalidated")
        for _ in range(5):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        results.check(
            "Step 2: reputation drops to ~0.59",
            0.55 < rec.reputation_score < 0.65,
            f"rep={rec.reputation_score:.4f}",
        )
        results.check(
            "Step 2: still 'active' (not yet in watchlist)",
            rec.status == "active",
        )

        # Step 3: More poisoning → watchlist
        print("\n  [Step 3] Continued poisoning → watchlist")
        for _ in range(8):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        results.check(
            "Step 3: reputation < 0.3 (watchlist)",
            rec.reputation_score < 0.3 and rec.status == "watchlist",
            f"rep={rec.reputation_score:.4f}, status={rec.status}",
        )

        # Step 4: New facts from Wikipedia now require verification
        print("\n  [Step 4] New facts from Wikipedia require 2+ sources")
        d = wl.ingestion_decision("wikipedia", tier=2)
        results.check(
            "Step 4: action='suspect', require_verification=True",
            d["action"] == "suspect" and d["require_verification"] is True,
            f"action={d['action']}, require_verification={d['require_verification']}",
        )

        # Step 5: Continued poisoning → block
        print("\n  [Step 5] Continued poisoning → permanent block")
        for _ in range(15):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        results.check(
            "Step 5: wikipedia blocked",
            rec.status == "blocked",
            f"rep={rec.reputation_score:.4f}, status={rec.status}",
        )

        # Step 6: Subsequent facts from Wikipedia rejected
        print("\n  [Step 6] Subsequent facts from Wikipedia rejected at ingestion")
        d = wl.ingestion_decision("wikipedia", tier=2)
        results.check(
            "Step 6: action='block', weight=0.0",
            d["action"] == "block" and d["effective_weight"] == 0.0,
            f"action={d['action']}, weight={d['effective_weight']}",
        )

        print("\n  ✓ Integration scenario passed — upstream poisoning contained")

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("SCP V98 — Verify New Modules")
    print("=" * 60)
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"SCP_ROOT: {SCP_ROOT}")
    print(f"Python: {sys.version.split()[0]}")

    results = TestResult()

    try:
        test_source_reputation(results)
        test_source_watchlist(results)
        test_response_monitor(results)
        test_integration_scenario(results)
    except Exception as e:
        import traceback
        print(f"\n❌ ERROR during testing: {type(e).__name__}: {e}")
        traceback.print_exc()
        results.failed += 1

    success = results.summary()

    print("\nNext steps:")
    print("  - If all PASS: modules are ready. Add hooks into KnowledgeIO and api_server.")
    print("  - If any FAIL: see traceback above, fix the issue, re-run this script.")
    print("  - Run benchmarks: python scripts/benchmarks/run_all_benchmarks.py --quick")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
