"""
Pytest tests for Source Reputation + Watchlist modules.
Run: pytest tests/test_source_reputation.py -v
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add SCP root to path
SCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCP_ROOT))

from scp.knowledge.source_reputation import (
    REPUTATION_BLOCK_THRESHOLD,
    REPUTATION_INITIAL,
    REPUTATION_WATCHLIST_THRESHOLD,
    TIER_WEIGHTS,
    ReputationStore,
    SourceReputation,
)
from scp.knowledge.source_watchlist import SourceWatchlist, check_source_before_ingestion


@pytest.fixture
def store():
    """Fresh ReputationStore for each test."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    os.remove(db_path)
    s = ReputationStore(db_path=db_path)
    yield s
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def watchlist(store):
    return SourceWatchlist(store)


# ============= SourceReputation tests =============

class TestReputationStore:
    def test_new_source_has_default_reputation(self, store):
        weight = store.get_effective_weight("wikipedia", tier=2)
        assert abs(weight - TIER_WEIGHTS[2] * REPUTATION_INITIAL) < 0.01

    def test_commit_increments_facts_contributed(self, store):
        for _ in range(5):
            store.on_fact_committed("wikipedia", tier=2)
        rec = store.get("wikipedia")
        assert rec.facts_contributed == 5

    def test_invalidate_decreases_reputation(self, store):
        store.on_fact_committed("wikipedia", tier=2)
        store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        assert rec.reputation_score < REPUTATION_INITIAL

    def test_watchlist_threshold(self, store):
        store.on_fact_committed("wikipedia", tier=2)
        # 0.9^12 ≈ 0.282 → below 0.3 threshold
        for _ in range(12):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        assert rec.status == "watchlist"
        assert rec.reputation_score < REPUTATION_WATCHLIST_THRESHOLD

    def test_block_threshold(self, store):
        store.on_fact_committed("wikipedia", tier=2)
        # 0.9^22 ≈ 0.098 → below 0.1 threshold
        for _ in range(22):
            store.on_fact_invalidated("wikipedia")
        rec = store.get("wikipedia")
        assert rec.status == "blocked"

    def test_blocked_source_has_zero_weight(self, store):
        store.force_block("evil.com", reason="test")
        weight = store.get_effective_weight("evil.com", tier=4)
        assert weight == 0.0

    def test_verification_boosts_reputation(self, store):
        store.on_fact_committed("wikipedia", tier=2)
        store.on_fact_invalidated("wikipedia")
        rep_before = store.get("wikipedia").reputation_score
        store.on_fact_independently_verified("wikipedia")
        rep_after = store.get("wikipedia").reputation_score
        assert rep_after > rep_before

    def test_reputation_cap_at_1(self, store):
        store.on_fact_committed("wikipedia", tier=2)
        for _ in range(20):
            store.on_fact_independently_verified("wikipedia")
        rec = store.get("wikipedia")
        assert rec.reputation_score <= 1.0

    def test_force_recover(self, store):
        store.on_fact_committed("evil.com", tier=4)
        store.force_block("evil.com")
        assert store.get("evil.com").status == "blocked"
        store.force_recover("evil.com")
        rec = store.get("evil.com")
        assert rec.status == "active"
        assert abs(rec.reputation_score - 0.5) < 0.01

    def test_persistence(self, store):
        db_path = store.db_path
        store.on_fact_committed("wikipedia", tier=2)
        del store
        store2 = ReputationStore(db_path=db_path)
        rec = store2.get("wikipedia")
        assert rec is not None
        assert rec.facts_contributed == 1

    def test_stats(self, store):
        store.on_fact_committed("a.com", tier=2)
        store.on_fact_committed("b.com", tier=2)
        store.force_block("c.com")
        stats = store.stats()
        assert stats["total_sources"] == 3
        assert stats["blocked"] == 1


# ============= SourceWatchlist tests =============

class TestSourceWatchlist:
    def test_new_source_is_trusted(self, watchlist):
        d = watchlist.ingestion_decision("wikipedia", tier=2)
        assert d["action"] == "commit"
        assert d["require_verification"] is False

    def test_suspect_source_requires_verification(self, store, watchlist):
        store.on_fact_committed("wikipedia", tier=2)
        for _ in range(12):
            store.on_fact_invalidated("wikipedia")
        d = watchlist.ingestion_decision("wikipedia", tier=2)
        assert d["action"] == "suspect"
        assert d["require_verification"] is True

    def test_blocked_source_rejected(self, store, watchlist):
        store.on_fact_committed("evil.com", tier=4)
        watchlist.force_block("evil.com")
        d = watchlist.ingestion_decision("evil.com", tier=4)
        assert d["action"] == "block"
        assert d["effective_weight"] == 0.0

    def test_helper_function(self, watchlist):
        should_commit, _, _ = check_source_before_ingestion("new.com", 2, watchlist)
        assert should_commit is True

        watchlist.force_block("evil.com")
        should_commit, _, _ = check_source_before_ingestion("evil.com", 2, watchlist)
        assert should_commit is False

    def test_is_suspect_is_blocked_is_trusted(self, store, watchlist):
        # [G5-FIX] Updated to match ROOT-FIX-8 defense-in-depth behavior:
        # Unknown sources are SUSPECT (not trusted) unless in well-known allowlist.
        # "new.com" is not well-known → is_trusted returns False (was True before fix)
        assert watchlist.is_trusted("new.com") is False  # unknown = suspect (safe default)
        assert watchlist.is_suspect("new.com") is False  # not in watchlist yet
        assert watchlist.is_blocked("new.com") is False

        # Well-known sources ARE trusted
        assert watchlist.is_trusted("wikipedia") is True
        assert watchlist.is_trusted("nasa") is True

        # Push to watchlist
        store.on_fact_committed("suspect.com", tier=2)
        for _ in range(12):
            store.on_fact_invalidated("suspect.com")
        assert watchlist.is_suspect("suspect.com") is True
        assert watchlist.is_trusted("suspect.com") is False

        # Block
        watchlist.force_block("blocked.com")
        assert watchlist.is_blocked("blocked.com") is True

    def test_list_watchlist_and_blocked(self, store, watchlist):
        store.on_fact_committed("suspect.com", tier=2)
        for _ in range(12):
            store.on_fact_invalidated("suspect.com")
        watchlist.force_block("evil.com")

        wl = watchlist.list_watchlist()
        assert any(e.source == "suspect.com" for e in wl)

        blocked = watchlist.list_blocked()
        assert any(r.source == "evil.com" for r in blocked)
