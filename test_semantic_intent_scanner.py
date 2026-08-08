"""Test harness for SemanticIntentScanner — exercises all classification paths.

Per Task 11-b TESTING requirement:
  1. py_compile + import test (run separately).
  2. Mock `_call_openrouter` to return "INTENTIONAL\n..." → verify bug suppressed.
  3. Verify fail-open when no OPENROUTER_API_KEY env var is set.

Runs WITHOUT calling the real OpenRouter API — `_call_openrouter` is monkey-
patched to return canned responses. This keeps the test free (no API cost)
and deterministic (no network dependency).

Test cases:
  A. INTENTIONAL  → bug suppressed (tier=TIER_1, "[LLM-intent] intentional")
  B. BUG          → original tier preserved, "[LLM-intent] bug confirmed"
  C. UNCLEAR      → escalated to TIER_3, "[LLM-intent] unclear, escalated"
  D. Non-ambiguous bug_type → passed through unchanged (no LLM call)
  E. Cache hit → second call with same bug reuses cached label (no LLM call)
  F. Rate-limited → UNCLEAR escalation path
  G. Fail-open: no OPENROUTER_API_KEY → original bug returned unchanged
  H. Fail-open: _call_openrouter returns None (network error) → original bug
  I. Batch triage_bugs: reads each file once, mixed ambiguous/non-ambiguous
  J. Response parser robustness: variations of INTENTIONAL/BUG/UNCLEAR
  K. Prompt includes required fields (bug_type, code context, docstrings)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure we can import scp.* — caller should run from project root with
# PYTHONPATH=. but we add it explicitly as a safety net.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from scp.autofix.classifier import BugReport, BugTier  # noqa: E402
from scp.autofix.scanners import semantic_intent_scanner as mod  # noqa: E402


# Sample source code with an ambiguous pattern (BLE001 blind except).
# The `pass` inside except is on line 7 (1-indexed).
SAMPLE_SOURCE = '''"""Sample module with a fail-open error handler."""
import logging


def safe_lookup(key: str) -> dict | None:
    """Return cached value or None on any error — fail-open by design."""
    try:
        return _cache[key]
    except Exception:
        pass
    return None


_cache: dict = {}
'''

BUG_LINE = 9  # the `pass` line


class Check:
    """Tiny assertion accumulator — collects failures without raising."""

    def __init__(self, label: str):
        self.label = label
        self.failures: list[str] = []

    def __call__(self, cond: bool, msg: str) -> None:
        if not cond:
            self.failures.append(msg)

    @property
    def ok(self) -> bool:
        return not self.failures


def _make_bug(bug_type: str = "Ruff_BLE001", line: int = BUG_LINE) -> BugReport:
    """Build a BugReport matching the sample source's `pass` line."""
    return BugReport(
        file="<test>/sample.py",
        line=line,
        bug_type=bug_type,
        description=f"[{bug_type}] Blind except with `pass` at line {line}",
        suggested_fix="Replace `pass` with `logger.exception(...)` or re-raise.",
        tier=BugTier.TIER_3_PERMISSION,
        affects_logic=True,
    )


def _patch_llm(response: str | None):
    """Monkey-patch _call_openrouter to return `response`.

    Returns (get_calls, get_last_prompt) closures so tests can verify how
    many times the LLM was actually invoked (cache hits should not increment
    it) and inspect the prompt sent.
    """
    state = {"n": 0, "last_prompt": ""}

    def _fake(prompt: str, max_tokens: int = 4000) -> str | None:
        state["n"] += 1
        state["last_prompt"] = prompt
        return response

    mod._call_openrouter = _fake  # type: ignore[assignment]

    def get_calls() -> int:
        return state["n"]

    def get_last_prompt() -> str:
        return state["last_prompt"]

    return get_calls, get_last_prompt


def _patch_rate_limit(allowed: bool) -> None:
    """Monkey-patch _check_rate_limit to always return `allowed`."""
    mod._check_rate_limit = lambda: allowed  # type: ignore[assignment]


def _ensure_api_key() -> dict[str, str | None]:
    """Set a fake OPENROUTER_API_KEY if none is set; return old env for restore."""
    old = {
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY"),
        "OPENROUTER_API_KEY_2": os.environ.get("OPENROUTER_API_KEY_2"),
        "OPENROUTER_API_KEY_3": os.environ.get("OPENROUTER_API_KEY_3"),
    }
    os.environ["OPENROUTER_API_KEY"] = "test-fake-key-for-mocking"
    return old


