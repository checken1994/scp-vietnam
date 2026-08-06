#!/usr/bin/env python3
"""
[Task 35-A] Behavioral verification for SmartCache SLMResponse serialization fix.

Tests:
1. GeographySLM.predict() returns proper SLMResponse for cached question
2. slm_cache_set + slm_cache_get roundtrip preserves SLMResponse data
3. Disk cache stores proper JSON dict (not string repr)
4. End-to-end judge() returns non-zero confidence for cached question
5. Legacy broken string entries are gracefully handled (treated as miss)
"""
import sys

sys.path.insert(0, '/home/z/my-project/scp_v105_fix')

import hashlib
import json


def test_cached_question_returns_proper_slmresponse():
    """Test 1: 'Thủ đô Việt Nam?' was cached as broken string before fix.
    After fix: should re-cache properly and return SLMResponse with conf > 0."""
    from scp.runtime.slm_base import SLMResponse
    from scp.runtime.slms import GeographySLM

    slm = GeographySLM()
    result = slm.predict("Thủ đô Việt Nam?")

    assert isinstance(result, SLMResponse), \
        f"Expected SLMResponse, got {type(result).__name__}"
    assert result.answer, f"Empty answer: {result.answer!r}"
    assert result.confidence > 0, f"Zero confidence: {result.confidence}"
    assert "Hà Nội" in result.answer or "Hanoi" in result.answer, \
        f"Wrong answer: {result.answer!r}"
    print(f"  [PASS] cached Q → SLMResponse(answer={result.answer!r}, conf={result.confidence})")


def test_cache_roundtrip_preserves_data():
    """Test 2: set then get returns same SLMResponse data."""
    from scp.core.smart_cache import get_smart_cache, slm_cache_get, slm_cache_set
    from scp.runtime.slm_base import SLMResponse

    cache = get_smart_cache()
    cache.invalidate("slm:TestSLM35A", "roundtrip test")
    try:
        from scp.core.db_manager import db_exec
        ck = hashlib.sha256(b"slm:TestSLM35A::roundtrip test").hexdigest()
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    except Exception:
        pass

    original = SLMResponse(
        question="roundtrip test",
        answer="test = 42",
        confidence=0.85,
        domain="test",
        reasoning="test reasoning",
        evidence={"source": "TestSource", "value": 42},
        slm_name="TestSLM35A",
        processing_time=0.001,
    )
    slm_cache_set("TestSLM35A", "roundtrip test", original, "TestSource")
    result = slm_cache_get("TestSLM35A", "roundtrip test")

    assert isinstance(result, SLMResponse), f"Expected SLMResponse, got {type(result).__name__}"
    assert result.answer == "test = 42", f"Answer mismatch: {result.answer!r}"
    assert result.confidence == 0.85, f"Confidence mismatch: {result.confidence}"
    print(f"  [PASS] roundtrip preserves SLMResponse(answer={result.answer!r}, conf={result.confidence})")

    # Cleanup
    cache.invalidate("slm:TestSLM35A", "roundtrip test")
    try:
        from scp.core.db_manager import db_exec
        ck = hashlib.sha256(b"slm:TestSLM35A::roundtrip test").hexdigest()
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    except Exception:
        pass


