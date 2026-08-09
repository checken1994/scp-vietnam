"""
[OPT-9] FREDDataSource — Federal Reserve Economic Data (St. Louis Fed).

DNA SCP: don't trust single source. WorldBank alone = SPOF for economics.
FRED provides US economic indicators (GDP, inflation, unemployment, interest rates).
Free API: https://fred.stlouisfed.org/docs/api/api_key.html
"""
from __future__ import annotations

import logging
import os

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.fred")


class FREDDataSource(IDataSource):
    """FRED API — US economic indicators (GDP, inflation, unemployment, rates)."""

    BASE_URL = "https://api.stlouisfed.org/fred"

    # Common FRED series IDs — used to map question keywords → series
    SERIES_MAP = {
        "gdp": "GDP",                    # Gross Domestic Product
        "gdp growth": "A191RL1Q225SBEA", # Real GDP growth rate
        "inflation": "CPIAUCSL",          # Consumer Price Index
        "cpi": "CPIAUCSL",
        "unemployment": "UNRATE",         # Civilian Unemployment Rate
        "interest rate": "FEDFUNDS",      # Federal Funds Effective Rate
        "fed funds": "FEDFUNDS",
        "federal funds": "FEDFUNDS",
        "population": "POPTHM",           # Population
        "10 year treasury": "GS10",       # 10-Year Treasury Constant Maturity Rate
        "10-year treasury": "GS10",
        "house price": "CSUSHPINSA",      # S&P/Case-Shiller Home Price Index
    }

    def __init__(self):
        self.api_key = os.environ.get("FRED_API_KEY", "")
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[FRED] disabled — set FRED_API_KEY to enable")

    @property
    def name(self) -> str:
        return "fred"

    @property
    def priority(self) -> int:
        return 10  # lower priority than AlphaVantage (priority 10) — equal weight

    @property
    def ttl(self) -> int:
        return 3600  # 1h cache

    def get_supported_intents(self) -> list[str]:
        return ["gdp", "inflation", "unemployment", "interest_rate", "economics"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = ["gdp", "inflation", "lạm phát", "unemployment", "thất nghiệp",
                    "interest rate", "lãi suất", "cpi", "federal funds", "fed funds",
                    "treasury", "federal reserve"]
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
            q = question.lower()
            # Find matching series
            for keyword, series_id in self.SERIES_MAP.items():
                if keyword in q:
                    return self._query_series(series_id, keyword)
            return None
        except Exception as e:
            logger.debug(f"[FRED] query failed: {e}")
            return None

    def _query_series(self, series_id: str, label: str) -> dict | None:
        import httpx
        try:
            r = httpx.get(f"{self.BASE_URL}/series/observations",
                          params={
                              "series_id": series_id,
                              "api_key": self.api_key,
                              "file_type": "json",
                              "sort_order": "desc",
                              "limit": 1,
                          }, timeout=10)
            r.raise_for_status()
            data = r.json()
            observations = data.get("observations", [])
            if observations:
                obs = observations[0]
                return {
                    "value": obs.get("value", ""),
                    "source": "fred",
                    "metadata": {
                        "series_id": series_id,
                        "label": label,
                        "date": obs.get("date", ""),
                        "realtime_start": obs.get("realtime_start", ""),
                    },
                }
        except Exception as e:
            logger.debug(f"[FRED] series {series_id} query failed: {e}")
        return None