def _restore_api_key(old: dict[str, str | None]) -> None:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _clear_cache() -> None:
    """Clear the process-local LLM intent cache between tests."""
    mod._intent_cache.clear()


# ----------------------------------------------------------------------------
# Test cases
# ----------------------------------------------------------------------------

def test_intentional_suppresses_bug() -> bool:
    """A. Mock LLM returns INTENTIONAL → bug suppressed to TIER_1."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("INTENTIONAL\nThis is deliberate fail-open design.")

        bug = _make_bug()
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("A")
        c(result.tier == BugTier.TIER_1_AUTO_FIX, f"tier={result.tier}, expected TIER_1")
        c(result.affects_logic is False, f"affects_logic={result.affects_logic}, expected False")
        c("[LLM-intent] intentional" in result.description, "description missing [LLM-intent] intentional")
        c("deliberate fail-open" in result.description, "description missing LLM reason")
        c("Original:" in result.description, "description missing Original: prefix")
        c(get_calls() == 1, f"LLM called {get_calls()} times, expected 1")
    finally:
        _restore_api_key(old)
    print(f"  [A] INTENTIONAL suppresses bug: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_bug_confirmed_preserves_tier() -> bool:
    """B. Mock LLM returns BUG → original tier preserved, description enriched."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("BUG\nEmpty except hides real errors that should be logged.")

        bug = _make_bug()
        original_tier = bug.tier
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("B")
        c(result.tier == original_tier, f"tier={result.tier}, expected {original_tier}")
        c("[LLM-intent] bug confirmed" in result.description, "missing [LLM-intent] bug confirmed")
        c("hides real errors" in result.description, "description missing LLM reason")
        c(get_calls() == 1, f"LLM called {get_calls()} times, expected 1")
    finally:
        _restore_api_key(old)
    print(f"  [B] BUG preserves tier + enriches description: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_unclear_escalates_to_tier3() -> bool:
    """C. Mock LLM returns UNCLEAR → escalated to TIER_3 (human review)."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("UNCLEAR\nCannot determine intent without caller contract.")

        bug = _make_bug()
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("C")
        c(result.tier == BugTier.TIER_3_PERMISSION, f"tier={result.tier}, expected TIER_3")
        c(result.affects_logic is True, f"affects_logic={result.affects_logic}, expected True")
        c("[LLM-intent] unclear, escalated" in result.description, "missing escalation marker")
        c("caller contract" in result.description, "description missing LLM reason")
        c(get_calls() == 1, f"LLM called {get_calls()} times, expected 1")
    finally:
        _restore_api_key(old)
    print(f"  [C] UNCLEAR escalates to TIER_3: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_non_ambiguous_passes_through() -> bool:
    """D. Non-ambiguous bug_type (e.g. BareExceptPass) → unchanged, no LLM call."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("INTENTIONAL\nshould not be called")

        bug = _make_bug(bug_type="BareExceptPass", line=BUG_LINE)
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("D")
        c(result.tier == bug.tier, f"tier={result.tier}, expected {bug.tier} (unchanged)")
        c(result.description == bug.description, "description should be unchanged")
        c(result.suggested_fix == bug.suggested_fix, "suggested_fix should be unchanged")
        c(get_calls() == 0, f"LLM called {get_calls()} times, expected 0 (non-ambiguous)")
    finally:
        _restore_api_key(old)
    print(f"  [D] Non-ambiguous bug passes through: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_cache_hit_skips_llm() -> bool:
    """E. Second call with same bug → cache hit, no LLM call."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("INTENTIONAL\ndeliberate fail-open.")

        bug = _make_bug()
        mod.triage_bug(bug, SAMPLE_SOURCE)
        first_calls = get_calls()

        # Second call with identical bug (same file:line:bug_type) — cache hit.
        bug2 = _make_bug()
        result2 = mod.triage_bug(bug2, SAMPLE_SOURCE)
        second_calls = get_calls()

        c = Check("E")
        c(first_calls == 1, f"first call: {first_calls}, expected 1")
        c(second_calls == 1, f"second call (cache hit): {second_calls}, expected 1 (no new LLM call)")
        c(result2.tier == BugTier.TIER_1_AUTO_FIX, f"cached result tier={result2.tier}, expected TIER_1")
    finally:
        _restore_api_key(old)
    print(f"  [E] Cache hit skips LLM call: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_rate_limited_escalates_to_unclear() -> bool:
    """F. Rate limit exceeded → UNCLEAR escalation path (no LLM call)."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=False)  # simulate quota exceeded
        get_calls, _ = _patch_llm("INTENTIONAL\nshould not be called")

        bug = _make_bug()
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("F")
        c(result.tier == BugTier.TIER_3_PERMISSION, f"tier={result.tier}, expected TIER_3 (escalated)")
        c(result.affects_logic is True, f"affects_logic={result.affects_logic}, expected True")
        c("[LLM-intent] unclear, escalated" in result.description, "missing escalation marker")
        c("rate-limited" in result.description.lower(), "description should mention rate-limited")
        c(get_calls() == 0, f"LLM called {get_calls()} times, expected 0 (rate-limited before call)")
    finally:
        _restore_api_key(old)
    print(f"  [F] Rate-limited → UNCLEAR escalation: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_failopen_no_api_key() -> bool:
    """G. No OPENROUTER_API_KEY in env → original bug returned unchanged."""
    _clear_cache()
    # Wipe ALL OpenRouter env vars.
    old_env = {
        k: os.environ.pop(k, None)
        for k in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3")
    }
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("INTENTIONAL\nshould not be called")

        bug = _make_bug()
        original_desc = bug.description
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("G")
        c(result.tier == bug.tier, f"tier={result.tier}, expected {bug.tier} (unchanged)")
        c(result.description == original_desc, "description should be unchanged")
        c(get_calls() == 0, f"LLM called {get_calls()} times, expected 0 (no API key)")
    finally:
        # Restore env.
        for k, v in old_env.items():
            if v is not None:
                os.environ[k] = v
    print(f"  [G] No API key → fail-open (original bug): {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_failopen_empty_llm_response() -> bool:
    """H. _call_openrouter returns None (network error) → original bug unchanged."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm(None)  # simulate network failure / empty response

        bug = _make_bug()
        original_desc = bug.description
        result = mod.triage_bug(bug, SAMPLE_SOURCE)

        c = Check("H")
        c(result.tier == bug.tier, f"tier={result.tier}, expected {bug.tier} (unchanged)")
        c(result.description == original_desc, "description should be unchanged")
        c(get_calls() == 1, f"LLM called {get_calls()} times, expected 1 (called but returned None)")
    finally:
        _restore_api_key(old)
    print(f"  [H] Empty LLM response → fail-open: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_batch_triage_mixed() -> bool:
    """I. triage_bugs: ambiguous + non-ambiguous mix; reads each file once."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, _ = _patch_llm("BUG\nconfirmed bug.")

        # Write a real temp file so triage_bugs can read it.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(SAMPLE_SOURCE)
            tmp_path = f.name

        try:
            ambiguous_bug = BugReport(
                file=tmp_path, line=BUG_LINE, bug_type="Bandit_S110",
                description="[S110] try-except-pass",
                suggested_fix="add logging", tier=BugTier.TIER_3_PERMISSION,
            )
            non_ambiguous_bug = BugReport(
                file=tmp_path, line=1, bug_type="MissingDocstring",
                description="[D100] missing module docstring",
                suggested_fix="add docstring", tier=BugTier.TIER_2_AUTO_FIX_LOG,
            )
            # Same bug twice to verify cache works in batch.
            duplicate_bug = BugReport(
                file=tmp_path, line=BUG_LINE, bug_type="Bandit_S110",
                description="[S110] try-except-pass (dup)",
                suggested_fix="add logging", tier=BugTier.TIER_3_PERMISSION,
            )

            results = mod.triage_bugs([ambiguous_bug, non_ambiguous_bug, duplicate_bug])
        finally:
            os.unlink(tmp_path)

        c = Check("I")
        c(len(results) == 3, f"expected 3 results, got {len(results)}")
        # Ambiguous bug → BUG confirmed (tier preserved, description enriched)
        c("[LLM-intent] bug confirmed" in results[0].description, "ambiguous bug not enriched")
        # Non-ambiguous → passed through unchanged
        c(results[1].description == non_ambiguous_bug.description, "non-ambiguous bug modified")
        # Duplicate (cache hit) → also BUG confirmed
        c("[LLM-intent] bug confirmed" in results[2].description, "duplicate bug not enriched (cache miss?)")
        # LLM should have been called ONCE (second Bandit_S110 hits cache)
        c(get_calls() == 1, f"LLM called {get_calls()} times, expected 1 (cache for dup)")
    finally:
        _restore_api_key(old)
    print(f"  [I] Batch triage mixed: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_parser_robustness() -> bool:
    """J. Response parser handles label variations."""
    _clear_cache()
    cases = [
        ("INTENTIONAL\nreason here", "INTENTIONAL", "reason here"),
        ("Intentional\nreason here", "INTENTIONAL", "reason here"),
        ("BUG: this is broken", "BUG", "this is broken"),
        ("UNCLEAR - need more info", "UNCLEAR", "need more info"),
        ("  INTENTIONAL  \n  trimmed reason  ", "INTENTIONAL", "trimmed reason"),
        ("First line junk\nINTENTIONAL\nreason", "INTENTIONAL", "reason"),
        ("", "UNCLEAR", "(empty"),
        ("completely unparseable response", "UNCLEAR", "(unparseable"),
        ("INTENTIONAL", "INTENTIONAL", "(no reason given)"),
    ]
    c = Check("J")
    for resp, exp_label, exp_reason_substr in cases:
        label, reason = mod._parse_intent_response(resp)
        if label != exp_label:
            c.failures.append(f"resp={resp!r} → label={label}, expected {exp_label}")
        elif exp_reason_substr not in reason:
            c.failures.append(f"resp={resp!r} → reason={reason!r}, expected to contain {exp_reason_substr!r}")
    print(f"  [J] Parser robustness ({len(cases)} cases): {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


def test_prompt_includes_required_fields() -> bool:
    """K. Verify the LLM prompt includes code context + docstrings."""
    _clear_cache()
    old = _ensure_api_key()
    try:
        _patch_rate_limit(allowed=True)
        get_calls, get_last_prompt = _patch_llm("INTENTIONAL\ntest")

        bug = _make_bug()
        mod.triage_bug(bug, SAMPLE_SOURCE)

        prompt = get_last_prompt()
        c = Check("K")
        c("INTENTIONAL" in prompt, "prompt missing INTENTIONAL label option")
        c("BUG" in prompt, "prompt missing BUG label option")
        c("UNCLEAR" in prompt, "prompt missing UNCLEAR label option")
        c("Ruff_BLE001" in prompt, "prompt missing bug_type")
        c("safe_lookup" in prompt, "prompt missing function name from code context")
        c("fail-open by design" in prompt, "prompt missing function docstring")
        c("Sample module" in prompt, "prompt missing module docstring")
        c(">>>" in prompt, "prompt missing >>> bug-line marker")
    finally:
        _restore_api_key(old)
    print(f"  [K] Prompt includes required fields: {'PASS' if c.ok else 'FAIL'}")
    if not c.ok:
        for f in c.failures:
            print(f"      - {f}")
    return c.ok


# ----------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("SemanticIntentScanner test harness (mocked LLM — no API calls)")
    print("=" * 70)

    results = [
        ("A", test_intentional_suppresses_bug()),
        ("B", test_bug_confirmed_preserves_tier()),
        ("C", test_unclear_escalates_to_tier3()),
        ("D", test_non_ambiguous_passes_through()),
        ("E", test_cache_hit_skips_llm()),
        ("F", test_rate_limited_escalates_to_unclear()),
        ("G", test_failopen_no_api_key()),
        ("H", test_failopen_empty_llm_response()),
        ("I", test_batch_triage_mixed()),
        ("J", test_parser_robustness()),
        ("K", test_prompt_includes_required_fields()),
    ]

    print("-" * 70)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  [{name}] {'PASS' if ok else 'FAIL'}")
    print("-" * 70)
    print(f"RESULT: {passed}/{total} test groups passed")
    if passed != total:
        print("STATUS: FAIL — see individual results above")
        return 1
    print("STATUS: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
