"""
RealityJudge shared types — extracted to break circular import (Task 19-A).

JudgeVerdict and _AllowedByWatchlist live here so both judge.py and the
mixin modules (judgecore_mixin, judgeroute_mixin, judgebg_mixin) can import
the SAME class — preventing the isinstance() mismatch that occurred when
each mixin had its own copy.
"""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class JudgeVerdict:
    """Verdict từ Reality Judge."""
    question: str = ""
    slm_responses: list[dict] = field(default_factory=list)
    final_answer: str = ""
    confidence: float = 0.0
    verdict: str = "UNKNOWN"          # PASS / FAIL / CONFLICT / PARTIAL / UNKNOWN
    reasoning: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    domain: str = "unknown"
    cross_validation: dict[str, Any] = field(default_factory=dict)
    slm_scores: dict[str, Any] = field(default_factory=dict)
    reality_check: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    similar_errors: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# [V104.42 #AH] Internal signal — source passed watchlist check, proceed to INSERT
class _AllowedByWatchlist(Exception):
    pass
