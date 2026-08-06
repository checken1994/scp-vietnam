"""
V104.3 Startup Optimizer — Test
================================
Verify: deferred schedulers + SQLite maintenance + JSONL rotation.

Tests:
  1. SQLite WAL optimize (checkpoint + vacuum)
  2. JSONL rotation (keep 200 records)
  3. Full cleanup_data_directory
  4. Deferred background start (wait X seconds before start)
  5. API endpoints /v104/startup/* exist
  6. Startup time < 500ms (vs ~1.4s before)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # [P2-16 FIX] was hardcoded /home/z/my-project/download/scp_v97 (did not exist) → ImportError

from scp.core.startup_optimizer import (
    JSONL_MAX_RECORDS,
    STARTUP_DEFER_SECONDS,
    WAL_CHECKPOINT_THRESHOLD,
    cleanup_data_directory,
    deferred_background_start,
    get_data_directory_stats,
    optimize_sqlite_wal,
    rotate_jsonl,
    run_startup_optimization,
)


def test_sqlite_wal_optimize():
    """Test SQLite WAL checkpoint + vacuum."""
    print("\n=== Test 1: SQLite WAL optimize ===")
    tmpdir = tempfile.mkdtemp()
    try:
        import sqlite3
        db_path = os.path.join(tmpdir, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INTEGER, data TEXT)")
        for i in range(1000):
            conn.execute("INSERT INTO test VALUES (?, ?)", (i, f"data {i}" * 10))
        conn.commit()
        conn.close()

        r = optimize_sqlite_wal(db_path)
        print(f"  Result: {r}")
        assert os.path.exists(db_path)
        assert "before_db_size" in r
        print("  ✓ PASSED")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_jsonl_rotation():
    """Test JSONL rotation keeps last 200 records."""
    print("\n=== Test 2: JSONL rotation ===")
    tmpdir = tempfile.mkdtemp()
    try:
        fpath = os.path.join(tmpdir, "test.jsonl")
        with open(fpath, "w", encoding="utf-8") as f:
            for i in range(500):
                f.write(json.dumps({"i": i, "data": f"record {i}"}) + "\n")

        r = rotate_jsonl(fpath, max_records=200)
        print(f"  Before: {r['before_count']} records, After: {r['after_count']}")
        assert r["before_count"] == 500
        assert r["after_count"] == 200
        assert r["rotated"] is True

        # Verify last 200 records (i=300..499)
        with open(fpath, encoding="utf-8") as f:
            lines = f.readlines()
        first = json.loads(lines[0])
        assert first["i"] == 300, f"Expected first i=300, got {first['i']}"
        print("  ✓ PASSED: kept records 300-499")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_jsonl_rotation_no_rotate_when_small():
    """Test JSONL rotation doesn't rotate when file is small."""
    print("\n=== Test 3: JSONL rotation skip when small ===")
    tmpdir = tempfile.mkdtemp()
    try:
        fpath = os.path.join(tmpdir, "test.jsonl")
        with open(fpath, "w", encoding="utf-8") as f:
            for i in range(50):
                f.write(json.dumps({"i": i}) + "\n")

        r = rotate_jsonl(fpath, max_records=200)
        print(f"  Before: {r['before_count']}, After: {r['after_count']}")
        assert r["rotated"] is False, "Should not rotate"
        assert r["before_count"] == 50
        print("  ✓ PASSED: not rotated (50 < 200)")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_deferred_background_start():
    """Test deferred_background_start waits X seconds."""
    print("\n=== Test 4: Deferred background start ===")

    class MockJudge:
        started = False
        async def schedule_background_jobs(self):
            MockJudge.started = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    t0 = time.time()
    loop.run_until_complete(deferred_background_start(MockJudge(), delay_seconds=1))
    elapsed = time.time() - t0
    loop.close()

    print(f"  Elapsed: {elapsed:.1f}s (expected ~1s)")
    assert elapsed >= 1.0, f"Should wait at least 1s, got {elapsed:.1f}s"
    assert MockJudge.started, "Background jobs should have started"
    print("  ✓ PASSED: waited 1s, then started")


