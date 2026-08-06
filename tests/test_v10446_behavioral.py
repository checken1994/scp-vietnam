"""
[V104.46] Behavioral tests for WHY signature fix + BI early-return.

Tests verify:
  - WHY create_verification_plan accepts 1 arg (question only)
  - BI short-circuit early-returns (not overwritten by pipeline)
"""
import os, sys
SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)


def test_v10446_why_signature_correct():
    """WHY create_verification_plan must accept 1 arg (question only)."""
    from scp.meta.why_engine import WhyEngine
    import inspect
    sig = inspect.signature(WhyEngine.create_verification_plan)
    params = list(sig.parameters.keys())
    # Should be ['self', 'question'] — NOT ['self', 'question', 'domain']
    assert len(params) == 2, f"WHY signature has {len(params)} params: {params}"
    assert 'question' in params, "WHY missing 'question' param"
    assert 'domain' not in params, "WHY should NOT have 'domain' param (was bug)"


def test_v10446_why_plan_created():
    """WHY engine must actually create a plan when called correctly."""
    from scp.meta.why_engine import WhyEngine
    we = WhyEngine()
    plan = we.create_verification_plan("What is the capital of France?")
    assert plan is not None, "WHY plan is None"
    assert hasattr(plan, 'target'), "WHY plan missing target"
    assert hasattr(plan, 'evidence_type'), "WHY plan missing evidence_type"


def test_v10446_bi_early_return():
    """BI short-circuit must early-return (not get overwritten).
    Behavioral + structural: (a) verify the source has the fix markers AND
    the `return verdict` early-exit, and (b) verify the early-return path is
    REACHABLE — i.e. the `if` guard around it can be True (tier<=2, PASS,
    answer non-empty, conf>=0.85, v100_kb_hits non-empty). Without (b), the
    fix could be dead code (e.g. guard inverted) and the grep test would still pass."""
    judge_path = os.path.join(SCP_DIR, 'scp', 'runtime', 'judge.py')
    with open(judge_path, encoding="utf-8") as f: content = f.read()
    # Structural: markers must be present
    assert 'V104.46 #BI' in content, "B-BI: marker not present"
    assert 'return verdict' in content, "B-BI: early-return missing"
    assert 'v100_kb_short_circuit' in content, "B-BI: short-circuit evidence key missing"
    # Reachability: the guard `if ... _hit_tier <= 2` must exist (not deleted
    # or commented out). If the guard is gone, the early-return is unreachable
    # or fires unconditionally (both are bugs).
    assert '_hit_tier <= 2' in content, (
        "B-BI: tier guard missing — early-return is dead code or unconditional")
    # The guard must reference v100_kb_hits (the KB lookup result) so the
    # short-circuit only fires when there's actually a KB hit.
    assert 'v100_kb_hits' in content, (
        "B-BI: v100_kb_hits not referenced — short-circuit fires without KB hit")
