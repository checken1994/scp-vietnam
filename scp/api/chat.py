"""
[V104.48] SCP Chat — WebSocket giao tiếp real-time với user

TẠI SAO: SCP V104.47 chỉ có /ask (1 question → 1 verdict) và /v1/chat/completions
(OpenAI-compat). KHÔNG có chat nhiều turn, nhớ context, hỏi lại user, giải thích.

Module này thêm:
  - WebSocket /chat: real-time bidirectional
  - Context memory: nhớ lịch sử conversation
  - SCP tự hỏi lại user khi UNKNOWN
  - SimpleExplainer: giải thích quyết định bằng tiếng Việt
  - Evolution status: user xem SCP đang tự sửa gì
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

# [FIX-CRIT-27 BUG 9] Import verify_admin from api_server to gate the
# /chat/sessions + /chat/{id}/history endpoints (previously NO auth — anyone
# could list all active sessions + read any session's full history).
from scp.api._shared import verify_admin
from typing import Optional

logger = logging.getLogger("scp.chat")

router = APIRouter()


class ConversationManager:
    """Quản lý lịch sử conversation per session."""

    def __init__(self, max_sessions: int = 100, max_history: int = 20):
        self._sessions: dict[str, list[dict]] = {}
        self._max_sessions = max_sessions
        self._max_history = max_history

    def get_history(self, session_id: str) -> list[dict]:
        return self._sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str, metadata: Optional[dict] = None):
        if session_id not in self._sessions:
            if len(self._sessions) >= self._max_sessions:
                oldest = next(iter(self._sessions))
                del self._sessions[oldest]
            self._sessions[session_id] = []

        self._sessions[session_id].append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
            "metadata": metadata or {},
        })

        if len(self._sessions[session_id]) > self._max_history:
            self._sessions[session_id] = self._sessions[session_id][-self._max_history:]

    def get_context_string(self, session_id: str) -> str:
        history = self.get_history(session_id)
        if not history:
            return ""
        lines = []
        for msg in history[-10:]:
            role = "User" if msg["role"] == "user" else "SCP"
            lines.append(f"{role}: {msg['content'][:200]}")
        return "\n".join(lines)


_conversation_mgr = ConversationManager()


@router.websocket("/chat")
async def scp_chat(websocket: WebSocket):
    """
    WebSocket endpoint — chat real-time với SCP.

    User gửi: {"message": "What is 2+2?"}
    SCP trả: {"answer": "4", "verdict": "PASS", "confidence": 0.99, "reasoning": "..."}
    """
    await websocket.accept()

    session_id = str(uuid.uuid4())[:8]
    logger.info(f"[SCP Chat] Session {session_id} connected")

    await websocket.send_json({
        "type": "system",
        "message": f"SCP V104.48 đã kết nối. Session: {session_id}\n"
                   f"Tôi có thể kiểm tra câu trả lời, phát hiện tấn công, và tự học.\n"
                   f"Hỏi tôi bất cứ điều gì — tôi sẽ nói 'Tại sao?' và kiểm tra.",
        "session_id": session_id,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                user_message = msg.get("message", "").strip()
            except json.JSONDecodeError:
                user_message = data.strip()

            if not user_message:
                continue

            _conversation_mgr.add_message(session_id, "user", user_message)

            try:
                import asyncio

                from scp.api_server import get_judge

                # [FIX-CRIT-27 BUG 8] Removed `os.environ.setdefault("SCP_DEV_MODE", "1")`
                # — any WebSocket client was force-enabling SCP_DEV_MODE globally,
                # which disables admin auth for ALL endpoints server-wide (verify_admin
                # reads SCP_DEV_MODE at call time). Dev mode must be explicit, not
                # auto-enabled by a chat connection.
                judge = get_judge()

                v = await asyncio.to_thread(
                    judge.judge,
                    question=user_message,
                    ai_answer="",
                    cycle_count=0,
                    source="chat",
                    v98_context={"session_id": session_id, "ip": "websocket"},
                )

                # [FIX-CRIT-27 BUG 7] Determine abstain BEFORE building response.
                # Previously `answer` was set to v.final_answer unconditionally,
                # then FAIL added an `explanation` but the leaked answer remained
                # in the JSON. Now FAIL/KILL/FLAGGED withhold the answer.
                _gov = v.evidence.get("governance_decision", "")
                _abstain = (_gov == "KILL") or (v.verdict in ("FAIL", "FLAGGED"))
                _ws_answer = ("[SCP: Answer withheld]" if _abstain
                              else (v.final_answer or "(Không có câu trả lời)"))
                _ws_reasoning = ("" if _abstain
                                 else (v.reasoning[:300] if v.reasoning else ""))

                response = {
                    "type": "answer",
                    "answer": _ws_answer,
                    "verdict": v.verdict,
                    "confidence": round(v.confidence, 2),
                    "domain": v.domain or "general",
                    "reasoning": _ws_reasoning,
                    "why_plan": "no" if _abstain else ("yes" if v.evidence.get("why_plan") else "no"),
                    "governance": _gov if _gov else "none",
                    "healing": 0 if _abstain else len(v.evidence.get("healing_actions", [])),
                }

                if v.verdict == "UNKNOWN":
                    response["type"] = "clarification"
                    response["question"] = (
                        f"Tôi chưa đủ thông tin để kết luận. "
                        f"Bạn có thể cung cấp thêm chi tiết về '{user_message[:50]}' không?"
                    )

                if v.verdict == "FAIL":
                    response["type"] = "rejected"
                    response["answer"] = "[SCP: Answer withheld]"  # [FIX-CRIT-27 BUG 7] belt-and-suspenders
                    response["reasoning"] = ""  # don't leak why attack was caught
                    response["explanation"] = (
                        f"Tôi không thể xác nhận câu trả lời này. Lý do: "
                        f"{v.reasoning[:200] if v.reasoning else 'Không đủ bằng chứng.'}"
                    )

                if v.verdict == "PASS":
                    response["type"] = "verified"
                    response["explanation"] = (
                        f"Đã kiểm tra: câu trả lời đạt độ tin cậy {v.confidence:.0%}. "
                        f"Domain: {v.domain}."
                    )

                if "tiến hóa" in user_message.lower() or "evolution" in user_message.lower():
                    try:
                        from scp.core.code_evolution_agent import get_evolution_agent
                        agent = get_evolution_agent()
                        response["evolution"] = agent.get_stats()
                    except Exception:
                        logger.exception("[chat.py:182] silenced exception")

                if "học" in user_message.lower() or "learning" in user_message.lower():
                    try:
                        from scp.api_server import _fast_learning
                        if _fast_learning:
                            response["learning"] = _fast_learning.get_stats()
                    except Exception:
                        logger.exception("[chat.py:190] silenced exception")

                await websocket.send_json(response)
                _conversation_mgr.add_message(session_id, "scp", response.get("answer", ""), response)

            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Lỗi xử lý: {str(e)[:100]}",
                })
                logger.error(f"[SCP Chat] Error: {e}")

    except WebSocketDisconnect:
        logger.info(f"[SCP Chat] Session {session_id} disconnected")
    except Exception as e:
        logger.error(f"[SCP Chat] WebSocket error: {e}")


@router.get("/chat/sessions")
async def list_sessions(_admin: bool = Depends(verify_admin)):  # [FIX-CRIT-27 BUG 9] was NO auth
    return {
        "active_sessions": len(_conversation_mgr._sessions),
        "sessions": list(_conversation_mgr._sessions.keys()),
    }


@router.get("/chat/{session_id}/history")
async def get_session_history(session_id: str, _admin: bool = Depends(verify_admin)):  # [FIX-CRIT-27 BUG 9] was NO auth
    return {
        "session_id": session_id,
        "messages": _conversation_mgr.get_history(session_id),
    }
