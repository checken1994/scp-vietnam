"""SCP-META — Hội đồng phản biện 'Có cần làm không?' (Gà §10)."""
# [G3-CONSOLIDATE RE-17] Meta namespace status:
# - This file: SCPMeta council — 4 enums (MetaDecision, System3Decision,
#   ConsequenceLevel, CouncilDecision) + SCPMetaReview dataclass + SCPMeta class
#   with 3-system voting (should_we_answer / is_there_another_way /
#   evaluate_consequences) — 61 LOC, LIVE in /ask via judgecore_mixin.py:45.
# - NOT related to: scp/meta/meta.py (MetaCognitionEngine), scp/meta/meta_schema.py (DDL).
# - The 'meta' namespace contains 3 UNRELATED things:
#   1. meta.py — MetaCognitionEngine (5 sub-engines, 929 LOC)
#   2. scp_meta.py — SCPMeta council (4 enums + SCPMetaReview dataclass, 61 LOC)
#   3. meta_schema.py — DDL init (5 meta_* tables, 108 LOC)
# - No rename attempted (would break imports) — this marker documents reality.
from dataclasses import dataclass
from enum import Enum


class MetaDecision(str, Enum):
    ANSWER = "ANSWER"
    SKIP = "SKIP"
    RESEARCH = "RESEARCH"

class System3Decision(str, Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    SUGGEST_TOOL = "SUGGEST_TOOL"
    DEFER_HUMAN = "DEFER_HUMAN"

class ConsequenceLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class CouncilDecision(str, Enum):
    ANSWER = "ANSWER"
    SKIP = "SKIP"
    DEFER_HUMAN = "DEFER_HUMAN"

@dataclass
class SCPMetaReview:
    scp_meta: MetaDecision
    system3: System3Decision
    consequence: ConsequenceLevel
    council_decision: CouncilDecision
    reason: str = ""

class SCPMeta:
    """3 systems vote independently — break ảo幻觉 đồng thuận (Gà §20)."""
    def review(self, question: str, verdict: str) -> SCPMetaReview:
        meta = self._should_we_answer(question)
        alt = self._is_there_another_way(question)
        cons = self._evaluate_consequences(question, verdict)
        return SCPMetaReview(meta, alt, cons, self._vote(meta, alt, cons))

    def _should_we_answer(self, q: str) -> MetaDecision:
        if not q or not q.strip(): return MetaDecision.SKIP
        if len(q) < 5: return MetaDecision.SKIP
        return MetaDecision.ANSWER

    def _is_there_another_way(self, q: str) -> System3Decision:
        ql = q.lower()
        if any(k in ql for k in ["giá bitcoin", "tra cứu", "lookup", "tính"]): return System3Decision.DIRECT_ANSWER
        if any(k in ql for k in ["thuốc", "liều", "medical"]): return System3Decision.DEFER_HUMAN
        return System3Decision.DIRECT_ANSWER

    def _evaluate_consequences(self, q: str, v: str) -> ConsequenceLevel:
        ql = q.lower()
        if any(k in ql for k in ["thuốc", "medical", "đầu tư", "luật"]): return ConsequenceLevel.HIGH
        return ConsequenceLevel.LOW

    def _vote(self, meta, alt, cons) -> CouncilDecision:
        if meta == MetaDecision.SKIP: return CouncilDecision.SKIP
        if cons == ConsequenceLevel.HIGH or alt == System3Decision.DEFER_HUMAN: return CouncilDecision.DEFER_HUMAN
        return CouncilDecision.ANSWER