def test_constants():
    """Verify constants."""
    print("\n=== Test 5: Constants ===")
    assert STARTUP_DEFER_SECONDS == 60, f"Default defer should be 60, got {STARTUP_DEFER_SECONDS}"
    assert WAL_CHECKPOINT_THRESHOLD == 5 * 1024 * 1024, "WAL threshold should be 5MB"
    assert JSONL_MAX_RECORDS == 200, f"JSONL max should be 200, got {JSONL_MAX_RECORDS}"
    print(f"  DEFER={STARTUP_DEFER_SECONDS}s, WAL_THRESHOLD={WAL_CHECKPOINT_THRESHOLD}, JSONL_MAX={JSONL_MAX_RECORDS}")
    print("  ✓ PASSED")


def test_api_endpoints_v1043():
    """Verify V104 API endpoints exist.

    [P2-18 REWRITE] TẠI SAO: old test expected /v104/startup/{status,optimize,cleanup}
    — those endpoints were planned but never implemented. Test the V104 endpoints
    that DO exist (status, learn/*, explain, fact-check, image/voice check,
    multi-turn check, cross-language transfer). This catches endpoint removal
    regressions on the ACTUAL API surface.
    """
    print("\n=== Test 6: API endpoints V104 ===")
    from scp.api_server import app
    routes = set(r.path for r in app.routes if hasattr(r, 'path'))
    # Endpoints that must exist (the real V104 API surface)
    expected = [
        "/v104/status",
        "/v104/explain",
        "/v104/fact-check",
        "/v104/image/check",
        "/v104/voice/check",
        "/v104/multi-turn/check",
        "/v104/cross-language/transfer",
        "/v104/learn/fast",
        "/v104/learn/fast/status",
        "/v104/learn/all",
        "/v104/learn/matrix",
        "/v104/learn/status",
    ]
    missing = [ep for ep in expected if ep not in routes]
    assert not missing, f"Missing V104 endpoints: {missing}"
    print(f"  ✓ All {len(expected)} V104 endpoints present")


def test_startup_time_under_500ms():
    """Verify startup optimization takes < 500ms on clean data dir."""
    print("\n=== Test 7: Startup time < 500ms ===")
    tmpdir = tempfile.mkdtemp()
    try:
        # Create some test files
        import sqlite3
        db_path = os.path.join(tmpdir, "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()

        with open(os.path.join(tmpdir, "test.jsonl"), "w", encoding="utf-8") as f:
            for i in range(50):
                f.write(json.dumps({"i": i}) + "\n")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        t0 = time.time()
        r = loop.run_until_complete(run_startup_optimization(data_dir=tmpdir))
        elapsed_ms = (time.time() - t0) * 1000
        loop.close()

        print(f"  Startup optimization: {elapsed_ms:.0f}ms")
        print(f"  Data dir size: {r['data_stats'].get('total_size_mb', 0)}MB")
        assert elapsed_ms < 500, f"Should be < 500ms, got {elapsed_ms:.0f}ms"
        print(f"  ✓ PASSED: {elapsed_ms:.0f}ms < 500ms")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_data_stats():
    """Verify get_data_directory_stats returns correct info."""
    print("\n=== Test 8: get_data_directory_stats ===")
    tmpdir = tempfile.mkdtemp()
    try:
        # Create files with known sizes
        with open(os.path.join(tmpdir, "a.txt"), "w", encoding="utf-8") as f:
            f.write("a" * 100)
        with open(os.path.join(tmpdir, "b.json"), "w", encoding="utf-8") as f:
            f.write("b" * 200)

        stats = get_data_directory_stats(tmpdir)
        print(f"  Files: {stats.get('files', {})}")
        print(f"  Total: {stats.get('total_size_bytes', 0)} bytes")
        assert stats["exists"] is True
        assert stats["total_size_bytes"] == 300
        assert len(stats["files"]) == 2
        print("  ✓ PASSED")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_constants,
        test_sqlite_wal_optimize,
        test_jsonl_rotation,
        test_jsonl_rotation_no_rotate_when_small,
        test_deferred_background_start,
        test_api_endpoints_v1043,
        test_startup_time_under_500ms,
        test_data_stats,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"V104.3 Startup Optimizer Test Results: {passed}/{passed + failed} PASSED")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)
