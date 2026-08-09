"""
[OPT-18] USGSDataSource — USGS Earthquake Hazards FDSNWS API.

DNA SCP: SuperGPQA Earth Science discipline needs real-time earthquake data
for verification (magnitude, depth, location, time). USGS FDSNWS event API is
the authoritative global earthquake feed (no key required). Short TTL=1800s
because earthquake feeds update continuously. Cross-checks
earthquake_magnitude_check antibody (added in Task 29-B).
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.usgs")


class USGSDataSource(IDataSource):
    """USGS FDSNWS Earthquake API — global quake events (no key required)."""

    BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1"

    def __init__(self):
        # USGS earthquake API is free, no key needed
        self.enabled = True
        logger.info("[USGS] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "usgs"

    @property
    def priority(self) -> int:
        return 11  # high-mid — earth_science primary (quake data authoritative)

    @property
    def ttl(self) -> int:
        return 1800  # 30min — quake feeds update continuously

    def get_supported_intents(self) -> list[str]:
        return ["earthquake", "seismic", "magnitude", "quake",
                "geology", "earth_science", "seismology"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "earthquake", "động đất",
            "seismic", "địa chấn",
            "magnitude", "độ lớn",
            "quake", "rung chấn",
            "richter", "ring-ten",
            "usgs", "fault line",
            "tectonic", "kiến tạo",
            "seismology", "địa chấn học",
            "epicenter", "tâm chấn",
            "tsunami", "sóng thần",
            "geology", "địa chất",
        ]
        return any(k in q for k in keywords)

    def fetch(self, intent: str, entity: str, **kwargs) -> dict | None:
        """IDataSource interface — delegate to query()."""
        return self.query(entity or intent)

    def health_check(self) -> bool:
        return self.enabled

    def query(self, question: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            import re
            q = question.lower()
            # Extract magnitude if present (e.g., "magnitude 7.5")
            m = re.search(r'(?:magnitude\s*|mag\s*|mw\s*|ml\s*|richter\s*)(\d+\.?\d*)', q)
            if m:
                return self._query_min_magnitude(float(m.group(1)))
            # Extract date (e.g., "yesterday", "today", "last week")
            if "today" in q:
                return self._query_recent(days=1)
            if "yesterday" in q or "recent" in q:
                return self._query_recent(days=7)
            if "last week" in q or "past week" in q:
                return self._query_recent(days=7)
            if "last month" in q or "past month" in q:
                return self._query_recent(days=30)
            # Default — recent significant events (M >= 4.5)
            return self._query_min_magnitude(4.5)
        except Exception as e:
            logger.debug(f"[USGS] query failed: {e}")
            return None

    def _query_min_magnitude(self, min_mag: float) -> dict | None:
        from datetime import datetime, timedelta, timezone

        import httpx
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=30)
            r = httpx.get(f"{self.BASE_URL}/query",
                          params={
                              "format": "geojson",
                              "starttime": start.strftime("%Y-%m-%d"),
                              "endtime": end.strftime("%Y-%m-%d"),
                              "minmagnitude": min_mag,
                              "limit": 5,
                              "orderby": "magnitude",
                          }, timeout=10)
            if r.status_code != 200:
                logger.debug(f"[USGS] query returned {r.status_code}")
                return None
            data = r.json()
            features = data.get("features", [])
            if not features:
                return None
            top = features[0]
            props = top.get("properties", {})
            geom = top.get("geometry", {})
            coords = geom.get("coordinates", [None, None, None])
            return {
                "value": f"M{props.get('mag', '?')} - {props.get('place', '?')}",
                "source": "usgs",
                "metadata": {
                    "magnitude": props.get("mag"),
                    "mag_type": props.get("magType", ""),
                    "place": props.get("place", ""),
                    "time": props.get("time"),
                    "updated": props.get("updated"),
                    "url": props.get("url", ""),
                    "tsunami": props.get("tsunami", 0) == 1,
                    "sig": props.get("sig", 0),
                    "depth_km": coords[2] if len(coords) >= 3 else None,
                    "longitude": coords[0] if len(coords) >= 1 else None,
                    "latitude": coords[1] if len(coords) >= 2 else None,
                    "felt_reports": props.get("felt", 0),
                    "result_count": data.get("metadata", {}).get("count", len(features)),
                },
            }
        except Exception as e:
            logger.debug(f"[USGS] min_mag query failed: {e}")
            return None

    def _query_recent(self, days: int = 7) -> dict | None:
        from datetime import datetime, timedelta, timezone

        import httpx
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            r = httpx.get(f"{self.BASE_URL}/query",
                          params={
                              "format": "geojson",
                              "starttime": start.strftime("%Y-%m-%d"),
                              "endtime": end.strftime("%Y-%m-%d"),
                              "minmagnitude": 2.5,
                              "limit": 10,
                              "orderby": "time",
                          }, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json()
            features = data.get("features", [])
            if not features:
                return None
            return {
                "value": f"{len(features)} quakes (M≥2.5) in last {days}d",
                "source": "usgs",
                "metadata": {
                    "time_window_days": days,
                    "result_count": len(features),
                    "top_events": [
                        {
                            "magnitude": f.get("properties", {}).get("mag"),
                            "place": f.get("properties", {}).get("place", ""),
                            "time": f.get("properties", {}).get("time"),
                        } for f in features[:5]
                    ],
                },
            }
        except Exception as e:
            logger.debug(f"[USGS] recent query failed: {e}")
            return None
