"""
SCP V107 — LogicalAuditorEngine
================================
Kiểm tra lỗi logic sâu — ngụy biện, mâu thuẫn nội tại, lỗ hổng suy luận.

Sử dụng LLM (GLM 5.2 qua OpenRouter) với reasoning_effort=max để:
  1. Phát hiện mâu thuẫn nội tại (Internal Consistency)
  2. Phát hiện ngụy biện logic (Logical Fallacies)
  3. Kiểm tra chuỗi nhân quả (Causal Chain)
  4. Đánh giá tính đầy đủ (Completeness)
  5. Đánh giá khả năng bác bỏ (Falsifiability)

Output: LogicalAuditResult với verdict + issues + falsification_attempt

Integration:
  Phase 6.5 (SAU ClaimExtractor, TRƯỚC FalsificationEngine):
    ClaimExtractor → LogicalAuditor → FalsificationEngine → Governance

  Nếu LogicalAuditor phát hiện critical issue → downgrade confidence
  Nếu phát hiện unfalsifiable → verdict = UNREFUTED_IN_CURRENT_SCOPE
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.meta.logical_auditor")


# ============================================================
# LOGICAL AUDITOR PROMPT
# ============================================================

LOGICAL_AUDITOR_PROMPT = """Bạn là một Logical Auditor – chuyên gia kiểm tra lỗi logic sâu trong các hệ thống suy luận của AI. Nhiệm vụ của bạn là phát hiện các lỗi logic, ngụy biện, mâu thuẫn nội tại và các lỗ hổng suy luận trong bất kỳ chuỗi lập luận nào.

Phân tích đoạn văn bản dưới đây theo 5 tiêu chí:

1. NHẤT QUÁN NỘI TẠI (Internal Consistency): mâu thuẫn, giả định ngầm, kết luận không suy ra từ tiền đề
2. NGỤY BIỆN LOGIC (Logical Fallacies): khái quát hóa vội, đánh tráo khái niệm, nguyên nhân giả, bằng chứng thiếu
3. CHUỖI NHÂN QUẢ (Causal Chain): trật tự logic, bước nhảy logic
4. TÍNH ĐẦY ĐỦ (Completeness): khía cạnh bỏ qua, giả định quá mạnh
5. KHẢ NĂNG BÁC BỎ (Falsifiability): điều gì làm kết luận sai? Không bác bỏ được → UNREFUTED_IN_CURRENT_SCOPE

Trả về JSON hợp lệ với format:
{"verdict":"PASS|FAIL|UNKNOWN|UNREFUTED_IN_CURRENT_SCOPE","confidence":0.0-1.0,"issues":[{"type":"internal_contradiction|fallacy|logical_leap|missing_premise|unfalsifiable","description":"...","severity":"critical|major|minor","location":"..."}],"falsification_attempt":"...","recommendation":"..."}

VĂN BẢN CẦN KIỂM TRA:
---
__TEXT__
---

