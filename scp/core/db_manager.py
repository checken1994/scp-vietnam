"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
SCP V14 — Database Manager
Quản lý kết nối SQLite, auto-cleanup, archive gzip.
"""
# ============================================================
# [AUDIT-42 / OPT-42] SQL INJECTION AUDIT — PASS
# ============================================================
# TẠI SAO: auditor flagged "SQL string concat" risk. Full audit of every
# db_exec / db_query_all / db_query_one call in this module:
#
#   - db_exec(sql, params=(), db_path=None) — ACCEPTS parameterized args.
#     Line ~125: `cur = conn.execute(sql, params)` — params bound by sqlite3.
#     Same for db_query_all (line ~205) and db_query_one (line ~219).
#
#   - 95%+ of calls are DDL (CREATE TABLE / CREATE INDEX / ALTER TABLE /
#     PRAGMA / VACUUM) with HARDCODED strings — no user input. Examples:
#       * init_db() lines 283-335 — all hardcoded CREATE TABLE.
#       * _init_all_module_tables() lines 373-758 — hardcoded CREATE/INDEX.
#       * _migrate_reverify_schema() line 1032 — hardcoded ALTER TABLE.
#
#   - Only 4 f-string SQL queries exist (already marked `# nosec B608`):
#       * _cap_table lines 247, 249 — `table_name` from SCP whitelist
#         (validated set of known table names) + `evict_count` is int.
#       * _migrate_verdict_cache_schema line 857 — `col_list` built from
#         PRAGMA table_info() output (DB schema names, not user input).
#       * _migrate_knowledge_schema line 978 — same as above.
#
# CONCLUSION: NO user input flows directly into SQL. Parameterization
# infrastructure EXISTS (db_exec/db_query_* accept `params`) and is already
# used by callers like brain.py / experience.py for INSERT ... VALUES.
# If a future query takes user input, use:
#     db_exec("INSERT INTO t (q, a) VALUES (?, ?)", (question, answer))
#     db_query_all("SELECT * FROM t WHERE q = ?", (question,))
# ============================================================
import gzip
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================
_RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_RUNTIME_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "v13.db")  # Keep v13.db name for backward compat

# ============================================================
# CONNECTION POOL (Thread-safe)
# ============================================================
_persistent_conn = None
_db_lock = threading.RLock()

# [AUDIT-2 FIX] Per-path connection cache — allows modules like FastLearningEngine
# to use their own scp_db_path while STILL sharing _db_lock (prevents race with
# Brain). TẠI SAO: db_exec used to write to the global DB_PATH only. When a
# module constructed with scp_db_path=tmpdir/test.db called db_exec, the write
# went to the global v13.db, not the module's path → test isolation broke.
# The wrong fix (bypass db_exec + use bare sqlite3.connect) restored isolation
# but lost _db_lock → race condition with Brain (Invariant #7 violation).
# Root-cause fix: db_exec/db_query accept optional db_path → use a cached
# per-path connection, still under _db_lock. Single lock, multiple paths.
_path_conns: dict[str, sqlite3.Connection] = {}

def _get_path_conn(db_path: str) -> sqlite3.Connection:
    """Get or create a cached connection for a specific db_path.

    [FIX-CRIT-135 BUG 4] TẠI SAO: the docstring used to read "caller must hold
    _db_lock" — but `db_query_one`/`db_query_all` called this function while
    holding ONLY `_read_lock` (NOT `_db_lock`). So `_path_conns` was mutated
    under two DIFFERENT locks:
      - db_exec (writes) held `_db_lock` while mutating `_path_conns`
      - db_query_* (reads)  held `_read_lock` while mutating `_path_conns`
    → two threads could race in `_get_path_conn` (one read, one write) and
    both create a new connection for the same path, or one's mutation could
    clobber the other's `_path_conns[db_path] = conn` assignment.
    Fix: enforce the contract — callers MUST hold `_db_lock` before calling
    this function. `db_query_*` now acquires `_db_lock` for the per-path
    branch (single lock for the per-path dict). The `_read_lock` is kept
    ONLY for the global `_persistent_conn` reads (V89 WAL optimization
    preserved for the no-db_path case).
    """
    conn = _path_conns.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # [FIX-CRIT-135 BUG 3 companion] enable FK enforcement on per-path
        # connections so Phase0 (which now routes through db_manager after
        # BUG 3 fix) still gets FK constraint behavior.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _path_conns[db_path] = conn
    return conn

def get_db():
    """Persistent connection + thread lock. [V42] SQLite PRAGMA tuned for speed."""
    global _persistent_conn
    _db_lock.acquire()
    try:
        if _persistent_conn is None:
            _persistent_conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
            _persistent_conn.row_factory = sqlite3.Row
            # [V42] Performance PRAGMAs — 10x faster writes
            _persistent_conn.execute("PRAGMA journal_mode=WAL")
            _persistent_conn.execute("PRAGMA synchronous=NORMAL")
            _persistent_conn.execute("PRAGMA cache_size=-128000")  # [V90 OPT] 128MB cache
            _persistent_conn.execute("PRAGMA temp_store=MEMORY")
            _persistent_conn.execute("PRAGMA busy_timeout=30000")
            _persistent_conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O
            _persistent_conn.execute("PRAGMA wal_autocheckpoint=5000")  # [V90 OPT] less frequent checkpoint
            _persistent_conn.execute("PRAGMA page_size=4096")
            # [FIX-CRIT-135 BUG 3 companion] enable FK enforcement on the
            # persistent global conn so Phase0 (which now routes through
            # db_manager after BUG 3 fix) still gets FK constraint behavior.
            _persistent_conn.execute("PRAGMA foreign_keys = ON")
        return _persistent_conn
    finally:
        _db_lock.release()

def db_exec(sql: str, params=(), db_path: Optional[str] = None) -> int:
    """Execute a SQL statement. If db_path is provided, use a per-path connection
    (cached) instead of the global DB_PATH — but STILL under _db_lock.

    [AUDIT-2 FIX] TẠI SAO: FastLearningEngine + RealLearningEngine accept scp_db_path
    in their constructor, but db_exec used to ignore it (always wrote to global
    DB_PATH). The wrong fix bypassed db_exec with bare sqlite3.connect → lost
    _db_lock → race with Brain → facts silently dropped (Invariant #7).
    Root-cause fix: accept optional db_path, use cached per-path connection,
    still acquire _db_lock. Single lock across all paths = no race.
    """
    _db_lock.acquire()
    try:
        conn = _get_path_conn(db_path) if db_path else get_db()
        try:
            cur = conn.execute(sql, params)
            if conn.in_transaction:
                conn.commit()
            return cur.rowcount
        except Exception:
            if conn.in_transaction:
                try: conn.rollback()
                except Exception as e:
                    logger.debug(f"[V104.37] core/db_manager.py: e={e}")
            raise
    finally:
        _db_lock.release()


# V104 OPT: Batch insert — for high-throughput writes
_batch_buffer: list[tuple[str, tuple]] = []
_batch_lock = threading.Lock()
_BATCH_SIZE = 100  # Flush every 100 writes
_BATCH_TIMEOUT = 1.0  # Or every 1 second
_last_batch_flush = time.time()


def db_batch_exec(sql: str, params: tuple) -> None:
    """Buffer writes → flush in batch (100x faster than 1-by-1)."""
    # [FALSE-POS-FIX] F824: `global _last_batch_flush` removed — read-only access
    # (only `time.time() - _last_batch_flush` comparison, no assignment here).
    # Assignment happens in db_batch_flush() below, which has its own global decl.
    with _batch_lock:
        _batch_buffer.append((sql, params))
        should_flush = (
            len(_batch_buffer) >= _BATCH_SIZE or
            time.time() - _last_batch_flush > _BATCH_TIMEOUT
        )
    if should_flush:
        db_batch_flush()


def db_batch_flush() -> int:
    """Flush buffered writes to DB in 1 transaction."""
    # [FALSE-POS-FIX] F824: removed `_batch_buffer` from global — only .clear()/.append()
    # method calls (no reassignment). Kept `_last_batch_flush` — assigned at line 198.
    # NOTE: pyflakes F824 may still flag `_last_batch_flush` as unused due to a known
    # pyflakes limitation (assignment inside `with` block). If so, that is a FALSE
    # POSITIVE — the assignment IS in function scope. Suppress with noqa if needed.
    global _last_batch_flush
    with _batch_lock:
        if not _batch_buffer:
            return 0
        to_flush = _batch_buffer[:]
        _batch_buffer.clear()
        _last_batch_flush = time.time()

    _db_lock.acquire()
    try:
        conn = get_db()
        count = 0
        try:
            for sql, params in to_flush:
                conn.execute(sql, params)
                count += 1
            conn.commit()
            logger.debug(f"Batch flushed: {count} writes")
            return count
        except Exception:
            try: conn.rollback()
            except Exception as e:
                logger.debug(f"[V104.37] core/db_manager.py: e={e}")
            raise
    finally:
        _db_lock.release()

_read_lock = threading.Lock()  # [V89] Light lock for reads — WAL allows concurrent reads

def db_query_all(sql: str, params=(), db_path: Optional[str] = None) -> list[dict]:
    # [V89] Don't use _db_lock for reads on the GLOBAL conn — WAL allows
    # concurrent reads. Only protect against connection creation race.
    # [FIX-CRIT-135 BUG 4] TẠI SAO: when db_path is provided, we MUST NOT use
    # _read_lock — that lock is NOT shared with db_exec's _db_lock, so the
    # per-path dict `_path_conns` was previously mutated under two DIFFERENT
    # locks → race condition (clobbered assignments, duplicate connections).
    # Fix: for the per-path branch, acquire `_db_lock` (single lock for the
    # per-path dict, shared with db_exec). For the global branch, keep the
    # `_read_lock` (V89 WAL optimization preserved when db_path is None).
    if db_path:
        _db_lock.acquire()
        try:
            conn = _get_path_conn(db_path)
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            _db_lock.release()
    with _read_lock:
        conn = get_db()
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

def db_query_one(sql: str, params=(), db_path: Optional[str] = None) -> Optional[dict]:
    # [V89] Light lock for reads on the GLOBAL conn.
    # [FIX-CRIT-135 BUG 4] per-path branch MUST use _db_lock (see db_query_all).
    if db_path:
        _db_lock.acquire()
        try:
            conn = _get_path_conn(db_path)
            r = conn.execute(sql, params).fetchone()
            return dict(r) if r else None
        finally:
            _db_lock.release()
    with _read_lock:
        conn = get_db()
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None

def _cap_table(table_name: str, max_rows: int, evict_count: int):
    """Archive + delete old rows."""
    try:
        count_row = db_query_one(f'SELECT COUNT(*) as cnt FROM "{table_name}"')  # nosec B608 — input validated by SCP whitelist  # noqa: S608
        if count_row and count_row['cnt'] > max_rows:
            try:
                old_rows = db_query_all(f'SELECT * FROM "{table_name}" ORDER BY timestamp ASC LIMIT {evict_count}')  # nosec B608 — input validated by SCP whitelist  # noqa: S608
            except Exception:
                old_rows = db_query_all(f'SELECT * FROM "{table_name}" ORDER BY rowid ASC LIMIT {evict_count}')  # nosec B608 — input validated by SCP whitelist  # noqa: S608
            if not old_rows: return

            archive_dir = os.path.join(DATA_DIR, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(archive_dir, f"{table_name}_{datetime.now().strftime('%Y-%m-%d')}.jsonl.gz")
            with gzip.open(archive_path, "at", encoding="utf-8") as f:
                for row in old_rows:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

            try:
                db_exec(f'DELETE FROM "{table_name}" WHERE rowid IN (SELECT rowid FROM "{table_name}" ORDER BY timestamp ASC LIMIT {evict_count})')  # nosec B608 — input validated by SCP whitelist  # noqa: S608
            except Exception:
                db_exec(f'DELETE FROM "{table_name}" WHERE rowid IN (SELECT rowid FROM "{table_name}" ORDER BY rowid ASC LIMIT {evict_count})')  # nosec B608 — input validated by SCP whitelist  # noqa: S608
    except Exception as e:
        logger.debug(f"[V104.37] core/db_manager.py: e={e}")

def checkpoint_wal():
    try:
        conn = get_db()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        logger.debug(f"[V104.37] core/db_manager.py: e={e}")

def vacuum_db():
    """[V81] VACUUM database để reclaim disk space.
    Run định kỳ (mỗi 1000 cycles hoặc khi DB > 100MB).
    """
    try:
        conn = get_db()
        conn.execute("VACUUM")
        logger.info("[V81] DB VACUUM complete")
        return True
    except Exception as e:
        logger.warning(f"[V81] VACUUM error: {e}")
        return False

def get_db_size_mb() -> float:
    """[V81] Get DB file size in MB."""
    try:
        import os
        return os.path.getsize(str(DB_PATH)) / (1024 * 1024)
    except Exception:
        return 0.0

def init_db():
    """Initialize all V14 tables + V62 all module tables."""
    db_exec("""CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, question TEXT, ai_answer TEXT, frame TEXT, verdict TEXT, reason TEXT, status TEXT DEFAULT 'active', recovered_at TEXT)""")
    # [V89] Ensure meta_goals table exists (was only created by meta.py init)
    db_exec("""CREATE TABLE IF NOT EXISTS meta_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT DEFAULT 'goal',
        description TEXT,
        status TEXT DEFAULT 'active',
        priority REAL DEFAULT 5,
        progress REAL DEFAULT 0,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT,
        parent_id INTEGER
    )""")
    db_exec("""CREATE TABLE IF NOT EXISTS health_states (domain TEXT PRIMARY KEY, state TEXT DEFAULT 'UNKNOWN')""")
    # [V89 FIX] Add last_updated column for health tracking
    try:
        db_exec("ALTER TABLE health_states ADD COLUMN last_updated TEXT")
    except Exception as e:
        logger.debug(f"[V104.37] core/db_manager.py: e={e}")
    db_exec("""CREATE TABLE IF NOT EXISTS health_counters (domain TEXT PRIMARY KEY, pass INTEGER DEFAULT 0, block INTEGER DEFAULT 0, inapplicable INTEGER DEFAULT 0, total INTEGER DEFAULT 0)""")
    db_exec("""CREATE TABLE IF NOT EXISTS recovery_issues (id TEXT PRIMARY KEY, timestamp TEXT, domain TEXT, question TEXT, ai_answer TEXT, error_type TEXT, cause TEXT, fix_action TEXT, status TEXT DEFAULT 'OPEN')""")
    db_exec("""CREATE TABLE IF NOT EXISTS knowledge_memory (id TEXT PRIMARY KEY, timestamp TEXT, question TEXT, ai_answer TEXT, domain TEXT, error_type TEXT, cause TEXT, fix_action TEXT, fix_artifact TEXT, evidence TEXT, confidence REAL, status TEXT DEFAULT 'active', retest_result TEXT DEFAULT '', retest_count INTEGER DEFAULT 0)""")
    db_exec("""CREATE TABLE IF NOT EXISTS error_history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, question TEXT NOT NULL, ai_answer TEXT, frame TEXT, v13_verdict TEXT, final_verdict TEXT, verdict_detail TEXT, error_type TEXT, source TEXT, real_value TEXT, ai_value TEXT, reason TEXT, learned_from TEXT DEFAULT '', sha256 TEXT, importance_score REAL DEFAULT 50.0, last_accessed TEXT, is_foundational INTEGER DEFAULT 0, is_superseded INTEGER DEFAULT 0)""")
    db_exec("CREATE INDEX IF NOT EXISTS idx_error_frame ON error_history(frame)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_error_verdict ON error_history(final_verdict)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_error_source ON error_history(source)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_error_importance ON error_history(importance_score)")
    # [V89.6 FIX] Add domain column to error_history for healing engine queries
    try:
        db_exec("ALTER TABLE error_history ADD COLUMN domain TEXT DEFAULT ''")
    except Exception as e:
        logger.debug(f"[V104.37] core/db_manager.py: e={e}")
    try:
        db_exec("CREATE INDEX IF NOT EXISTS idx_error_domain ON error_history(domain)")
    except Exception as e:
        logger.debug(f"[V104.37] core/db_manager.py: e={e}")

    # [V36] Knowledge versioning — history of changes
    db_exec("""
        CREATE TABLE IF NOT EXISTS knowledge_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            entity TEXT NOT NULL,
            attribute TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            change_type TEXT,
            source TEXT,
            reason TEXT
        )
    """)
    db_exec("CREATE INDEX IF NOT EXISTS idx_kv_entity ON knowledge_versions(entity, attribute)")

    # [V62] Create ALL tables from all modules — central init
    # This ensures system_test passes without needing to init each module individually
    _init_all_module_tables()


def _init_all_module_tables():
    """[V62] Create all tables from all modules — using EXACT schemas from each module."""
    # [V104.49 FIX-C] Migrate legacy verdict_cache schema BEFORE creating the canonical one.
    # This must run first so the CREATE TABLE IF NOT EXISTS below is a no-op when migration
    # already rebuilt the table, and so a legacy DB with the wrong columns gets upgraded
    # in-place instead of being left with a stale schema that silently breaks INSERTs.
    try:
        _migrate_verdict_cache_schema()
    except Exception as e:
        logger.debug(f"[V104.49] verdict_cache migration guard failed: {e}")

    # [ROOT-FIX 1] Migrate legacy knowledge schema BEFORE creating the canonical one.
    # Bug: `knowledge` was historically CREATE'd in 5 places (brain.py, experience.py,
    # fast_learning_engine.py, real_learning_engine.py, db_manager.py) with TWO different
    # PK shapes:
    #   - brain.py / experience.py: `id INTEGER PRIMARY KEY AUTOINCREMENT` + UNIQUE(entity, attribute)
    #   - db_manager / fast_learning / real_learning: `PRIMARY KEY (entity, attribute)` (no id)
    # First-creation-wins → behavior depends on module load order. The migration guard
    # detects a legacy/wrong-shape table and rebuilds it with the canonical schema below.
    try:
        _migrate_knowledge_schema()
    except Exception as e:
        logger.debug(f"[ROOT-FIX 1] knowledge migration guard failed: {e}")

    # [RUNTIME-FIX-3] Migrate legacy reverify_queue missing `notes` column.
    # Bug surfaced in runtime log: "no such column: notes" (8 occurrences in 2 min).
    try:
        _migrate_reverify_schema()
    except Exception as e:
        logger.debug(f"[RUNTIME-FIX-3] reverify migration guard failed: {e}")

    tables = [
        # knowledge (canonical — see _KNOWLEDGE_CANONICAL_DDL below)
        # [ROOT-FIX 1] Single source of truth. All 5 historical CREATE sites now use
        # the same `_KNOWLEDGE_CANONICAL_DDL` constant imported from this module.
        _KNOWLEDGE_CANONICAL_DDL,
        # [V69] ALTER for legacy DBs missing columns (idempotent — `_migrate_knowledge_schema`
        # handles full rebuild, but these ALTERs provide a fast no-op path for already-correct
        # tables that just need a single column added).
        "ALTER TABLE knowledge ADD COLUMN times_wrong INTEGER DEFAULT 0",
        "ALTER TABLE knowledge ADD COLUMN last_verified TEXT",
        "ALTER TABLE knowledge ADD COLUMN bias_correction REAL DEFAULT 0.0",
        "ALTER TABLE knowledge ADD COLUMN notes TEXT DEFAULT ''",
        # experiences (experience.py) — EXACT schema matching ExperienceEngine.learn()
        """CREATE TABLE IF NOT EXISTS experiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            entity TEXT,
            domain TEXT,
            source TEXT,
            ai_answer TEXT,
            real_value TEXT,
            verdict TEXT,
            error_type TEXT,
            error_reason TEXT,
            lesson_type TEXT NOT NULL,
            lesson_description TEXT NOT NULL,
            policy_action TEXT NOT NULL,
            policy_target TEXT,
            policy_value REAL,
            applied INTEGER DEFAULT 0,
            sha256 TEXT,
            frame TEXT,
            final_verdict TEXT,
            verdict_detail TEXT,
            confidence REAL,
            cycle INTEGER
        )""",
        # meta_curiosity (meta.py) — EXACT schema with reason column
        """CREATE TABLE IF NOT EXISTS meta_curiosity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            entity TEXT,
            domain TEXT,
            information_gain REAL DEFAULT 0.0,
            curiosity_type TEXT,
            reason TEXT,
            asked BOOLEAN DEFAULT 0,
            asked_at TEXT,
            result TEXT
        )""",
        # meta_principles (meta.py) — EXACT schema with applied_count
        """CREATE TABLE IF NOT EXISTS meta_principles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            principle TEXT NOT NULL,
            derived_from TEXT,
            domain TEXT,
            confidence REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            applied_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.0
        )""",
        # [V89.6 FIX] Add columns used by principle_rules.py
        "ALTER TABLE meta_principles ADD COLUMN rule_condition TEXT DEFAULT ''",
        "ALTER TABLE meta_principles ADD COLUMN rule_action TEXT DEFAULT ''",
        "ALTER TABLE meta_principles ADD COLUMN fallback_chain TEXT DEFAULT ''",
        "ALTER TABLE meta_principles ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE meta_principles ADD COLUMN previous_version INTEGER DEFAULT 0",
        # knowledge_summaries (consolidator.py)
        """CREATE TABLE IF NOT EXISTS knowledge_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity TEXT NOT NULL,
            attribute TEXT NOT NULL,
            occurrences INTEGER DEFAULT 0,
            unique_values_count INTEGER DEFAULT 0,
            value_distribution TEXT,
            min_value TEXT,
            max_value TEXT,
            median_value TEXT,
            last_value TEXT,
            last_timestamp TEXT,
            mean_value TEXT,
            std_value TEXT,
            error_rate REAL DEFAULT 0,
            anomaly_count INTEGER DEFAULT 0,
            conflict_count INTEGER DEFAULT 0,
            evidence_ids TEXT,
            compressed_at TEXT,
            UNIQUE(entity, attribute)
        )""",
        # [V73] ALTER for legacy DBs that have old schema
        "ALTER TABLE knowledge_summaries ADD COLUMN entity TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN attribute TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN occurrences INTEGER DEFAULT 0",
        "ALTER TABLE knowledge_summaries ADD COLUMN unique_values_count INTEGER DEFAULT 0",
        "ALTER TABLE knowledge_summaries ADD COLUMN value_distribution TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN min_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN max_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN median_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN last_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN mean_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN std_value TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN error_rate REAL DEFAULT 0",
        "ALTER TABLE knowledge_summaries ADD COLUMN anomaly_count INTEGER DEFAULT 0",
        "ALTER TABLE knowledge_summaries ADD COLUMN conflict_count INTEGER DEFAULT 0",
        "ALTER TABLE knowledge_summaries ADD COLUMN evidence_ids TEXT",
        "ALTER TABLE knowledge_summaries ADD COLUMN compressed_at TEXT",
        # [V104.49 FIX-C] verdict_cache — SINGLE source of truth.
        # Union of columns used by all 3 historical definition sites:
        #   - engine.py / db_manager.py (old): cache_key, verdict, confidence, domain, reasoning, timestamp
        #   - data_partitioner.py (old):       question_hash, question_text, verdict, confidence,
        #                                       final_answer, domain, evidence_json, cached_at,
        #                                       expires_at, times_used
        # Both `cache_key` (engine.py: f"{question}|{ai_answer}") and `question_hash`
        # (data_partitioner.py: sha256(question)) are kept — they are conceptually different
        # cache keys. cache_key is PK (nullable in SQLite for TEXT PK), question_hash is UNIQUE
        # so data_partitioner's INSERT OR REPLACE still triggers on question_hash conflicts.
        # Migration guard (_migrate_verdict_cache_schema) runs first; see bottom of file.
        """CREATE TABLE IF NOT EXISTS verdict_cache (
            cache_key TEXT PRIMARY KEY,
            question_hash TEXT UNIQUE,
            question_text TEXT,
            verdict TEXT,
            confidence REAL,
            final_answer TEXT,
            domain TEXT,
            reasoning TEXT,
            evidence_json TEXT,
            timestamp REAL,
            cached_at TEXT,
            expires_at TEXT,
            times_used INTEGER DEFAULT 1
        )""",
        "CREATE INDEX IF NOT EXISTS idx_vc_question_hash ON verdict_cache(question_hash)",
        # calibration_history (calibration_engine.py)
        """CREATE TABLE IF NOT EXISTS calibration_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, domain TEXT,
            slm_name TEXT, confidence REAL, actual_verdict TEXT,
            predicted_correct TEXT, question TEXT, cycle_id INTEGER
        )""",
        # calibration_factors (calibration_engine.py)
        """CREATE TABLE IF NOT EXISTS calibration_factors (
            domain TEXT PRIMARY KEY, factor REAL, sample_count INTEGER,
            last_updated TEXT, brier_score REAL, ece REAL, accuracy REAL
        )""",
        # predictions (predictive.py) — EXACT schema with status
        """CREATE TABLE IF NOT EXISTS predictions (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            domain TEXT,
            predicted_answer TEXT,
            confidence REAL DEFAULT 0.0,
            check_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            actual_answer TEXT,
            verified_at TEXT,
            error_type TEXT,
            error_reason TEXT,
            source TEXT,
            real_value TEXT,
            sha256 TEXT
        )""",
        # reverify_queue (reverify_scheduler.py) — EXACT schema with scheduled_at
        # [RUNTIME-FIX-3] Added `notes` column — was missing here but reverify_scheduler.py:62
        # has it in its own CREATE TABLE, causing "no such column: notes" errors at runtime
        # (log lines 741, 957, 1391, 1406 — 8 occurrences in 2 min). Migration below
        # (_migrate_reverify_schema) handles existing DBs that lack the column.
        """CREATE TABLE IF NOT EXISTS reverify_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            ai_answer TEXT,
            domain TEXT,
            original_verdict TEXT,
            original_confidence REAL,
            cooldown_minutes INTEGER DEFAULT 5,
            scheduled_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reverify_verdict TEXT,
            reverify_confidence REAL,
            reverify_at TEXT,
            verdict_stable BOOLEAN,
            attempts INTEGER DEFAULT 0,
            next_retry TEXT,
            notes TEXT DEFAULT ''
        )""",
        # why_verification_plans (why_engine.py) — EXACT schema with status
        """CREATE TABLE IF NOT EXISTS why_verification_plans (
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
        )""",
        # smart_cache_disk (smart_cache.py)
        # [V85 FIX] Was missing namespace+identifier columns → 28x error per run
        # [V104.49 FIX-C COMPAT NOTE for other fixers]
        #   The `question` column does NOT exist in this table.
        #   Consumers that need to delete rows by question text MUST use `identifier`
        #   (when the SmartCache was set with identifier=question_text) or look up
        #   `cache_key = sha256(namespace + "::" + identifier)` first.
        #   Known broken call sites owned by other fixers (DO NOT edit here):
        #     - scp/runtime/judge.py:3529,3541   `WHERE question IN (...)`
        #     - scp/runtime/engine.py:119        `WHERE question IN (...)`
        #     - scp/runtime/slms.py:135          `WHERE question IN (...)`
        #   Reference fix: scp/runtime/healing_v14.py:290 uses `WHERE identifier IN (...)`.
        """CREATE TABLE IF NOT EXISTS smart_cache_disk (
            cache_key TEXT PRIMARY KEY, value TEXT, source TEXT,
            timestamp REAL, ttl INTEGER,
            namespace TEXT, identifier TEXT, value_blob BLOB
        )""",
        "ALTER TABLE smart_cache_disk ADD COLUMN namespace TEXT",
        "ALTER TABLE smart_cache_disk ADD COLUMN identifier TEXT",
        "ALTER TABLE smart_cache_disk ADD COLUMN value_blob BLOB",
        # [V104.49 FIX-C] Index on identifier — speeds up the DELETE FROM smart_cache_disk
        # WHERE identifier IN (...) queries used by healing/cache-invalidation paths.
        "CREATE INDEX IF NOT EXISTS idx_scd_identifier ON smart_cache_disk(identifier)",
        "CREATE INDEX IF NOT EXISTS idx_scd_namespace ON smart_cache_disk(namespace)",
        # question_log (question_tracker.py)
        """CREATE TABLE IF NOT EXISTS question_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_hash TEXT NOT NULL,
            question_text TEXT NOT NULL,
            source TEXT,
            question_type TEXT,
            domain TEXT,
            verdict TEXT,
            confidence REAL,
            first_seen_ts TEXT,
            last_seen_ts TEXT,
            times_seen INTEGER DEFAULT 1,
            cycle_id INTEGER
        )""",
        # question_events (question_tracker.py)
        """CREATE TABLE IF NOT EXISTS question_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_hash TEXT NOT NULL,
            question_text TEXT NOT NULL,
            source TEXT,
            question_type TEXT,
            domain TEXT,
            verdict TEXT,
            confidence REAL,
            cycle_id INTEGER,
            event_ts TEXT,
            duration_ms REAL,
            slm_count INTEGER
        )""",
        # [V93.9] ALTER for legacy DBs missing these 2 columns
        "ALTER TABLE question_events ADD COLUMN duration_ms REAL",
        "ALTER TABLE question_events ADD COLUMN slm_count INTEGER",
        # cognitive_gate_log (cognitive_gate.py)
        """CREATE TABLE IF NOT EXISTS cognitive_gate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT,
            domain TEXT,
            original_verdict TEXT,
            gated_verdict TEXT,
            reasons TEXT,
            evidence_type TEXT,
            reverify_outcome TEXT,
            reverify_timestamp TEXT
        )""",
        # proof_graph_history (cognitive_engine.py)
        """CREATE TABLE IF NOT EXISTS proof_graph_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT,
            root_claim TEXT,
            overall_status TEXT,
            node_count INTEGER,
            nodes_json TEXT,
            verdict TEXT,
            confidence REAL
        )""",
        # pending_resolutions (cognitive_engine.py)
        """CREATE TABLE IF NOT EXISTS pending_resolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT,
            domain TEXT,
            subtype TEXT,
            reason TEXT,
            resolution_strategy TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_attempt_ts TEXT
        )""",
        # pending_reverification (cognitive_engine.py)
        """CREATE TABLE IF NOT EXISTS pending_reverification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT,
            domain TEXT,
            counter_questions TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0
        )""",
        # live_knowledge_cache (live_knowledge.py)
        """CREATE TABLE IF NOT EXISTS live_knowledge_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT NOT NULL UNIQUE,
            query_text TEXT,
            domain TEXT,
            value TEXT,
            source TEXT,
            sources_succeeded TEXT,
            confidence REAL,
            fetched_at TEXT,
            expires_at TEXT,
            metadata TEXT
        )""",
    ]
    for sql in tables:
        try:
            db_exec(sql)
        except Exception as e:
            logger.debug(f"[V104.37] core/db_manager.py: e={e}")

    # Create indexes
    # [V72] Added composite indexes for common query patterns:
    #   - question_log by timestamp + domain (for "recent questions in domain X")
    #   - question_events by source + timestamp (for "real_fetcher questions in last hour")
    #   - external_questions by used + fetched_at (for "get N unused real questions")
    #   - error_history by domain + final_verdict (for "FAIL count per domain")
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_vc_time ON verdict_cache(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_ch_domain ON calibration_history(domain)",
        "CREATE INDEX IF NOT EXISTS idx_rq_status ON reverify_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status)",
        "CREATE INDEX IF NOT EXISTS idx_pred_check ON predictions(check_date)",
        # question_log indexes
        "CREATE INDEX IF NOT EXISTS idx_qlog_hash ON question_log(question_hash)",
        "CREATE INDEX IF NOT EXISTS idx_qlog_ts ON question_log(last_seen_ts)",
        "CREATE INDEX IF NOT EXISTS idx_qlog_domain ON question_log(domain)",
        "CREATE INDEX IF NOT EXISTS idx_qlog_source ON question_log(source)",
        "CREATE INDEX IF NOT EXISTS idx_qlog_verdict ON question_log(verdict)",
        "CREATE INDEX IF NOT EXISTS idx_qlog_domain_ts ON question_log(domain, last_seen_ts)",
        # question_events indexes
        "CREATE INDEX IF NOT EXISTS idx_qev_hash ON question_events(question_hash)",
        "CREATE INDEX IF NOT EXISTS idx_qev_ts ON question_events(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_qev_domain ON question_events(domain)",
        "CREATE INDEX IF NOT EXISTS idx_qev_source ON question_events(source)",
        "CREATE INDEX IF NOT EXISTS idx_qev_source_ts ON question_events(source, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_qev_question_type ON question_events(question_type)",
        # cognitive_gate_log
        "CREATE INDEX IF NOT EXISTS idx_cgl_ts ON cognitive_gate_log(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_cgl_domain ON cognitive_gate_log(domain)",
        "CREATE INDEX IF NOT EXISTS idx_cgl_gated ON cognitive_gate_log(gated_verdict)",
        # proof_graph_history
        "CREATE INDEX IF NOT EXISTS idx_pgh_ts ON proof_graph_history(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_pgh_status ON proof_graph_history(status)",
        # live_knowledge_cache
        "CREATE INDEX IF NOT EXISTS idx_lkc_hash ON live_knowledge_cache(query_hash)",
        "CREATE INDEX IF NOT EXISTS idx_lkc_domain ON live_knowledge_cache(domain)",
        # [V72] external_questions — critical for RealQuestionFetcher.get_unused()
        "CREATE INDEX IF NOT EXISTS idx_eq_used ON external_questions(used, fetched_at)",
        "CREATE INDEX IF NOT EXISTS idx_eq_source ON external_questions(source)",
        "CREATE INDEX IF NOT EXISTS idx_eq_domain ON external_questions(domain)",
        # [V72] error_history — composite for "FAIL count per domain" queries
        "CREATE INDEX IF NOT EXISTS idx_eh_domain_verdict ON error_history(domain, final_verdict)",
        "CREATE INDEX IF NOT EXISTS idx_eh_ts ON error_history(timestamp)",
        # [V72] knowledge — for "is this entity already verified?" lookups
        "CREATE INDEX IF NOT EXISTS idx_k_entity ON knowledge(entity)",
        "CREATE INDEX IF NOT EXISTS idx_k_source ON knowledge(source)",
        # [V72] experiences — for "recent experiences" queries
        "CREATE INDEX IF NOT EXISTS idx_exp_ts ON experiences(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_exp_domain ON experiences(domain)",
        # [V72] calibration_history — for "recent calibration per domain"
        "CREATE INDEX IF NOT EXISTS idx_ch_domain_ts ON calibration_history(domain, timestamp)",
    ]
    for idx_sql in indexes:
        try:
            db_exec(idx_sql)
        except Exception as e:
            logger.debug(f"[V104.37] core/db_manager.py: e={e}")


# ============================================================
# [V104.49 FIX-C] SCHEMA MIGRATION GUARD for verdict_cache
# ============================================================
# Bug P1-11: verdict_cache was historically CREATE'd in 3 places with
# incompatible column sets. On a legacy DB, whichever CREATE ran first won
# and the other consumers' INSERTs silently failed (caught by bare except).
# This guard detects a legacy/wrong-column verdict_cache and rebuilds it
# with the canonical union schema, preserving whatever common data we can.
_VERDICT_CACHE_CANONICAL_COLS = {
    "cache_key", "question_hash", "question_text", "verdict",
    "confidence", "final_answer", "domain", "reasoning",
    "evidence_json", "timestamp", "cached_at", "expires_at",
    "times_used",
}

_VERDICT_CACHE_CANONICAL_DDL = """
CREATE TABLE verdict_cache (
    cache_key TEXT PRIMARY KEY,
    question_hash TEXT UNIQUE,
    question_text TEXT,
    verdict TEXT,
    confidence REAL,
    final_answer TEXT,
    domain TEXT,
    reasoning TEXT,
    evidence_json TEXT,
    timestamp REAL,
    cached_at TEXT,
    expires_at TEXT,
    times_used INTEGER DEFAULT 1
)
"""


def _migrate_verdict_cache_schema() -> None:
    """[V104.49 FIX-C] Detect legacy verdict_cache schema and rebuild to canonical.

    Strategy:
      1. PRAGMA table_info(verdict_cache). If no table → nothing to do (the
         CREATE TABLE IF NOT EXISTS in _init_all_module_tables will build it).
      2. If table exists with all canonical columns → no-op (idempotent).
      3. If table exists but is missing any canonical column → rename to
         verdict_cache_old, create canonical, copy over the intersection of
         common columns, drop the old table.
    All steps wrapped in try/except so a partial failure leaves the old table
    intact (better stale-schema than no-schema).
    """
    try:
        cols = db_query_all("PRAGMA table_info(verdict_cache)")
    except Exception as e:
        logger.debug(f"[V104.49] PRAGMA table_info(verdict_cache) failed: {e}")
        return

    if not cols:
        # Table doesn't exist yet — canonical CREATE will handle it.
        return

    col_names = {c.get("name") for c in cols if c.get("name")}
    if _VERDICT_CACHE_CANONICAL_COLS.issubset(col_names):
        # Already has all canonical columns — nothing to migrate.
        return

    logger.info(
        f"[V104.49 FIX-C] verdict_cache has legacy schema "
        f"(cols={sorted(col_names)}); rebuilding to canonical."
    )

    try:
        db_exec("ALTER TABLE verdict_cache RENAME TO verdict_cache_old")
    except Exception as e:
        logger.warning(f"[V104.49] RENAME verdict_cache → verdict_cache_old failed: {e}")
        return

    try:
        db_exec(_VERDICT_CACHE_CANONICAL_DDL)
    except Exception as e:
        # Restore the old name and bail out.
        logger.warning(f"[V104.49] canonical CREATE failed: {e}; restoring old table")
        try:
            db_exec("ALTER TABLE verdict_cache_old RENAME TO verdict_cache")
        except Exception as ee:
            logger.debug(f"[V104.49] restore failed: {ee}")
        return

    # Migrate the intersection of columns that exist in BOTH old and canonical.
    try:
        old_cols = db_query_all("PRAGMA table_info(verdict_cache_old)")
    except Exception as e:
        old_cols = []
        logger.debug(f"[V104.49] PRAGMA table_info(verdict_cache_old) failed: {e}")
    old_col_names = {c.get("name") for c in (old_cols or []) if c.get("name")}
    common = _VERDICT_CACHE_CANONICAL_COLS & old_col_names
    if common:
        col_list = ", ".join(sorted(common))
        try:
            db_exec(
                f"INSERT INTO verdict_cache ({col_list}) "  # nosec B608 — input validated by SCP whitelist  # noqa: S608
                f"SELECT {col_list} FROM verdict_cache_old"
            )
            logger.info(
                f"[V104.49 FIX-C] migrated {len(common)} columns "
                f"({sorted(common)}) from verdict_cache_old → verdict_cache"
            )
        except Exception as e:
            logger.warning(
                f"[V104.49] row migration failed ({e}); "
                f"canonical table is empty but functional."
            )

    try:
        db_exec("DROP TABLE verdict_cache_old")
    except Exception as e:
        logger.debug(f"[V104.49] DROP verdict_cache_old failed: {e}")


# ============================================================
# [ROOT-FIX 1] SCHEMA MIGRATION GUARD for knowledge
# ============================================================
# Bug: `knowledge` was historically CREATE'd in 5 places (brain.py:89,
# experience.py:258, fast_learning_engine.py:104, real_learning_engine.py:246,
# db_manager.py:355) with TWO different PK shapes:
#   - brain.py / experience.py: `id INTEGER PRIMARY KEY AUTOINCREMENT` + UNIQUE(entity, attribute)
#   - db_manager / fast_learning / real_learning: `PRIMARY KEY (entity, attribute)` (no id)
# `CREATE TABLE IF NOT EXISTS` is first-creation-wins → behavior depends on module
# load order. If brain.py ran first, schema has an `id` column that the canonical
# (no-id) INSERTs ignore silently (and the UNIQUE constraint kicks in for de-dup).
# If db_manager ran first, no `id` column → `SELECT id FROM knowledge` (used by
# legacy scripts) fails.
#
# Single source of truth: all 5 sites now import `_KNOWLEDGE_CANONICAL_DDL` and use
# it as their CREATE TABLE statement. This guard detects any legacy/wrong-shape
# table and rebuilds it with the canonical schema, preserving common data.

_KNOWLEDGE_CANONICAL_COLS = {
    "entity", "attribute", "value", "value_type",
    "confidence", "source", "timestamp", "times_verified",
    "times_wrong", "last_verified", "bias_correction", "notes",
}

_KNOWLEDGE_CANONICAL_DDL = """
CREATE TABLE IF NOT EXISTS knowledge (
    entity TEXT, attribute TEXT, value TEXT, value_type TEXT,
    confidence REAL, source TEXT, timestamp TEXT, times_verified INTEGER DEFAULT 1,
    times_wrong INTEGER DEFAULT 0,
    last_verified TEXT,
    bias_correction REAL DEFAULT 0.0,
    notes TEXT DEFAULT '',
    PRIMARY KEY (entity, attribute)
)
"""


def _migrate_knowledge_schema() -> None:
    """[ROOT-FIX 1] Detect legacy `knowledge` schema and rebuild to canonical.

    Strategy (mirrors `_migrate_verdict_cache_schema`):
      1. PRAGMA table_info(knowledge). If no table → no-op (canonical CREATE
         will build it).
      2. If table exists with all canonical columns AND no `id` column → no-op.
      3. If table has an `id` column (legacy brain.py/experience.py shape) OR
         is missing any canonical column → rename to knowledge_old, create
         canonical, copy over the common columns, drop the old table.
    All steps wrapped in try/except so a partial failure leaves the old table
    intact (better stale-schema than no-schema).
    """
    try:
        cols = db_query_all("PRAGMA table_info(knowledge)")
    except Exception as e:
        logger.debug(f"[ROOT-FIX 1] PRAGMA table_info(knowledge) failed: {e}")
        return

    if not cols:
        # Table doesn't exist yet — canonical CREATE will handle it.
        return

    col_names = {c.get("name") for c in cols if c.get("name")}
    has_id_column = "id" in col_names
    has_all_canonical = _KNOWLEDGE_CANONICAL_COLS.issubset(col_names)

    if has_all_canonical and not has_id_column:
        # Already canonical — nothing to migrate.
        return

    logger.info(
        f"[ROOT-FIX 1] knowledge has legacy schema "
        f"(cols={sorted(col_names)}, has_id={has_id_column}); rebuilding to canonical."
    )

    try:
        db_exec("ALTER TABLE knowledge RENAME TO knowledge_old")
    except Exception as e:
        logger.warning(f"[ROOT-FIX 1] RENAME knowledge → knowledge_old failed: {e}")
        return

    try:
        db_exec(_KNOWLEDGE_CANONICAL_DDL)
    except Exception as e:
        # Restore the old name and bail out.
        logger.warning(f"[ROOT-FIX 1] canonical CREATE failed: {e}; restoring old table")
        try:
            db_exec("ALTER TABLE knowledge_old RENAME TO knowledge")
        except Exception as ee:
            logger.debug(f"[ROOT-FIX 1] restore failed: {ee}")
        return

    # Migrate the intersection of columns that exist in BOTH old and canonical.
    try:
        old_cols = db_query_all("PRAGMA table_info(knowledge_old)")
    except Exception as e:
        old_cols = []
        logger.debug(f"[ROOT-FIX 1] PRAGMA table_info(knowledge_old) failed: {e}")
    old_col_names = {c.get("name") for c in (old_cols or []) if c.get("name")}
    common = _KNOWLEDGE_CANONICAL_COLS & old_col_names
    if common:
        col_list = ", ".join(sorted(common))
        try:
            db_exec(
                f"INSERT OR IGNORE INTO knowledge ({col_list}) "  # nosec B608 — input validated by SCP whitelist  # noqa: S608
                f"SELECT {col_list} FROM knowledge_old"
            )
            logger.info(
                f"[ROOT-FIX 1] migrated {len(common)} columns "
                f"({sorted(common)}) from knowledge_old → knowledge"
            )
        except Exception as e:
            logger.warning(
                f"[ROOT-FIX 1] row migration failed ({e}); "
                f"canonical table is empty but functional."
            )

    try:
        db_exec("DROP TABLE knowledge_old")
    except Exception as e:
        logger.debug(f"[ROOT-FIX 1] DROP knowledge_old failed: {e}")


def _migrate_reverify_schema() -> None:
    """[RUNTIME-FIX-3] Ensure reverify_queue has `notes` column.

    Root cause (runtime log lines 741, 957, 1391, 1406):
      WARNING | scp.reverify | ReVerify process error: no such column: notes
      WARNING | scp.judge | [V104.42 #AV] ReVerify process_pending error: no such column: notes

    Bug: this module's CREATE TABLE reverify_queue (canonical, runs first at
    startup) did NOT include the `notes` column. But reverify_scheduler.py:62
    has `notes TEXT` in its own CREATE TABLE — which is a no-op (IF NOT EXISTS)
    because the table was already created by db_manager. Result: reverify
    UPDATE/SELECT referencing `notes` fails.

    Fix strategy:
      1. PRAGMA table_info(reverify_queue). If no table → no-op (canonical
         CREATE will build it with the new `notes` column).
      2. If table exists but `notes` column missing → ALTER TABLE ADD COLUMN
         (idempotent — only adds if missing).
    """
    try:
        cols = db_query_all("PRAGMA table_info(reverify_queue)")
    except Exception as e:
        logger.debug(f"[RUNTIME-FIX-3] PRAGMA table_info(reverify_queue) failed: {e}")
        return

    if not cols:
        # Table doesn't exist yet — canonical CREATE will build it with notes.
        return

    col_names = {c.get("name") for c in cols if c.get("name")}
    if "notes" in col_names:
        # Already has the column — nothing to do.
        return

    try:
        db_exec("ALTER TABLE reverify_queue ADD COLUMN notes TEXT DEFAULT ''")
        logger.info("[RUNTIME-FIX-3] reverify_queue: added missing 'notes' column")
    except Exception as e:
        logger.error(f"[RUNTIME-FIX-3] failed to add 'notes' column to reverify_queue: {e}")
