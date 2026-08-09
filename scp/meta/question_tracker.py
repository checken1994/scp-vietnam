"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
[V45] Question Tracker — phân loại câu hỏi: NEW / REPEAT / INTERNAL.

3 Loại câu hỏi:
  - NEW      : Câu hỏi mới (chưa từng thấy trong history)
  - REPEAT   : Câu hỏi đã thấy trước đó (cache hit có thể)
  - INTERNAL : Câu hỏi từ SCP tự sinh ra (generator, reverify, prediction check)

Tracked trong DB table question_log với:
  - question_hash (SHA256 của normalized question)
  - first_seen_ts, last_seen_ts, times_seen
  - source: generator / external / reverify / prediction / benchmark
  - verdict: PASS / FAIL / UNKNOWN / PARTIAL / CONFLICT
"""
import hashlib
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("scp.qtracker")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from scp.core.db_manager import db_exec, db_query_all, db_query_one, init_db


# ============================================================
# QUESTION NORMALIZATION + CLASSIFICATION
# ============================================================
def normalize_question(q: str) -> str:
    """Normalize câu hỏi để hash ổn định."""
    if not q:
        return ""
    # Lowercase
    s = q.lower().strip()
    # Remove extra whitespace
    s = re.sub(r'\s+', ' ', s)
    # Remove punctuation (keep digits, letters, spaces)
    s = re.sub(r'[^\w\s]', '', s, flags=re.UNICODE)
    return s.strip()


def question_hash(q: str) -> str:
    """SHA256 của normalized question."""
    return hashlib.sha256(normalize_question(q).encode('utf-8')).hexdigest()[:16]


def classify_source(source: str = "") -> str:
    """Phân loại source → NEW / REPEAT / INTERNAL."""
    if not source:
        return "NEW"
    internal_sources = {"generator", "reverify", "prediction", "benchmark", "internal"}
    if source.lower() in internal_sources:
        return "INTERNAL"
    return "EXTERNAL"  # từ user/API


# ============================================================
# DB SCHEMA
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS question_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL,
    question_text TEXT NOT NULL,
    source TEXT,                    -- generator / external / reverify / prediction / benchmark
    question_type TEXT,             -- NEW / REPEAT / INTERNAL (first-seen type)
    domain TEXT,
    verdict TEXT,
    confidence REAL,
    first_seen_ts TEXT,
    last_seen_ts TEXT,
    times_seen INTEGER DEFAULT 1,
    cycle_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_qlog_hash ON question_log(question_hash);
CREATE INDEX IF NOT EXISTS idx_qlog_type ON question_log(question_type);
CREATE INDEX IF NOT EXISTS idx_qlog_source ON question_log(source);
CREATE INDEX IF NOT EXISTS idx_qlog_ts ON question_log(last_seen_ts);

-- [V45.1] Event log — every single question visit logged separately
CREATE TABLE IF NOT EXISTS question_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL,
    question_text TEXT NOT NULL,
    source TEXT,
    question_type TEXT,             -- NEW / REPEAT / INTERNAL (at time of THIS event)
    domain TEXT,
    verdict TEXT,
    confidence REAL,
    cycle_id INTEGER,
    event_ts TEXT,
    duration_ms REAL,                -- [V93.9] how long judge() took for this question
    slm_count INTEGER                -- [V93.9] how many SLMs produced a valid answer
);
CREATE INDEX IF NOT EXISTS idx_qev_hash ON question_events(question_hash);
CREATE INDEX IF NOT EXISTS idx_qev_type ON question_events(question_type);
CREATE INDEX IF NOT EXISTS idx_qev_source ON question_events(source);
CREATE INDEX IF NOT EXISTS idx_qev_ts ON question_events(event_ts);
"""

# [V93.9] Legacy DBs — add the 2 new columns if the table already existed without them
_LEGACY_ALTERS = [
    "ALTER TABLE question_events ADD COLUMN duration_ms REAL",
    "ALTER TABLE question_events ADD COLUMN slm_count INTEGER",
]


