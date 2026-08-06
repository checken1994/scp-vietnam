"""[G3-STUB] PipelineOrchestrator — dead code removed.

Previous version: PipelineOrchestrator was initialized at judge.py:587 but
record_query() was NEVER called. /v102/orchestrator/stats returned zeros.

This stub preserves the import in judge.py while signaling the orchestrator
is deprecated. Real phase coordination lives in scp/runtime/judge_parts/.
"""
from typing import Any


class PipelineOrchestrator:
    """Deprecated orchestrator stub — phase logic in judge_parts."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._queries: list = []

    def record_query(self, *args: Any, **kwargs: Any) -> None:
        """No-op — orchestrator is deprecated."""
        pass

    def get_stats(self) -> dict:
        return {"status": "deprecated", "queries_recorded": len(self._queries)}