def test_disk_cache_stores_json_dict():
    """Test 3: After fix, disk cache value_blob should be JSON dict, not string repr."""
    from scp.core.db_manager import db_query_one
    from scp.core.smart_cache import get_smart_cache, slm_cache_set
    from scp.runtime.slm_base import SLMResponse

    cache = get_smart_cache()
    cache.invalidate("slm:TestSLM35A", "disk format test")
    try:
        from scp.core.db_manager import db_exec
        ck = hashlib.sha256(b"slm:TestSLM35A::disk format test").hexdigest()
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    except Exception:
        pass

    resp = SLMResponse(
        question="disk format test", answer="x = 1", confidence=0.7,
        domain="test", reasoning="r", evidence={"source": "TestSource"},
        slm_name="TestSLM35A", processing_time=0.0,
    )
    slm_cache_set("TestSLM35A", "disk format test", resp, "TestSource")

    ck = hashlib.sha256(b"slm:TestSLM35A::disk format test").hexdigest()
    row = db_query_one("SELECT value_blob FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    assert row and row['value_blob'], "No disk cache row"
    blob = row['value_blob']
    if isinstance(blob, bytes):
        blob = blob.decode('utf-8')
    decoded = json.loads(blob)
    assert isinstance(decoded, dict), \
        f"Expected dict, got {type(decoded).__name__}: {str(decoded)[:100]}"
    assert decoded.get('answer') == 'x = 1', f"Wrong answer in dict: {decoded}"
    assert decoded.get('confidence') == 0.7, f"Wrong conf in dict: {decoded}"
    print(f"  [PASS] disk cache stores proper JSON dict (keys={list(decoded.keys())[:4]})")

    # Cleanup
    cache.invalidate("slm:TestSLM35A", "disk format test")
    try:
        from scp.core.db_manager import db_exec
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    except Exception:
        pass


def test_end_to_end_judge_cached_question():
    """Test 4: End-to-end judge() for previously-broken cached question."""
    from scp.runtime.judge import RealityJudge
    judge = RealityJudge()
    verdict = judge.judge("Thủ đô Việt Nam?", ai_answer="Hà Nội")
    assert verdict.confidence > 0, f"Zero confidence: {verdict.confidence}"
    assert "không có đủ dữ liệu" not in verdict.final_answer, \
        f"Got 'no data' response: {verdict.final_answer[:80]!r}"
    print(f"  [PASS] judge() → verdict={verdict.verdict}, conf={verdict.confidence:.3f}")


def test_legacy_string_entry_treated_as_miss():
    """Test 5: If disk cache has legacy broken string entry, slm_cache_get
    should return None (cache miss), not the broken string."""
    import time

    from scp.core.db_manager import db_exec
    from scp.core.smart_cache import slm_cache_get

    # Manually insert a broken string-repr entry (simulating pre-fix data)
    ck = hashlib.sha256(b"slm:LegacySLM::legacy broken question").hexdigest()
    broken_value = '"SLMResponse(question=\'legacy broken question\', answer=\'x = 1\', confidence=0.7, ...)"'
    try:
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
        db_exec(
            "INSERT INTO smart_cache_disk (cache_key, namespace, identifier, value, value_blob, ttl, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ck, "slm:LegacySLM", "legacy broken question", None,
             broken_value.encode('utf-8'), 999999, time.time())
        )
    except Exception as e:
        print(f"  (setup skipped: {e})")
        return

    # Clear in-memory cache to force disk read
    from scp.core.smart_cache import get_smart_cache
    get_smart_cache().clear()

    result = slm_cache_get("LegacySLM", "legacy broken question")
    assert result is None, \
        f"Expected None for legacy broken entry, got {type(result).__name__}: {str(result)[:80]}"

    print("  [PASS] legacy broken string entry → treated as cache miss (None)")

    # Cleanup
    try:
        db_exec("DELETE FROM smart_cache_disk WHERE cache_key = ?", (ck,))
    except Exception:
        pass


if __name__ == "__main__":
    print("=" * 70)
    print("[Task 35-A] SmartCache SLMResponse Serialization Fix — Verification")
    print("=" * 70)
    print()

    tests = [
        ("Cached question returns proper SLMResponse", test_cached_question_returns_proper_slmresponse),
        ("Cache roundtrip preserves SLMResponse data", test_cache_roundtrip_preserves_data),
        ("Disk cache stores proper JSON dict", test_disk_cache_stores_json_dict),
        ("End-to-end judge() for cached question", test_end_to_end_judge_cached_question),
        ("Legacy broken string entry treated as miss", test_legacy_string_entry_treated_as_miss),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"TEST: {name}")
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR] {e}")
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 70)
    print(f"Results: {passed}/{passed + failed} tests passed")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)
