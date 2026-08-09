"""
[OPT-15] GoogleFactCheckDataSource — Google Fact Check API integration.

DNA SCP #6 Evidence: Google Fact Check API aggregates 40k+ fact checks
from 100+ publishers (Snopes, PolitiFact, FactCheck.org, Reuters, etc.).
Free tier: 10k requests/day.

Env: GOOGLE_FACT_CHECK_API_KEY (already in .env, currently empty)
API: https://factchecktools.googleapis.com/v1alpha1/claims:search
"""
from __future__ import annotations

import logging
import os

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.google_factcheck")


class GoogleFactCheckDataSource(IDataSource):
    """Google Fact Check API — aggregate fact checks from 100+ publishers."""

    BASE_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_FACT_CHECK_API_KEY", "")
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info(
                "[GoogleFactCheck] disabled — set GOOGLE_FACT_CHECK_API_KEY to enable"
            )

    @property
    def name(self) -> str:
        return "google_factcheck"

    @property
    def priority(self) -> int:
        return 8  # high priority — authoritative fact checks

    @property
    def ttl(self) -> int:
        return 3600  # 1h cache

    def get_supported_intents(self) -> list[str]:
        return ["fact_check", "claim_verify", "news_verify", "general"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "true or false", "fact check", "is it true", "có thật",
            "đúng không", "real or fake", "verified", "kiểm chứng",
        ]
        return any(k in q for k in keywords)

    def fetch(self, intent: str, entity: str, **kwargs) -> dict | None:
        return self.query(entity or intent)

    def health_check(self) -> bool:
        return self.enabled

    def query(self, question: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            import httpx
            r = httpx.get(
                self.BASE_URL,
                params={
                    "query": question[:500],
                    "key": self.api_key,
                    "languageCode": "en",
                    "maxAgeDays": 365,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            claims = data.get("claims", [])
            if not claims:
                return None
            # Aggregate ratings from all publishers
            ratings = []
            for claim in claims[:5]:
                review = (claim.get("claimReview") or [{}])[0]
                rating = review.get("textualRating", "")
                publisher = review.get("publisher", {}).get("name", "")
                ratings.append({"rating": rating, "publisher": publisher})
            # Determine consensus
            false_count = sum(
                1 for r in ratings if "false" in r["rating"].lower()
            )
            true_count = sum(
                1 for r in ratings if "true" in r["rating"].lower()
            )
            if false_count > true_count:
                verdict = "FALSE"
            elif true_count > false_count:
                verdict = "TRUE"
            else:
                verdict = "MIXED"
            return {
                "value": verdict,
                "source": "google_factcheck",
                "metadata": {
                    "claims_found": len(claims),
                    "ratings": ratings,
                    "consensus": verdict,
                },
            }
        except Exception as e:
            logger.debug(f"[GoogleFactCheck] query failed: {e}")
            return None


__all__ = ["GoogleFactCheckDataSource"]