def init_question_log_db():
    """Init question_log table."""
    try:
        init_db()
    except Exception as e:
        logger.warning(f"init_question_log_db (init_db): {e}")
    for stmt in SCHEMA.strip().split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                db_exec(stmt)
            except Exception as e:
                logger.debug(f"init_question_log_db stmt skipped: {e}")
    # [V93.9] Legacy DB migration — safe no-op if columns already exist
    for stmt in _LEGACY_ALTERS:
        try:
            db_exec(stmt)
        except Exception as e:
            logger.debug(f"[V104.37] meta/question_tracker.py: e={e}")


# ============================================================
# TRACKER
# ============================================================
class QuestionTracker:
    """
    Question Tracker — log + classify mọi câu hỏi đi qua SCP.

    Usage:
        tracker = get_question_tracker()
        qtype = tracker.log(question="2 + 3 = ?", source="generator",
                            domain="math", verdict="PASS", confidence=0.95)
        # qtype is "NEW" / "REPEAT" / "INTERNAL"

        stats = tracker.get_stats()
        # { "NEW": 100, "REPEAT": 50, "INTERNAL": 30 }
    """

    def __init__(self):
        self._cache: dict[str, dict] = {}  # hash -> last info
        self._stats = Counter()
        self._initialized = False
        # [V104.40 #E] TẠI SAO: _cache read/written by log() from multiple threads
        # (process_batch 16 workers) → race on NEW/REPEAT classification.
        # Fix: lock protects all _cache access.
        import threading as _threading
        self._cache_lock = _threading.Lock()

    def _ensure_init(self):
        if not self._initialized:
            try:
                init_question_log_db()
            except Exception as e:
                logger.debug(f"[V104.37] meta/question_tracker.py: e={e}")
            self._initialized = True

    def log(self, question: str, source: str = "external",
            domain: str = "", verdict: str = "",
            confidence: float = 0.0, cycle_id: int = 0,
            duration_ms: Optional[float] = None, slm_count: Optional[int] = None) -> str:
        """
        Log 1 câu hỏi vào tracker.

        Args:
            question: Nội dung câu hỏi
            source: Nguồn (generator / external / reverify / prediction / benchmark)
            domain: Domain (math, chemistry, ...)
            verdict: Verdict (PASS / FAIL / UNKNOWN / PARTIAL / CONFLICT)
            confidence: 0-1
            cycle_id: Cycle ID từ SCP runtime

        Returns:
            question_type: NEW / REPEAT / INTERNAL
        """
        self._ensure_init()
        if not question:
            return "NEW"

        qhash = question_hash(question)
        ts = datetime.now().astimezone().isoformat()
        source_lower = (source or "external").lower()
        is_internal = source_lower in ("generator", "reverify", "prediction", "benchmark")

        # Check cache first (in-memory) — [V104.40 #E] lock-protected
        with self._cache_lock:
            cached = self._cache.get(qhash)
        if cached:
            # Already seen — REPEAT or INTERNAL
            qtype = "INTERNAL" if is_internal else "REPEAT"
        else:
            # Check DB
            try:
                existing = db_query_one(
                    "SELECT id, times_seen FROM question_log WHERE question_hash=?",
                    (qhash,)
                )
                if existing:
                    # DB has it but cache didn't
                    qtype = "INTERNAL" if is_internal else "REPEAT"
                else:
                    # Truly NEW
                    qtype = "INTERNAL" if is_internal else "NEW"
            except Exception:
                qtype = "INTERNAL" if is_internal else "NEW"

        # Insert into question_events (every visit logged)
        try:
            db_exec(
                "INSERT INTO question_events (question_hash, question_text, source, question_type, "
                "domain, verdict, confidence, cycle_id, event_ts, duration_ms, slm_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (qhash, question[:500], source_lower, qtype, domain, verdict,
                 confidence, cycle_id, ts, duration_ms, slm_count)
            )
        except Exception as e:
            logger.debug(f"QuestionTracker.log event insert error: {e}")

        # Update or insert question_log (aggregate) — [V104.40 #E] lock-protected cache
        with self._cache_lock:
            cached = self._cache.get(qhash)  # re-fetch under lock
        if cached:
            with self._cache_lock:
                cached["times_seen"] = cached.get("times_seen", 1) + 1
                cached["last_seen_ts"] = ts
                cached["last_source"] = source_lower
                cached["last_verdict"] = verdict
            try:
                db_exec(
                    "UPDATE question_log SET last_seen_ts=?, times_seen=times_seen+1, "
                    "verdict=?, confidence=?, source=? WHERE question_hash=?",
                    (ts, verdict, confidence, source_lower, qhash)
                )
            except Exception as e:
                logger.debug(f"[V104.37] meta/question_tracker.py: e={e}")
        else:
            try:
                db_exec(
                    "INSERT INTO question_log (question_hash, question_text, source, question_type, "
                    "domain, verdict, confidence, first_seen_ts, last_seen_ts, times_seen, cycle_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (qhash, question[:500], source_lower, qtype, domain, verdict,
                     confidence, ts, ts, cycle_id)
                )
            except Exception as e:
                logger.debug(f"QuestionTracker.log insert error: {e}")
            # [V104.40 #E] lock-protected cache set
            with self._cache_lock:
                self._cache[qhash] = {
                    "question": question,
                    "times_seen": 1,
                    "first_seen_ts": ts,
                    "last_seen_ts": ts,
                    "first_source": source_lower,
                    "last_source": source_lower,
                    "first_verdict": verdict,
                    "last_verdict": verdict,
                }

        self._stats[qtype] += 1
        return qtype

    def get_stats(self, hours: Optional[int] = None) -> dict[str, Any]:
        """
        Lấy thống kê question types.

        Args:
            hours: Nếu set, chỉ đếm trong N giờ gần nhất

        Returns:
            {
                "by_type": {"NEW": N, "REPEAT": N, "INTERNAL": N},
                "by_source": {"generator": N, "external": N, ...},
                "total": N,
                "unique_questions": N,
                "top_repeated": [...top 10 most repeated questions]
            }
        """
        self._ensure_init()
        try:
            # Build WHERE clause for events table
            ev_where = ""
            ev_params = []
            if hours:
                from datetime import timedelta
                cutoff = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat()
                ev_where = "WHERE event_ts >= ?"
                ev_params = [cutoff]

            # By type — count log events from question_events table
            rows = db_query_all(
                f"SELECT question_type, COUNT(*) as cnt FROM question_events {ev_where} GROUP BY question_type",  # nosec B608 — input validated by SCP whitelist  # noqa: S608
                ev_params
            ) or []
            by_type = {r["question_type"]: r["cnt"] for r in rows if r["question_type"]}

            # By source
            rows = db_query_all(
                f"SELECT source, COUNT(*) as cnt FROM question_events {ev_where} GROUP BY source",  # nosec B608 — input validated by SCP whitelist  # noqa: S608
                ev_params
            ) or []
            by_source = {r["source"]: r["cnt"] for r in rows if r["source"]}

            # Total events + unique questions
            total_row = db_query_one(f"SELECT COUNT(*) as cnt FROM question_events {ev_where}", ev_params) or {}  # nosec B608 — input validated by SCP whitelist  # noqa: S608
            total = total_row.get("cnt", 0)
            unique_row = db_query_one(f"SELECT COUNT(DISTINCT question_hash) as cnt FROM question_events {ev_where}", ev_params) or {}  # nosec B608 — input validated by SCP whitelist  # noqa: S608
            unique = unique_row.get("cnt", 0)

            # Top repeated (from question_log aggregate table)
            top_rows = db_query_all(
                "SELECT question_text, times_seen, question_type, verdict, last_seen_ts "
                "FROM question_log ORDER BY times_seen DESC LIMIT 10"
            ) or []
            top_repeated = [
                {
                    "question": r["question_text"][:80] if r.get("question_text") else "",
                    "times_seen": r.get("times_seen", 0),
                    "type": r.get("question_type", ""),
                    "last_verdict": r.get("verdict", ""),
                    "last_seen": r.get("last_seen_ts", ""),
                }
                for r in top_rows
            ]

            return {
                "by_type": by_type,
                "by_source": by_source,
                "total": total,
                "unique_questions": unique,
                "top_repeated": top_repeated,
            }
        except Exception as e:
            logger.warning(f"QuestionTracker.get_stats error: {e}")
            return {
                "by_type": dict(self._stats),
                "by_source": {},
                "total": sum(self._stats.values()),
                "unique_questions": len(self._cache),
                "top_repeated": [],
                "error": str(e),
            }

    def reset_stats(self):
        self._stats.clear()

    def clear_cache(self):
        self._cache.clear()


