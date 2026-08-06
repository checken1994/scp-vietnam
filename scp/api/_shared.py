"""Shared functions for API routes — breaks circular import.

Route modules import from here instead of api_server.py.
"""
import logging
from typing import Any

# [G5-FIX] Header import at module level — verify_admin uses it in signature
try:
    from fastapi import Header
except ImportError:
    Header = None  # type: ignore

logger = logging.getLogger("scp.api")


# Default value for authorization header (used if Header not available)
_AUTH_DEFAULT = Header("", alias="Authorization") if Header else ""


def verify_admin(authorization: str = Header("", alias="Authorization"), token: str = None):
    """Verify admin token — timing-safe, NO dev-mode bypass, RAISES on failure.

    [G2-FIX PERM-01] Previous version returned bool. When used as
    `dependencies=[Depends(verify_admin)]`, FastAPI discarded the return
    value — so ALL 6 admin endpoints accepted any request, no token needed.

    [G5-FIX] Use Header() for authorization — FastAPI auto-populates it
    from the Authorization header. We extract the Bearer token from it.
    """
    import os
    import secrets as _secrets

    from fastapi import HTTPException
    auth_password = os.environ.get("SCP_AUTH_PASSWORD", "")
    auth_token = os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
    # Deny by default if NEITHER is configured (no dev-mode bypass).
    if not auth_password and not auth_token:
        logger.error("SCP_AUTH_TOKEN_SECRET not configured — admin access DENIED")
        raise HTTPException(status_code=503, detail="Auth not configured")
    provided = token or ""
    # [G5-FIX] Extract from Authorization header (FastAPI populates `authorization` param)
    # [REAUDIT-FIX] When called directly (not via Depends), authorization may be Header object
    if not provided and isinstance(authorization, str) and authorization:
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        else:
            provided = authorization.strip()
    if not provided:
        raise HTTPException(status_code=401, detail="Missing auth token")
    ok = False
    if auth_token:
        try:
            ok = ok or _secrets.compare_digest(provided, auth_token)
        except (TypeError, ValueError) as e:
            logger.warning(f"Silent except: {e}")
    if auth_password and not ok:
        try:
            ok = ok or _secrets.compare_digest(provided, auth_password)
        except (TypeError, ValueError) as e:
            logger.warning(f"Silent except: {e}")
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid auth token")
    return True


def get_judge():
    """Get RealityJudge singleton."""
    from scp.runtime.judge import RealityJudge
    return RealityJudge()


def _extract_v98_context(request) -> dict[str, Any]:
    """Extract V98 context from request."""
    ctx = {}
    ctx["ip"] = request.client.host if request.client else "unknown"
    ctx["user_agent"] = request.headers.get("user-agent", "")
    return ctx


# [FIX] Stubs for route imports — delegate to api_server at runtime

from typing import Any

from pydantic import BaseModel, Field


class SessionAnalyzeRequest(BaseModel):
    session_logs: list[dict[str, Any]] = []
    model_responses: list[dict[str, Any]] = []


class SimulationRequest(BaseModel):
    count: int = Field(50, ge=1, le=500)

def _SCP_VERSION(*args, **kwargs):
    """Delegate to api_server._SCP_VERSION (breaks circular import)."""
    from scp.api_server import _SCP_VERSION as _impl
    return _impl(*args, **kwargs)

def _V1042_AVAILABLE(*args, **kwargs):
    """Delegate to api_server._V1042_AVAILABLE (breaks circular import)."""
    from scp.api_server import _V1042_AVAILABLE as _impl
    return _impl(*args, **kwargs)

def _attack_crawler(*args, **kwargs):
    """Delegate to api_server._attack_crawler (breaks circular import)."""
    from scp.api_server import _attack_crawler as _impl
    return _impl(*args, **kwargs)

def _cross_language_learner(*args, **kwargs):
    """Delegate to api_server._cross_language_learner (breaks circular import)."""
    from scp.api_server import _cross_language_learner as _impl
    return _impl(*args, **kwargs)

def _fact_checker(*args, **kwargs):
    """Delegate to api_server._fact_checker (breaks circular import)."""
    from scp.api_server import _fact_checker as _impl
    return _impl(*args, **kwargs)

def _fast_learning(*args, **kwargs):
    """Delegate to api_server._fast_learning (breaks circular import)."""
    from scp.api_server import _fast_learning as _impl
    return _impl(*args, **kwargs)

def _image_detector(*args, **kwargs):
    """Delegate to api_server._image_detector (breaks circular import)."""
    from scp.api_server import _image_detector as _impl
    return _impl(*args, **kwargs)

def _multi_turn_tracker(*args, **kwargs):
    """Delegate to api_server._multi_turn_tracker (breaks circular import)."""
    from scp.api_server import _multi_turn_tracker as _impl
    return _impl(*args, **kwargs)

def _real_learning(*args, **kwargs):
    """Delegate to api_server._real_learning (breaks circular import)."""
    from scp.api_server import _real_learning as _impl
    return _impl(*args, **kwargs)

def _safe_fetch_url(*args, **kwargs):
    """Delegate to api_server._safe_fetch_url (breaks circular import)."""
    from scp.api_server import _safe_fetch_url as _impl
    return _impl(*args, **kwargs)

def _simple_explainer(*args, **kwargs):
    """Delegate to api_server._simple_explainer (breaks circular import)."""
    from scp.api_server import _simple_explainer as _impl
    return _impl(*args, **kwargs)

def _voice_detector(*args, **kwargs):
    """Delegate to api_server._voice_detector (breaks circular import)."""
    from scp.api_server import _voice_detector as _impl
    return _impl(*args, **kwargs)
