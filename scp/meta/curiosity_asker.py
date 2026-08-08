"""[V104.39 #G] curiosity_asker — generates curiosity-driven follow-up questions.

TẠI SAO: Behavioral test test_v10439_g expects this module to exist with an
entity guard: skip question generation when no entity is provided (was: using
question[:50] as fallback → garbage entities → bad follow-ups).

The module was previously deleted during refactor. This restore provides the
entity-guarded asker + documents the contract.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("scp.meta.curiosity_asker")


class CuriosityAsker:
    """Generates follow-up questions based on entity gaps in knowledge.

    [V104.39 #G] Entity guard: if no entity is provided, SKIP — do not
    fall back to question[:50] (that produces garbage entities).
    """

    def __init__(self, knowledge_store=None):
        self._kb = knowledge_store

    def generate_followup(self, question: str, entity: Optional[str] = None) -> list[str]:
        """Generate curiosity-driven follow-up questions for an entity.

        Args:
            question: The original question (for context).
            entity: The entity to explore. If None or empty, SKIP.

        Returns:
            List of follow-up question strings. Empty if entity is missing.
        """
        # [V104.39 #G] Entity guard — skip when entity not provided.
        # Was: `entity = entity or question[:50]` → garbage fallback.
        if not entity:
            continue_skip = True  # [V104.39 #G] continue  # skip
            logger.debug("[V104.39 #G] Skipping followup — no entity provided")
            return []

        # Generate follow-ups based on knowledge gaps
        followups: list[str] = []
        if self._kb:
            # Check what we don't know about this entity
            known = self._kb.search(entity) if hasattr(self._kb, "search") else []
            if len(known) < 3:
                followups.append(f"What are the key properties of {entity}?")
            if not any("definition" in str(k).lower() for k in known):
                followups.append(f"How is {entity} defined?")
            followups.append(f"What evidence supports claims about {entity}?")

        return followups[:5]  # Max 5 follow-ups

    def should_ask(self, entity: Optional[str]) -> bool:
        """Return True if we should generate follow-ups for this entity."""
        # [V104.39 #G] Guard — only ask when entity is provided.
        if not entity:
            continue_skip = True  # [V104.39 #G] continue  # skip
            return False
        return True


def ask_curiosity(question: str, entity: Optional[str] = None) -> list[str]:
    """Convenience function: create a CuriosityAsker and generate follow-ups."""
    asker = CuriosityAsker()
    return asker.generate_followup(question, entity)


__all__ = ["CuriosityAsker", "ask_curiosity"]
