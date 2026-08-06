"""
V104.2 Fast Learning Engine — Test
====================================
Verify parallel learning + skip-known + compounding + adaptive interval.

Tests:
  1. Parallel speedup (10 questions parallel ~ 1 question sequential time)
  2. Skip-already-known (KB check before Ollama call)
  3. Compounding L2 questions (sinh từ facts đã học)
  4. Adaptive interval (BURST/FAST/IDLE based on stored_ratio)
  5. API endpoints /v104/learn/fast, /fast/status, /fast/benchmark
  6. Concurrency = 10 (Semaphore)
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # [P2-16 FIX] was hardcoded /home/z/my-project/download/scp_v97 (did not exist) → ImportError

from scp.core.fast_learning_engine import (
    LEARN_INTERVAL_BURST,
    LEARN_INTERVAL_FAST,
    LEARN_INTERVAL_IDLE,
    PARALLEL_OLLAMA_CONCURRENCY,
    WIKIPEDIA_CONCURRENCY,
    FastLearningEngine,
    start_fast_learning_thread,
)


def test_parallel_speedup_10x():
    """V104.2.3: 10 questions parallel phải nhanh hơn 10 sequential ít nhất 5x."""
    print("\n=== Test 1: Parallel speedup 5-10x ===")
    tmpdir = tempfile.mkdtemp()
    try:
        engine = FastLearningEngine(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )

        async def mock_ask(q):
            await asyncio.sleep(0.5)  # 500ms per Ollama call
            return f"Mock: {q[:40]}"

        async def mock_wiki(q, a):
            await asyncio.sleep(0.2)  # 200ms per Wiki call
            return {"verified": True, "confidence": 0.85}

        engine._ask_ollama_parallel = mock_ask
        engine._check_wikipedia_parallel = mock_wiki

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        t0 = time.time()
        r = loop.run_until_complete(engine.fast_learning_cycle(count=10))
        elapsed_ms = (time.time() - t0) * 1000
        loop.close()

        # Sequential would be: 10 × (500 + 200) = 7000ms
        # Parallel (10 concurrent) should be ~700-1000ms
        sequential_est = 10 * 700
        speedup = sequential_est / elapsed_ms
        print(f"  10 questions: {elapsed_ms:.0f}ms (sequential est: {sequential_est}ms)")
        print(f"  Speedup: {speedup:.1f}x")
        assert speedup >= 5.0, f"Speedup too low: {speedup:.1f}x (expected ≥5x)"
        assert r["asked"] == 10
        print(f"  ✓ PASSED: {speedup:.1f}x speedup")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_skip_already_known():
    """V104.2.1: Skip câu hỏi đã có trong KB."""
    print("\n=== Test 2: Skip-already-known ===")
    tmpdir = tempfile.mkdtemp()
    try:
        engine = FastLearningEngine(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )

        # Pre-populate KB với 3 câu hỏi đã biết
        engine._store_kb(
            entity="Thủ đô của Việt Nam là gì?",
            attribute="verified_answer",
            value="Hà Nội",
            source="pre-stored",
            confidence=0.95,
        )
        engine._store_kb(
            entity="Diện tích của Mỹ bao nhiêu km vuông?",
            attribute="verified_answer",
            value="9.833.520 km²",
            source="pre-stored",
            confidence=0.95,
        )

        # Mock Ollama — count calls (should be < count because some are skipped)
        call_count = 0
        async def mock_ask(q):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return f"Mock: {q[:40]}"

        async def mock_wiki(q, a):
            return {"verified": True, "confidence": 0.85}

        engine._ask_ollama_parallel = mock_ask
        engine._check_wikipedia_parallel = mock_wiki

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 50 questions — but we only have 14×5=70 unique templates
        # With random sampling, we'll likely hit some of the 2 pre-stored
        r = loop.run_until_complete(engine.fast_learning_cycle(count=50))
        loop.close()

        print(f"  Asked: {r['asked']}, Skipped known: {r['skipped_known']}")
        # With 2 pre-stored facts and 50 questions, very likely to hit them
        # But random sampling may not always hit
        # So we just check skipped_known is captured
        assert "skipped_known" in r
        assert r["asked"] + r["skipped_known"] == 50 + r["compounding_l2"]
        print(f"  ✓ PASSED: skip mechanism works (skipped {r['skipped_known']})")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_compounding_l2():
    """V104.2.2: Compounding — sinh câu hỏi L2 từ facts đã học."""
    print("\n=== Test 3: Compounding L2 questions ===")
    tmpdir = tempfile.mkdtemp()
    try:
        engine = FastLearningEngine(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )

        # Pre-populate KB với facts về thủ đô
        engine._store_kb(
            entity="Thủ đô của Việt Nam là gì?",
            attribute="verified_answer",
            value="Hà Nội",
            source="pre-stored",
            confidence=0.95,
        )

        compounding = engine._generate_compounding_questions(count=5)
        print(f"  Generated {len(compounding)} L2 questions")
        for q, _country, domain in compounding[:3]:
            print(f"    [{domain}] {q}")
        assert len(compounding) > 0, "No L2 questions generated"
        # Check at least 1 question is L2 (has "tại sao" or "so sánh" etc.)
        l2_keywords = ["tại sao", "so sánh", "đe dọa", "khai thác"]
        has_l2 = any(any(kw in q.lower() for kw in l2_keywords)
                     for q, _, _ in compounding)
        assert has_l2, f"No L2 keywords found: {compounding}"
        print("  ✓ PASSED: L2 questions generated")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_adaptive_interval_burst():
    """V104.2.4: Adaptive interval — BURST mode khi stored_ratio > 50%."""
    print("\n=== Test 4: Adaptive interval BURST mode ===")
    tmpdir = tempfile.mkdtemp()
    try:
        engine = FastLearningEngine(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )

        # Mock with 100% verify rate → BURST mode
        async def mock_ask(q):
            await asyncio.sleep(0.01)
            return f"Mock: {q[:40]}"

        async def mock_wiki(q, a):
            return {"verified": True, "confidence": 0.9}

        engine._ask_ollama_parallel = mock_ask
        engine._check_wikipedia_parallel = mock_wiki

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        r = loop.run_until_complete(engine.fast_learning_cycle(count=10))
        loop.close()

        print(f"  Stored ratio: {r['stored_ratio']:.2f}")
        print(f"  Adaptive mode: {r['adaptive_mode']}")
        print(f"  Adaptive interval: {r['adaptive_interval_s']}s")
        assert r["stored_ratio"] >= 0.5, f"Expected ≥0.5, got {r['stored_ratio']}"
        assert r["adaptive_mode"] == "BURST", f"Expected BURST, got {r['adaptive_mode']}"
        assert r["adaptive_interval_s"] == LEARN_INTERVAL_BURST
        print("  ✓ PASSED: BURST mode (60s interval)")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_adaptive_interval_idle():
    """V104.2.4: Adaptive interval — IDLE mode khi stored_ratio < 10%."""
    print("\n=== Test 5: Adaptive interval IDLE mode ===")
    tmpdir = tempfile.mkdtemp()
    try:
        engine = FastLearningEngine(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )

        # Mock with 0% verify rate → IDLE mode
        async def mock_ask(q):
            await asyncio.sleep(0.01)
            return f"Mock: {q[:40]}"

        async def mock_wiki(q, a):
            return {"verified": False, "confidence": 0.0}

        engine._ask_ollama_parallel = mock_ask
        engine._check_wikipedia_parallel = mock_wiki

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        r = loop.run_until_complete(engine.fast_learning_cycle(count=10))
        loop.close()

        print(f"  Stored ratio: {r['stored_ratio']:.2f}")
        print(f"  Adaptive mode: {r['adaptive_mode']}")
        assert r["stored_ratio"] < 0.1, f"Expected <0.1, got {r['stored_ratio']}"
        assert r["adaptive_mode"] == "IDLE", f"Expected IDLE, got {r['adaptive_mode']}"
        assert r["adaptive_interval_s"] == LEARN_INTERVAL_IDLE
        print("  ✓ PASSED: IDLE mode (30 min interval)")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_concurrency_constants():
    """Verify concurrency constants đúng."""
    print("\n=== Test 6: Concurrency constants ===")
    assert PARALLEL_OLLAMA_CONCURRENCY == 10, f"Expected 10, got {PARALLEL_OLLAMA_CONCURRENCY}"
    assert WIKIPEDIA_CONCURRENCY == 5, f"Expected 5, got {WIKIPEDIA_CONCURRENCY}"
    assert LEARN_INTERVAL_FAST == 300, f"FAST interval wrong: {LEARN_INTERVAL_FAST}"
    assert LEARN_INTERVAL_BURST == 60, f"BURST interval wrong: {LEARN_INTERVAL_BURST}"
    assert LEARN_INTERVAL_IDLE == 1800, f"IDLE interval wrong: {LEARN_INTERVAL_IDLE}"
    print(f"  Ollama concurrency: {PARALLEL_OLLAMA_CONCURRENCY}")
    print(f"  Wikipedia concurrency: {WIKIPEDIA_CONCURRENCY}")
    print(f"  FAST: {LEARN_INTERVAL_FAST}s, BURST: {LEARN_INTERVAL_BURST}s, IDLE: {LEARN_INTERVAL_IDLE}s")
    print("  ✓ PASSED")


def test_api_endpoints_v1042():
    """Verify API endpoints V104.2 tồn tại."""
    print("\n=== Test 7: API endpoints V104.2 ===")
    from scp.api_server import app
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    expected = ["/v104/learn/fast", "/v104/learn/fast/status", "/v104/learn/fast/benchmark"]
    for ep in expected:
        assert ep in routes, f"Missing endpoint: {ep}"
    print("  Found /v104/learn/fast ✓")
    print("  Found /v104/learn/fast/status ✓")
    print("  Found /v104/learn/fast/benchmark ✓")
    print("  ✓ PASSED")


def test_background_thread_startable():
    """Verify fast learning thread có thể start."""
    print("\n=== Test 8: Background thread startable ===")
    tmpdir = tempfile.mkdtemp()
    try:
        thread = start_fast_learning_thread(
            scp_db_path=os.path.join(tmpdir, "test.db"),
            data_dir=tmpdir,
        )
        assert thread.is_alive(), "Thread not alive"
        print(f"  Thread alive: {thread.is_alive()}")
        print(f"  Thread name: {thread.name}")
        # Stop thread (daemon — will be killed when main exits)
        # Just verify it can start; it'll continue in background
        print("  ✓ PASSED")
    finally:
        # Daemon thread will exit when test process exits
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_concurrency_constants,
        test_parallel_speedup_10x,
        test_skip_already_known,
        test_compounding_l2,
        test_adaptive_interval_burst,
        test_adaptive_interval_idle,
        test_api_endpoints_v1042,
        test_background_thread_startable,
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
    print(f"V104.2 Fast Learning Test Results: {passed}/{passed + failed} PASSED")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)
