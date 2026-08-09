"""
[OPT-15] MetMuseumDataSource — Metropolitan Museum of Art Collection API.

DNA SCP: SuperGPQA Art discipline needs verifiable artwork metadata (artist,
period, medium, date). The generic ArtsDataSource is broad but not deep on
individual artworks. The Met's Open Access API exposes 490k+ objects with
rich metadata (no key required) — fills the artwork-lookup gap.
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.metmuseum")


class MetMuseumDataSource(IDataSource):
    """Met Museum Collection API — 490k+ artworks (no key required)."""

    BASE_URL = "https://collectionapi.metmuseum.org/public/collection/v1"

    def __init__(self):
        # Met Museum public API — no key needed
        self.enabled = True
        logger.info("[MetMuseum] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "metmuseum"

    @property
    def priority(self) -> int:
        return 15  # mid-low — art niche; cross-check ArtsDataSource

    @property
    def ttl(self) -> int:
        return 86400  # 24h — museum collection stable

    def get_supported_intents(self) -> list[str]:
        return ["art", "artwork", "painting", "sculpture", "artist",
                "art_history", "museum", "exhibition"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "art", "nghệ thuật",
            "artwork", "tác phẩm",
            "painting", "tranh",
            "sculpture", "điêu khắc",
            "artist", "họa sĩ",
            "art history", "lịch sử nghệ thuật",
            "museum", "viện bảo tàng",
            "met museum", "metropolitan",
            "met object", "object #", "object id",
            "art collection", "collection",
            "renaissance", "phục hưng",
            "impressionism", "ấn tượng",
            "baroque", "rococo",
            "monet", "van gogh", "picasso", "rembrandt",
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
            # Try to extract an object ID first ("met #12345" or "object 12345")
            import re
            m = re.search(r'\b(?:met\s*#?|object\s*)(\d{3,8})\b', question, re.IGNORECASE)
            if m:
                return self._fetch_object(int(m.group(1)))
            # Otherwise search by keyword
            return self._search_objects(question)
        except Exception as e:
            logger.debug(f"[MetMuseum] query failed: {e}")
            return None

    def _search_objects(self, query: str) -> dict | None:
        import httpx
        try:
            r = httpx.get(f"{self.BASE_URL}/search",
                          params={"q": query, "hasImages": "true"},
                          timeout=10)
            if r.status_code != 200:
                logger.debug(f"[MetMuseum] search returned {r.status_code}")
                return None
            data = r.json()
            object_ids = data.get("objectIDs")
            if not object_ids:
                return None
            # Fetch first object for full metadata
            return self._fetch_object(object_ids[0], total=len(object_ids))
        except Exception as e:
            logger.debug(f"[MetMuseum] search failed: {e}")
            return None

    def _fetch_object(self, object_id: int, total: int = 1) -> dict | None:
        import httpx
        try:
            r = httpx.get(f"{self.BASE_URL}/objects/{object_id}", timeout=10)
            if r.status_code != 200:
                return None
            obj = r.json()
            return {
                "value": obj.get("title", ""),
                "source": "metmuseum",
                "metadata": {
                    "title": obj.get("title", ""),
                    "artist": obj.get("artistDisplayName", ""),
                    "artist_begin_date": obj.get("artistBeginDate", ""),
                    "artist_end_date": obj.get("artistEndDate", ""),
                    "object_date": obj.get("objectDate", ""),
                    "medium": obj.get("medium", ""),
                    "dimensions": obj.get("dimensions", ""),
                    "department": obj.get("department", ""),
                    "classification": obj.get("classification", ""),
                    "period": obj.get("period", ""),
                    "culture": obj.get("culture", ""),
                    "object_id": object_id,
                    "image_url": obj.get("primaryImageSmall", "") or obj.get("primaryImage", ""),
                    "met_url": obj.get("objectURL", ""),
                    "total_results": total,
                    "is_public_domain": obj.get("isPublicDomain", False),
                },
            }
        except Exception as e:
            logger.debug(f"[MetMuseum] fetch object {object_id} failed: {e}")
            return None
