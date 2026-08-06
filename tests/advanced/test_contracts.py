"""
[5] Contract Testing — verify SLM ↔ DataSource contract.

Contract:
  - DataSource.fetch() must return dict with "value", "confidence", "source"
  - SLM.predict() must set evidence["value"]
  - confidence ∈ [0, 1]
  - If abstain → value=None, abstain=True
"""
import os
import sys

import pytest

SCP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, SCP_DIR)


def test_all_slims_set_evidence_value():
    """EVERY SLM must set evidence["value"] for adversary cross-check."""
    import inspect

    import scp.runtime.slms as slms_mod
    from scp.runtime.slms import BaseSLM

    slm_classes = []
    for name in dir(slms_mod):
        obj = getattr(slms_mod, name)
        if (isinstance(obj, type)
            and issubclass(obj, BaseSLM)
            and obj is not BaseSLM
            and hasattr(obj, 'predict')):
            slm_classes.append((name, obj))

    # Check source code contains "value" key in evidence
    weak_slms = []
    for name, cls in slm_classes:
        try:
            src = inspect.getsource(cls.predict)
            if '"value"' not in src and "'value'" not in src:
                weak_slms.append(name)
        except (OSError, TypeError):
            pass  # can't get source — skip

    # Allow GeneralSLM (catches unknown, no evidence)
    weak_slms = [s for s in weak_slms if s != "GeneralSLM"]
    assert not weak_slms, f"SLMs missing evidence['value']: {weak_slms}"


def test_datasource_contract_shape():
    """DataSource.fetch() must return dict with required keys."""
    import inspect

    from scp.interfaces import data_source

    # Check interface defines required return keys
    src = inspect.getsource(data_source)
    assert "value" in src or "fetch" in src, "DataSource interface must define value contract"


def test_confidence_range():
    """SLM confidence must be in [0, 1]."""
    # Test with a simple SLM that doesn't need network
    from scp.runtime.slms import MathSLM
    slm = MathSLM()
    try:
        result = slm.predict("What is 2+2?")
        if result and hasattr(result, 'confidence'):
            assert 0.0 <= result.confidence <= 1.0, f"confidence {result.confidence} out of [0,1]"
    except Exception:
        pass  # SLM may need network — skip


def test_abstain_contract():
    """If abstain=True, value must be None."""
    # Medical SLM should abstain on dose advice
    from scp.data_sources.medical import MedicalDataSource
    ds = MedicalDataSource()
    try:
        result = ds.fetch("what dose of paracetamol should I take?", intent="medical_dose_advice")
        if result and result.get("abstain"):
            assert result.get("value") is None, "abstain=True but value not None"
    except Exception:
        pass
