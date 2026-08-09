"""
[OPT-10] WorldBankDataSource — World Bank Open Data API.

DNA SCP: cross-check FRED with World Bank for global indicators.
World Bank API is FREE, no API key needed — great fallback when FRED disabled.
Supports: country GDP, population, poverty stats for 200+ countries.
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.worldbank")


class WorldBankDataSource(IDataSource):
    """World Bank API — global economic indicators (no key required)."""

    BASE_URL = "https://api.worldbank.org/v2"

    # World Bank indicator codes — see https://data.worldbank.org/indicator
    INDICATOR_MAP = {
        "gdp": "NY.GDP.MKTP.CD",          # GDP (current US$)
        "gdp per capita": "NY.GDP.PCAP.CD",  # GDP per capita (current US$)
        "population": "SP.POP.TOTL",       # Total population
        "life expectancy": "SP.DYN.LE00.IN",  # Life expectancy at birth
        "inflation": "FP.CPI.TOTL.ZG",     # Inflation, consumer prices (annual %)
        "unemployment": "SL.UEM.TOTL.ZS",  # Unemployment, total (% of labor force)
        "poverty": "SI.POV.DDAY",          # Poverty headcount ratio at $2.15/day
        "co2 emissions": "EN.ATM.CO2E.KT",  # CO2 emissions (kt)
        "internet users": "IT.NET.USER.ZS",  # Internet users (% of population)
    }

    # Common country names → ISO-2 codes
    COUNTRY_MAP = {
        "việt nam": "VN", "vietnam": "VN",
        "usa": "US", "mỹ": "US", "united states": "US",
        "china": "CN", "trung quốc": "CN",
        "japan": "JP", "nhật bản": "JP",
        "korea": "KR", "hàn quốc": "KR", "south korea": "KR",
        "india": "IN", "ấn độ": "IN",
        "germany": "DE", "đức": "DE",
        "france": "FR", "pháp": "FR",
        "uk": "GB", "anh": "GB", "united kingdom": "GB",
        "russia": "RU", "nga": "RU",
        "brazil": "BR", "bra-xin": "BR",
        "indonesia": "ID", "indo": "ID",
        "thailand": "TH", "thái lan": "TH",
    }

    def __init__(self):
        # World Bank API is free, no key needed
        self.enabled = True
        logger.info("[WorldBank] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "worldbank"

    @property
    def priority(self) -> int:
        return 12  # lower priority than FRED (priority 10) — fallback

    @property
    def ttl(self) -> int:
        return 86400  # 24h cache — World Bank data doesn't change fast

    def get_supported_intents(self) -> list[str]:
        return ["gdp", "population", "inflation", "unemployment",
                "poverty", "life_expectancy", "economics"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = ["gdp", "population", "dân số", "inflation", "lạm phát",
                    "unemployment", "thất nghiệp", "poverty", "nghèo",
                    "life expectancy", "tuổi thọ", "co2", "internet users"]
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
            # Find matching indicator
            indicator_code = None
            indicator_label = None
            for keyword, code in self.INDICATOR_MAP.items():
                if keyword in q:
                    indicator_code = code
                    indicator_label = keyword
                    break
            if not indicator_code:
                return None
            # Find country
            country_code = None
            for name, code in self.COUNTRY_MAP.items():
                if name in q:
                    country_code = code
                    break
            return self._query_indicator(indicator_code, indicator_label, country_code)
        except Exception as e:
            logger.debug(f"[WorldBank] query failed: {e}")
            return None

    def _query_indicator(self, indicator_code: str, label: str,
                          country_code: Optional[str] = None) -> dict | None:
        import httpx
        try:
            country = country_code or "all"
            r = httpx.get(f"{self.BASE_URL}/country/{country}/indicator/{indicator_code}",
                          params={
                              "format": "json",
                              "per_page": 1,
                              "date": "2020:2024",
                              "sort": "desc",
                          }, timeout=10)
            r.raise_for_status()
            data = r.json()
            # World Bank returns [metadata, [observations]]
            if isinstance(data, list) and len(data) >= 2 and data[1]:
                obs = data[1][0]
                return {
                    "value": obs.get("value", ""),
                    "source": "worldbank",
                    "metadata": {
                        "indicator": label,
                        "indicator_code": indicator_code,
                        "country": obs.get("country", {}).get("value", country),
                        "country_code": country_code or "WLD",
                        "date": obs.get("date", ""),
                    },
                }
        except Exception as e:
            logger.debug(f"[WorldBank] indicator {indicator_code} query failed: {e}")
        return None
