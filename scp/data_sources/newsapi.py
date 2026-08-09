"""
[OPT-4] NewsAPIDataSource — news headlines for cross-checking RealLearning.
DNA SCP: RealLearning uses news headlines but stores them without verification.
NewsAPI adds structured news data with source attribution + timestamp.
"""
from __future__ import annotations

import logging
import os

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.newsapi")


class NewsAPIDataSource(IDataSource):
    """NewsAPI.org — structured news headlines (free tier: 100 requests/day)."""

    BASE_URL = "https://newsapi.org/v2"

    def __init__(self):
        self.api_key = os.environ.get("NEWSAPI_API_KEY", "")
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[NewsAPI] disabled — set NEWSAPI_API_KEY to enable")

    @property
    def name(self) -> str:
        return "newsapi"

    @property
    def priority(self) -> int:
        return 15  # lower priority — supplementary source

    @property
    def ttl(self) -> int:
        return 1800  # 30min cache (news changes fast)

    def get_supported_intents(self) -> list[str]:
        return ["news", "headline", "current_event", "breaking"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = ["news", "tin tức", "headline", "breaking", "latest", "mới nhất",
                    "happen", "xảy ra", "current event", "sự kiện"]
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
            import httpx
            r = httpx.get(f"{self.BASE_URL}/everything", params={
                "q": question[:100],
                "sortBy": "publishedAt",
                "pageSize": 5,
                "language": "en",
                "apiKey": self.api_key,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            articles = data.get("articles", [])
            if articles:
                headlines = [
                    {
                        "title": a.get("title", ""),
                        "source": a.get("source", {}).get("name", ""),
                        "published": a.get("publishedAt", ""),
                        "url": a.get("url", ""),
                    }
                    for a in articles[:5]
                ]
                return {
                    "value": headlines[0]["title"] if headlines else "",
                    "source": "newsapi",
                    "metadata": {"headlines": headlines, "count": len(headlines)},
                }
        except Exception as e:
            logger.debug(f"[NewsAPI] query failed: {e}")
        return None
