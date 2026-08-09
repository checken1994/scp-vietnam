"""
[OPT-12] CourtListenerDataSource — CourtListener REST API v4 (case law).

DNA SCP: SuperGPQA Law discipline needs case-law verification beyond statutes.
CourtListener (Free Law Project) hosts millions of US federal/state court
opinions with a public REST API. Without a token you can still query (rate-
limited); with COURTLISTENER_TOKEN you get higher limits. Fills the case-law
gap left by Cornell LII (statutes only).
"""
from __future__ import annotations

import logging
import os

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.courtlistener")


class CourtListenerDataSource(IDataSource):
    """CourtListener API v4 — US case law (free, token optional for rate limit)."""

    BASE_URL = "https://www.courtlistener.com/api/rest/v4"

    def __init__(self):
        self.api_key = os.environ.get("COURTLISTENER_TOKEN", "")
        # CourtListener works without token but rate-limited — enabled either way
        self.enabled = True
        if self.api_key:
            logger.info("[CourtListener] enabled with COURTLISTENER_TOKEN (higher rate limit)")
        else:
            logger.info("[CourtListener] enabled without token (rate-limited; set "
                        "COURTLISTENER_TOKEN for higher limits)")

    @property
    def name(self) -> str:
        return "courtlistener"

    @property
    def priority(self) -> int:
        return 16  # mid-low — case-law niche; cross-check Cornell LII

    @property
    def ttl(self) -> int:
        return 3600  # 1h — case law doesn't change often but recent opinions flow

    def get_supported_intents(self) -> list[str]:
        return ["case_law", "court_case", "legal_case", "court_opinion",
                "judicial", "precedent", "ruling"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "case law", "án lệ", "court case", "vụ án",
            "court opinion", "judicial", "tư pháp",
            "precedent", "tiền lệ", "ruling", "phán quyết",
            "supreme court", "tối cao法院", "appeals court",
            "v. ", " vs. ",  # case citation patterns
            "courtlistener",
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
            return self._search_opinions(question)
        except Exception as e:
            logger.debug(f"[CourtListener] query failed: {e}")
            return None

    def _search_opinions(self, query: str) -> dict | None:
        import httpx
        try:
            headers = {"User-Agent": "SCP-Verifier/1.0 (educational)"}
            if self.api_key:
                headers["Authorization"] = f"Token {self.api_key}"
            r = httpx.get(f"{self.BASE_URL}/o/",
                          params={"search": query, "page_size": 5},
                          headers=headers, timeout=10)
            if r.status_code != 200:
                logger.debug(f"[CourtListener] search returned {r.status_code}")
                return None
            data = r.json()
            results = data.get("results", [])
            if not results:
                return None
            top = results[0]
            return {
                "value": top.get("caseName") or top.get("case_name", ""),
                "source": "courtlistener",
                "metadata": {
                    "case_name": top.get("caseName") or top.get("case_name", ""),
                    "date_filed": top.get("dateFiled") or top.get("date_filed", ""),
                    "court": top.get("court", ""),
                    "docket_number": top.get("docketNumber") or top.get("docket_number", ""),
                    "citation": top.get("citation", []),
                    "result_count": data.get("count", 0),
                    "absolute_url": top.get("absolute_url", ""),
                },
            }
        except Exception as e:
            logger.debug(f"[CourtListener] opinion search failed: {e}")
            return None
