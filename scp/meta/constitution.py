"""
SCP V4 FORTRESS — AI Defense Citadel
Copyright (c) 2026 [Author: Minh / SCP V4 Project]
All rights reserved.

File: constitution.py
Purpose: V4 Constitution — the 10 inviolable principles that govern every
         decision in the FORTRESS stack. The Constitution is loaded once
         at boot and consulted by Governance, PersistenceAuthority and
         InvariantChecker. No code path may override a principle.
"""


from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PrincipleId(str, Enum):
    ACCURACY = "accuracy"
    TRANSPARENCY = "transparency"
    SAFETY = "safety"
    ACCOUNTABILITY = "accountability"
    PRIVACY = "privacy"
    FAIRNESS = "fairness"
    ROBUSTNESS = "robustness"
    HUMAN_OVERSIGHT = "human_oversight"
    NO_HALLUCINATION = "no_hallucination"
    EVIDENCE_FIRST = "evidence_first"


@dataclass(frozen=True)
class Principle:
    """A single constitutional principle."""
    id: PrincipleId
    name: str
    description: str
    weight: float = 1.0  # relative weight in conflict resolution
    inviolable: bool = True
    # Operational hint: which governance action to take when violated.
    default_action: str = "KILL"

    def to_dict(self) -> dict:
        return {
            "id": self.id.value,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "inviolable": self.inviolable,
            "default_action": self.default_action,
        }


# -------------------------------------------------------------------- #
# The 10 principles of the V4 Constitution
# -------------------------------------------------------------------- #
_PRINCIPLES: list[Principle] = [
    Principle(
        PrincipleId.ACCURACY, "Accuracy",
        "Every factual claim must be verifiable against reality; reality outranks model opinion.",
        weight=1.2, default_action="ESCALATE"  # [FIX-P1] was KILL → ESCALATE (Gà §5),
    ),
    Principle(
        PrincipleId.TRANSPARENCY, "Transparency",
        "The pipeline must expose its reasoning, evidence and confidence at every step.",
        weight=1.0, default_action="ESCALATE",
    ),
    Principle(
        PrincipleId.SAFETY, "Safety",
        "No output may cause foreseeable harm to humans, property or rights.",
        weight=1.5, default_action="KILL",
    ),
    Principle(
        PrincipleId.ACCOUNTABILITY, "Accountability",
        "Every decision must trace to a named, auditable source — no anonymous overrides.",
        weight=1.1, default_action="ESCALATE",
    ),
    Principle(
        PrincipleId.PRIVACY, "Privacy",
        "PII / secrets must be redacted before logging, persistence or external calls.",
        weight=1.3, default_action="KILL",
    ),
    Principle(
        PrincipleId.FAIRNESS, "Fairness",
        "Outputs must not discriminate against protected groups without justification.",
        weight=1.1, default_action="ESCALATE",
    ),
    Principle(
        PrincipleId.ROBUSTNESS, "Robustness",
        "The system must degrade gracefully under adversarial input and partial failure.",
        weight=1.0, default_action="ESCALATE",
    ),
    Principle(
        PrincipleId.HUMAN_OVERSIGHT, "Human Oversight",
        "High-stakes decisions require an explicit human approval gate.",
        weight=1.2, default_action="ESCALATE",
    ),
    Principle(
        PrincipleId.NO_HALLUCINATION, "No Hallucination",
        "The system must abstain rather than fabricate sources, citations or facts.",
<<<<<<< HEAD
        weight=1.4, default_action="ESCALATE",
=======
        weight=1.4, default_action="KILL",
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
    ),
    Principle(
        PrincipleId.EVIDENCE_FIRST, "Evidence First",
        "Belief follows evidence; never the reverse. Missing evidence triggers abstention.",
        weight=1.3, default_action="ESCALATE",
    ),
]


class Constitution:
    """Holds the 10 principles and offers lookup / violation helpers."""

    def __init__(self, principles: Optional[list[Principle]] = None) -> None:
        self._principles: list[Principle] = list(principles) if principles else list(_PRINCIPLES)
        self._by_id: dict[PrincipleId, Principle] = {p.id: p for p in self._principles}

    # ---------------------------------------------------------------- #
    def principles(self) -> list[Principle]:
        return list(self._principles)

    def get(self, pid: PrincipleId) -> Principle:
        if pid not in self._by_id:
            raise KeyError(f"Unknown principle: {pid}")
        return self._by_id[pid]

    def ids(self) -> list[PrincipleId]:
        return list(self._by_id.keys())

    def __len__(self) -> int:
        return len(self._principles)

    def __contains__(self, pid: object) -> bool:
        return isinstance(pid, PrincipleId) and pid in self._by_id

    # ---------------------------------------------------------------- #
    def violation_severity(self, pid: PrincipleId) -> float:
        """Return the effective weight used to rank violations."""
        return self.get(pid).weight

    def to_dict(self) -> dict:
        return {
            "version": "4.0.0",
            "codename": "FORTRESS",
            "principle_count": len(self._principles),
            "principles": [p.to_dict() for p in self._principles],
        }

    def summary(self) -> str:
        lines = [f"SCP V4 FORTRESS Constitution — {len(self._principles)} principles"]
        for p in self._principles:
            lines.append(f"  [{p.id.value:>18}] {p.name}  (w={p.weight}, act={p.default_action})")
        return "\n".join(lines)


_DEFAULT_CONSTITUTION: Optional[Constitution] = None


def get_default_constitution() -> Constitution:
    global _DEFAULT_CONSTITUTION
    if _DEFAULT_CONSTITUTION is None:
        _DEFAULT_CONSTITUTION = Constitution()
    return _DEFAULT_CONSTITUTION


__all__ = [
    "PrincipleId",
    "Principle",
    "Constitution",
    "get_default_constitution",
]