# ============================================================
# SINGLETON
# ============================================================
_tracker: Optional[QuestionTracker] = None


def get_question_tracker() -> QuestionTracker:
    global _tracker
    if _tracker is None:
        _tracker = QuestionTracker()
    return _tracker


# ============================================================
# SUMMARY TABLE GENERATOR
# ============================================================
def print_summary_table(hours: Optional[int] = None):
    """In bảng tóm tắt NEW / REPEAT / INTERNAL."""
    tracker = get_question_tracker()
    stats = tracker.get_stats(hours=hours)

    print(f"\n{'='*70}")
    print("  QUESTION TYPE TRACKING SUMMARY")
    if hours:
        print(f"  (Last {hours} hours)")
    print(f"{'='*70}")
    print(f"  Total questions logged: {stats.get('total', 0)}")
    print(f"  Unique questions:       {stats.get('unique_questions', 0)}")
    print()
    print(f"  {'Type':15s} {'Count':>8s} {'%':>8s}")
    print(f"  {'-'*15} {'-'*8} {'-'*8}")
    by_type = stats.get("by_type", {})
    total = stats.get("total", 0) or 1
    for qtype in ["NEW", "REPEAT", "INTERNAL"]:
        cnt = by_type.get(qtype, 0)
        pct = cnt / total * 100
        print(f"  {qtype:15s} {cnt:>8d} {pct:>7.1f}%")
    other = sum(v for k, v in by_type.items() if k not in ("NEW", "REPEAT", "INTERNAL"))
    if other:
        print(f"  {'OTHER':15s} {other:>8d} {other/total*100:>7.1f}%")

    print()
    print("  By Source:")
    print(f"  {'Source':15s} {'Count':>8s}")
    print(f"  {'-'*15} {'-'*8}")
    by_source = stats.get('by_source', {})
    for src, cnt in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {src:15s} {cnt:>8d}")

    top = stats.get("top_repeated", [])
    if top:
        print()
        print("  Top Repeated Questions:")
        for i, t in enumerate(top[:5], 1):
            print(f"    {i}. [{t.get('times_seen', 0)}x] [{t.get('type', '?')}] {t.get('question', '')[:60]}")
    print(f"{'='*70}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SCP V45 Question Tracker")
    parser.add_argument("--hours", type=int, help="Chỉ xem N giờ gần nhất")
    parser.add_argument("--test", action="store_true", help="Run demo test")
    args = parser.parse_args()

    if args.test:
        # Demo: log some questions
        tracker = get_question_tracker()
        questions = [
            ("Tính 2 + 3", "generator", "math", "PASS", 0.95),
            ("Tính 2 + 3", "generator", "math", "PASS", 0.95),  # repeat
            ("Thủ đô Pháp?", "external", "geography", "PASS", 0.9),
            ("Thủ đô Pháp?", "external", "geography", "PASS", 0.9),  # repeat
            ("Nhiệt độ Hà Nội?", "reverify", "weather", "PARTIAL", 0.5),  # internal
            ("Tính 7 * 8", "generator", "math", "PASS", 0.95),
            ("Giá bitcoin?", "prediction", "finance", "PASS", 0.8),  # internal
            ("Tính 2 + 3", "benchmark", "math", "PASS", 0.95),  # internal (benchmark)
        ]
        print("\n  Demo: Logging 8 questions...")
        for q, src, dom, verd, conf in questions:
            qtype = tracker.log(q, src, dom, verd, conf)
            print(f"    [{qtype:8s}] [{src:12s}] {q[:50]}")

    print_summary_table(hours=args.hours)


if __name__ == "__main__":
    main()
