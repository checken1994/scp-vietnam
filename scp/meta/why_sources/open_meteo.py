"""
[Task 8-A] Open-Meteo weather source handler — extracted from why_engine.py

TẠI SAO: WhyEngine._query_open_meteo() was 25 LOC inline. Extracted as standalone
function for modularity. Backward-compatible — WhyEngine delegates.
"""
from __future__ import annotations

import json as _json
import logging
import urllib.request

from scp.security.url_safety import safe_urlopen  # noqa: B310

logger = logging.getLogger("scp.why.sources.open_meteo")


def query_open_meteo(target: str, question: str) -> str | None:
    """Query Open-Meteo for weather."""
    try:
        # Geocode city name
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={target}&count=1"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "SCP-WHY/1.0"})
        with safe_urlopen(req, timeout=8) as resp:
            geo = _json.loads(resp.read().decode('utf-8'))
            results = geo.get("results", [])
            if not results:
                return None
            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
        # Get weather
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        req = urllib.request.Request(weather_url, headers={"User-Agent": "SCP-WHY/1.0"})
        with safe_urlopen(req, timeout=8) as resp:
            w = _json.loads(resp.read().decode('utf-8'))
            temp = w.get("current", {}).get("temperature_2m", "")
            return f"temperature={temp}°C"
        return None
    except Exception as e:  # [RC-7 FIX Task 6-B] silent swallow → log context
        logger.warning(f"[why_sources.open_meteo] failed for target='{target}': {e}")
        return None
