"""
[OPT-14] DTICDataSource — Defense Technical Information Center (DTIC).

DNA SCP: SuperGPQA Military discipline needs verified defense research &
doctrine beyond the generic MilitaryDataSource. DTIC hosts 4M+ scientific &
technical reports (DoD-funded research, doctrine pubs, theses from NPS/AFIT).
Public search is free without an API key — fills the doctrine/research gap.
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.dtic")


class DTICDataSource(IDataSource):
    """DTIC API — defense research, doctrine, technical reports (no key)."""

    BASE_URL = "https://apps.dtic.mil"
    SEARCH_URL = "https://apps.dtic.mil/wti/api/search"

    def __init__(self):
        # DTIC public search is free, no key needed
        self.enabled = True
        logger.info("[DTIC] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "dtic"

    @property
    def priority(self) -> int:
        return 18  # mid-low — military niche; cross-check MilitaryDataSource

    @property
    def ttl(self) -> int:
        return 86400  # 24h — doctrine/research doesn't change fast

    def get_supported_intents(self) -> list[str]:
        return ["military", "defense", "doctrine", "defense_research",
                "military_research", "dtic", "national_security"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "military", "quân sự",
            "defense", "quốc phòng",
            "doctrine", "học thuyết quân sự",
            "tactical", "chiến thuật",
            "strategic", "chiến lược quân sự",
            "nato", "pentagon",
            "warfare", "chiến tranh",
            "army", "navy", "air force",
            "lục quân", "hải quân", "không quân",
            "dtic", "national defense",
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
            return self._search(question)
        except Exception as e:
            logger.debug(f"[DTIC] query failed: {e}")
            return None

    def _search(self, question: str) -> dict | None:
        import httpx
        try:
            headers = {"User-Agent": "SCP-Verifier/1.0 (educational)",
                       "Accept": "application/json"}
            r = httpx.get(self.SEARCH_URL, params={"q": question, "page_size": 5},
                          headers=headers, timeout=10)
            if r.status_code != 200:
                logger.debug(f"[DTIC] search returned {r.status_code}")
                # Fall back to DTIC public search page (HTML)
                return self._fallback_search_page(question)
            data = r.json()
            results = data.get("results") or data.get("items") or []
            if not results:
                return None
            top = results[0]
            return {
                "value": top.get("title", ""),
                "source": "dtic",
                "metadata": {
                    "title": top.get("title", ""),
                    "authors": top.get("authors", []),
                    "date": top.get("date") or top.get("publication_date", ""),
                    "abstract": (top.get("abstract") or "")[:300],
                    "dtic_id": top.get("id") or top.get("document_id", ""),
                    "url": top.get("url") or "",
                    "result_count": data.get("count", len(results)),
                },
            }
        except Exception as e:
            logger.debug(f"[DTIC] search failed: {e}")
            return None

    def _fallback_search_page(self, question: str) -> dict | None:
        """Fallback: hit DTIC public search HTML page (no JSON API)."""
        import httpx
        try:
            url = "https://discover.dtic.mil/results/"
            r = httpx.get(url, params={"q": question}, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0 (educational)"})
            if r.status_code != 200:
                return None
            return {
                "value": question,
                "source": "dtic",
                "metadata": {
                    "url": r.url,
                    "fallback": True,
                    "page_size": len(r.text),
                },
            }
        except Exception as e:
            logger.debug(f"[DTIC] fallback search failed: {e}")
            return None
