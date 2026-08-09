"""
[OPT-19] UNDataDataSource — UN Data API (country statistics & demographics).

DNA SCP: SuperGPQA Political Science discipline needs verifiable country
statistics (population, GDP, trade, demographics, education, energy). WorldBank
covers economic indicators but UN Data adds trade, energy, environment,
population breakdowns. UN Data's SOAP/JSON API is free without a key —
fills the political_science stats gap.
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.undata")


class UNDataDataSource(IDataSource):
    """UN Data API — country stats, demographics, trade, energy (no key)."""

    BASE_URL = "https://data.un.org/ws/bs/JsonService.svc"

    def __init__(self):
        # UN Data API is free, no key needed
        self.enabled = True
        logger.info("[UNData] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "undata"

    @property
    def priority(self) -> int:
        return 15  # mid-low — political_science niche; cross-check WorldBank

    @property
    def ttl(self) -> int:
        return 86400  # 24h — UN stats update annually

    def get_supported_intents(self) -> list[str]:
        return ["country_stats", "demographics", "trade_stats",
                "energy_stats", "population_stats", "political_science",
                "un_data"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "country stats", "thống kê quốc gia",
            "demographics", "nhân khẩu học",
            "trade stats", "thương mại",
            "energy stats", "năng lượng",
            "population", "dân số",
            "un data", "liên hiệp quốc",
            "united nations", "un stats",
            "life expectancy", "tuổi thọ",
            "infant mortality", "tử vong trẻ sơ sinh",
            "migration", "di cư",
            "refugee", "nạn nhân",
            "gender", "giới tính",
            "literacy", "xóa mù chữ",
            "human development", "phát triển con người",
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
            return self._search_data(question)
        except Exception as e:
            logger.debug(f"[UNData] query failed: {e}")
            return None

    def _search_data(self, query: str) -> dict | None:
        import httpx
        try:
            # UN Data JSON service — search endpoint
            r = httpx.get(f"{self.BASE_URL}/Search",
                          params={"searchQuery": query, "maxRes": 5},
                          timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0 (educational)"})
            if r.status_code != 200:
                logger.debug(f"[UNData] search returned {r.status_code}")
                # Fallback to UN Data public search page
                return self._fallback_search_page(query)
            data = r.json()
            results = (data.get("d") or data.get("results") or
                       data.get("SearchResults") or [])
            if not results:
                return None
            top = results[0]
            return {
                "value": top.get("IndicatorName") or top.get("Title") or "",
                "source": "undata",
                "metadata": {
                    "indicator": top.get("IndicatorName") or top.get("Title", ""),
                    "country": top.get("Country") or top.get("Area", ""),
                    "year": top.get("Year", ""),
                    "value": top.get("Value", ""),
                    "unit": top.get("Unit", ""),
                    "url": top.get("Url", ""),
                    "result_count": len(results),
                },
            }
        except Exception as e:
            logger.debug(f"[UNData] search failed: {e}")
            return None

    def _fallback_search_page(self, query: str) -> dict | None:
        """Fallback: UN Data public search HTML page (no JSON API)."""
        import httpx
        try:
            url = "https://data.un.org/Search.aspx"
            r = httpx.get(url, params={"q": query}, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0 (educational)"})
            if r.status_code != 200:
                return None
            return {
                "value": query,
                "source": "undata",
                "metadata": {
                    "url": r.url,
                    "fallback": True,
                    "page_size": len(r.text),
                },
            }
        except Exception as e:
            logger.debug(f"[UNData] fallback search failed: {e}")
            return None
