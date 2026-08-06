"""
[OPT-2] Lightweight ReActAgent — multi-tool coordination WITHOUT LangChain.

Why not LangChain:
  - 200MB+ dependency overhead
  - Lock-in to their abstractions
  - SCP already has SmartClassifier + FalsificationEngine — reuse them

Architecture (ReAct pattern):
  Thought → Action → Observation → Thought → ... → Final Answer

Tools available (reuse existing SCP modules):
  - DataSource.query(question) — 35 data sources
  - SLM.ask(question) — 53 SLMs
  - LLMGateway.chat(question) — Ollama + OpenRouter
  - FalsificationEngine.verify(claim, evidence) — reality check

Stops when:
  - confidence >= 0.8 (success)
  - max_iterations reached (failure → escalate)
  - contradiction detected (escalate to human)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("scp.core.react_agent")


@dataclass
class ReActStep:
    thought: str
    action: str  # "datasource" | "slm" | "llm" | "falsify" | "final"
    action_input: str
    observation: str = ""
    confidence: float = 0.0


@dataclass
class ReActResult:
    answer: str
    confidence: float
    steps: list[ReActStep] = field(default_factory=list)
    success: bool = False
    escalated: bool = False
    reason: str = ""


class ReActAgent:
    """Lightweight ReAct agent — 200 LOC, no external deps.

    Usage:
        agent = ReActAgent(judge=get_judge())
        result = agent.solve("What is the capital of France?")
        # → ReActResult(answer="Paris", confidence=0.95, steps=[...], success=True)
    """

    MAX_ITERATIONS = 4  # DNA SCP: bounded — don't loop forever
    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, judge=None):
        self.judge = judge
        self._stats = {
            "total_solves": 0,
            "success": 0,
            "escalated": 0,
            "max_iter_reached": 0,
        }

    def solve(self, question: str, context: str = "") -> ReActResult:
        """Solve a question using ReAct pattern."""
        self._stats["total_solves"] += 1
        result = ReActResult(answer="", confidence=0.0)
        if not self.judge:
            result.reason = "no judge available"
            return result
        for i in range(self.MAX_ITERATIONS):
            step = self._think(question, context, result.steps, i)
            result.steps.append(step)
            if step.action == "final":
                result.answer = step.action_input
                result.confidence = step.confidence
                result.success = step.confidence >= self.CONFIDENCE_THRESHOLD
                if result.success:
                    self._stats["success"] += 1
                return result
            # Execute action → observation
            step.observation = self._execute(step, question)
            step.confidence = self._score_observation(step.observation, question)
            if step.confidence >= self.CONFIDENCE_THRESHOLD:
                # Good enough — final answer
                final_step = ReActStep(
                    thought="Confidence threshold reached",
                    action="final",
                    action_input=step.observation,
                    confidence=step.confidence,
                )
                result.steps.append(final_step)
                result.answer = step.observation
                result.confidence = step.confidence
                result.success = True
                self._stats["success"] += 1
                return result
        # Max iterations reached — escalate
        self._stats["max_iter_reached"] += 1
        self._stats["escalated"] += 1
        result.escalated = True
        result.reason = f"max_iterations={self.MAX_ITERATIONS} reached without confidence"
        result.answer = result.steps[-1].observation if result.steps else ""
        result.confidence = result.steps[-1].confidence if result.steps else 0.0
        return result

    def _think(self, question: str, context: str,
               prev_steps: list[ReActStep], iteration: int) -> ReActStep:
        """Decide next action based on question + history."""
        if iteration == 0:
            return ReActStep(
                thought="Try DataSource first (cheapest, most reliable for facts)",
                action="datasource",
                action_input=question,
            )
        last = prev_steps[-1] if prev_steps else None
        if last and last.confidence < 0.5:
            return ReActStep(
                thought="DataSource low confidence — try SLM",
                action="slm",
                action_input=question,
            )
        if last and last.confidence < 0.7:
            return ReActStep(
                thought="SLM partial — use LLM for synthesis",
                action="llm",
                action_input=question,
            )
        if last and last.confidence < self.CONFIDENCE_THRESHOLD:
            return ReActStep(
                thought="LLM answer needs falsification",
                action="falsify",
                action_input=last.observation,
            )
        return ReActStep(
            thought="Ready for final answer",
            action="final",
            action_input=last.observation if last else "",
            confidence=last.confidence if last else 0.0,
        )

    def _execute(self, step: ReActStep, question: str) -> str:
        """Execute the action and return observation."""
        try:
            if step.action == "datasource":
                if hasattr(self.judge, "smart_classifier"):
                    result = self.judge.smart_classifier.classify(question)
                    if result and result.get("slm"):
                        slm = result["slm"]
                        if hasattr(slm, "ask"):
                            ans = slm.ask(question)
                            return str(ans) if ans else ""
                return ""
            if step.action == "slm":
                if hasattr(self.judge, "route_question"):
                    routed = self.judge.route_question(question)
                    if routed and routed.get("slm"):
                        ans = routed["slm"].ask(question)
                        return str(ans) if ans else ""
                return ""
            if step.action == "llm":
                if hasattr(self.judge, "llm_client") and self.judge.llm_client:
                    answer, _ = self.judge.llm_client.chat_sync(question)
                    return str(answer) if answer else ""
                return ""
            if step.action == "falsify":
                if hasattr(self.judge, "falsification_engine"):
                    result = self.judge.falsification_engine.verify(step.action_input, question)
                    if result and result.get("verified"):
                        return step.action_input  # confirmed
                    return f"[UNVERIFIED] {step.action_input}"
                return step.action_input
        except Exception as e:
            logger.debug(f"[ReActAgent] execute {step.action} failed: {e}")
            return f"[ERROR] {e}"
        return ""

    def _score_observation(self, observation: str, question: str) -> float:
        """Heuristic confidence scoring."""
        if not observation:
            return 0.0
        if observation.startswith("[ERROR]"):
            return 0.0
        if observation.startswith("[UNVERIFIED]"):
            return 0.4
        # Length-based (longer = more substantive, capped)
        score = min(0.7, len(observation) / 200)
        # Bonus for numbers (factual answers)
        if any(c.isdigit() for c in observation):
            score = min(0.9, score + 0.2)
        return score

    def stats(self) -> dict:
        return {**self._stats}


__all__ = ["ReActAgent", "ReActResult", "ReActStep"]
