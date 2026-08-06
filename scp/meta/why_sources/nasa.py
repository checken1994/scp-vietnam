"""
[Task 8-A] NASA APOD source handler — extracted from why_engine.py

TẠI SAO: WhyEngine._query_nasa() was 13 LOC inline. Extracted as standalone
function for modularity. Backward-compatible — WhyEngine delegates.
"""
from __future__ import annotations

import json as _json
import logging
import urllib.request

from scp.security.url_safety import safe_urlopen  # noqa: B310

logger = logging.getLogger("scp.why.sources.nasa")


def query_nasa(target: str) -> str | None:
    """Query NASA APOD."""
    try:
        url = "https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY"
        req = urllib.request.Request(url, headers={"User-Agent": "SCP-WHY/1.0"})
        with safe_urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            return data.get("title", "") + ": " + data.get("explanation", "")[:100]
        return None
    except Exception as e:  # [RC-7 FIX Task 6-B] silent swallow → log context
        logger.warning(f"[why_sources.nasa] failed for target='{target}': {e}")
        return None