Chỉ trả về JSON, không thêm gì khác."""


@dataclass
class LogicalIssue:
    """1 lỗi logic phát hiện."""
    type: str  # internal_contradiction | fallacy | logical_leap | missing_premise | unfalsifiable
    description: str
    severity: str  # critical | major | minor
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "description": self.description,
            "severity": self.severity,
            "location": self.location,
        }


@dataclass
class LogicalAuditResult:
    """Kết quả logical audit."""
    verdict: str = "UNREFUTED_IN_CURRENT_SCOPE"
    confidence: float = 0.5
    issues: list[LogicalIssue] = field(default_factory=list)
    falsification_attempt: str = ""
    recommendation: str = ""
    raw_response: str = ""
    elapsed_ms: float = 0.0
    model_used: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "issues": [i.to_dict() for i in self.issues],
            "falsification_attempt": self.falsification_attempt,
            "recommendation": self.recommendation,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "model_used": self.model_used,
            "error": self.error,
        }


class LogicalAuditorEngine:
    """Kiểm tra lỗi logic sâu bằng LLM với reasoning_effort=max.

    Naming convention: <Purpose>Engine (world standard).

    Sử dụng GLM 5.2 (qua OpenRouter) với deep thinking để:
      - Phát hiện ngụy biện logic
      - Kiểm tra nhất quán nội tại
      - Truy vết chuỗi nhân quả
      - Đánh giá falsifiability

    Pipeline integration:
      Phase 6.5: Sau ClaimExtractor, trước FalsificationEngine
      Nếu critical issue → confidence *= 0.3
      Nếu unfalsifiable → verdict = UNREFUTED_IN_CURRENT_SCOPE
    """

    def __init__(self):
        self._api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = "https://openrouter.ai/api/v1/chat/completions"
        self._model = "z-ai/glm-5.2"  # GLM 5.2 — deep reasoning
        self._fallback_model = "meta-llama/llama-3.3-70b-instruct"
        self._timeout = 30
        self._stats = {
            "total_audits": 0,
            "total_issues_found": 0,
            "total_critical": 0,
            "total_pass": 0,  # nosec B105 — stats counter key, not a password
            "total_fail": 0,
            "total_unrefuted": 0,
            "total_errors": 0,
        }

    async def audit(self, text_to_audit: str, context: str = "") -> LogicalAuditResult:
        """Audit text for logical errors.

        Args:
            text_to_audit: Text to audit (answer or reasoning chain)
            context: Optional context (original question, SLM responses)

        Returns:
            LogicalAuditResult
        """
        self._stats["total_audits"] += 1
        result = LogicalAuditResult()
        t0 = time.time()

        if not text_to_audit or len(text_to_audit.strip()) < 10:
            result.verdict = "UNKNOWN"
            result.error = "Text too short to audit"
            return result

        # Build prompt
        full_text = text_to_audit
        if context:
            full_text = f"Context: {context}\n\nText to audit: {text_to_audit}"

        prompt = LOGICAL_AUDITOR_PROMPT.replace("__TEXT__", full_text[:4000])

        # Call LLM
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Try GLM 5.2 first
                response_data = await self._call_llm(client, prompt, self._model)
                if response_data is None:
                    # Fallback to Llama 3.3
                    logger.info("[LogicalAuditor] GLM 5.2 failed — fallback to Llama 3.3")
                    response_data = await self._call_llm(client, prompt, self._fallback_model)

                if response_data is None:
                    result.verdict = "UNKNOWN"
                    result.error = "LLM call failed — both models unavailable"
                    self._stats["total_errors"] += 1
                    return result

                result.raw_response = response_data.get("content", "")
                result.model_used = response_data.get("model", self._model)

                # Parse JSON from response
                parsed = self._parse_json_response(result.raw_response)
                if parsed:
                    result.verdict = parsed.get("verdict", "UNREFUTED_IN_CURRENT_SCOPE")
                    result.confidence = float(parsed.get("confidence", 0.5))
                    result.falsification_attempt = parsed.get("falsification_attempt", "")
                    result.recommendation = parsed.get("recommendation", "")

                    for issue_data in parsed.get("issues", []):
                        result.issues.append(LogicalIssue(
                            type=issue_data.get("type", "unknown"),
                            description=issue_data.get("description", ""),
                            severity=issue_data.get("severity", "minor"),
                            location=issue_data.get("location", ""),
                        ))

                    self._stats["total_issues_found"] += len(result.issues)
                    critical_count = sum(1 for i in result.issues if i.severity == "critical")
                    self._stats["total_critical"] += critical_count

                    if result.verdict == "PASS":
                        self._stats["total_pass"] += 1
                    elif result.verdict == "FAIL":
                        self._stats["total_fail"] += 1
                    elif result.verdict == "UNREFUTED_IN_CURRENT_SCOPE":
                        self._stats["total_unrefuted"] += 1
                else:
                    result.verdict = "UNKNOWN"
                    result.error = "Failed to parse LLM response as JSON"

        except Exception as e:
            result.verdict = "UNKNOWN"
            result.error = str(e)[:200]
            self._stats["total_errors"] += 1
            logger.warning(f"[LogicalAuditor] Error: {e}")

        result.elapsed_ms = (time.time() - t0) * 1000
        logger.info(
            f"[LogicalAuditor] verdict={result.verdict} conf={result.confidence:.2f} "
            f"issues={len(result.issues)} elapsed={result.elapsed_ms:.0f}ms model={result.model_used}"
        )
        return result

    async def _call_llm(self, client, prompt: str, model: str) -> dict | None:
        """Call LLM via OpenRouter."""
        if not self._api_key:
            logger.warning("[LogicalAuditor] No API key")
            return None

        try:
            r = await client.post(
                self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.1,  # Low temp for analytical tasks
                },
            )
            if r.status_code == 200:
                data = r.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"content": content, "model": model}
            else:
                logger.debug(f"[LogicalAuditor] {model} HTTP {r.status_code}: {r.text[:100]}")
                return None
        except Exception as e:
            logger.debug(f"[LogicalAuditor] {model} error: {e}")
            return None

    def _parse_json_response(self, response: str) -> dict | None:
        """Extract JSON from LLM response."""
        # Try direct JSON parse
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError as e:
            logger.debug(f"[V104.37] meta/logical_auditor.py: e={e}")

        # Try to find JSON block in response
        import re
        # Look for ```json ... ``` block
        match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as e:
                logger.debug(f"[V104.37] meta/logical_auditor.py: e={e}")

        # Look for { ... } block
        match = re.search(r'\{[^{}]*"(?:verdict|issues)"[^{}]*\}', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                logger.debug(f"[V104.37] meta/logical_auditor.py: e={e}")

        # Last resort — find first { and last }
        first = response.find('{')
        last = response.rfind('}')
        if first >= 0 and last > first:
            try:
                return json.loads(response[first:last+1])
            except json.JSONDecodeError as e:
                logger.debug(f"[V104.37] meta/logical_auditor.py: e={e}")

        return None

    def should_audit(self, verdict: str, answer: str) -> bool:
        """Check if logical audit should run.

        Run audit when:
          - verdict is PASS (verify the reasoning is sound)
          - answer is long enough (>50 chars — short answers don't need deep audit)
          - answer contains reasoning/claims (not just a single word)
        """
        if verdict != "PASS":
            return False
        if not answer or len(answer) < 50:
            return False
        # Skip simple math answers
        if answer.replace(" ", "").replace("=", "").replace("+", "").replace("-", "").replace("*", "").replace("/", "").replace(".", "").isdigit():
            return False
        return True

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "model": self._model,
            "fallback_model": self._fallback_model,
            "api_configured": bool(self._api_key),
        }


__all__ = ["LogicalIssue", "LogicalAuditResult", "LogicalAuditorEngine", "LOGICAL_AUDITOR_PROMPT"]
