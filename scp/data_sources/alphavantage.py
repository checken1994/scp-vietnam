"""
[OPT-3] AlphaVantageDataSource — cross-check CoinGecko for finance.
DNA SCP: don't trust single source. CoinGecko alone = SPOF.
AlphaVantage provides stocks + forex + crypto (free tier: 25 calls/day).
"""
from __future__ import annotations

import logging
import os

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.alphavantage")


class AlphaVantageDataSource(IDataSource):
    """AlphaVantage API — stocks, forex, crypto (cross-check CoinGecko)."""

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self):
        self.api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.enabled = bool(self.api_key and self.api_key != "demo")
        if not self.enabled:
            logger.info("[AlphaVantage] disabled — set ALPHAVANTAGE_API_KEY to enable")

    @property
    def name(self) -> str:
        return "alphavantage"

    @property
    def priority(self) -> int:
        return 10  # lower priority than CoinGecko (priority 5)

    @property
    def ttl(self) -> int:
        return 3600  # 1h cache

    def get_supported_intents(self) -> list[str]:
        return ["stock_price", "forex_rate", "crypto_price", "finance"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        # Accept both intent-based and question-based calls
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = ["stock", "cổ phiếu", "forex", "ngoại hối", "btc usd", "bitcoin price",
                    "exchange rate", "tỷ giá", "giá vàng", "gold price"]
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
            if "stock" in q or "cổ phiếu" in q:
                symbol = self._extract_symbol(question)
                if symbol:
                    return self._query_stock(symbol)
            if "forex" in q or "ngoại hối" in q or "exchange rate" in q or "tỷ giá" in q:
                return self._query_forex(question)
            if "btc" in q or "bitcoin" in q or "crypto" in q:
                return self._query_crypto()
            return None
        except Exception as e:
            logger.debug(f"[AlphaVantage] query failed: {e}")
            return None

    def _extract_symbol(self, question: str) -> str:
        import re
        m = re.search(r'\b(AAPL|GOOGL|MSFT|TSLA|AMZN|META|NVDA|NFLX|IBM|ORCL)\b', question, re.I)
        return m.group(1).upper() if m else ""

    def _query_stock(self, symbol: str) -> dict | None:
        import httpx
        try:
            r = httpx.get(self.BASE_URL, params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self.api_key,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            quote = data.get("Global Quote", {})
            if quote:
                return {
                    "value": quote.get("05. price", ""),
                    "source": "alphavantage",
                    "metadata": {"symbol": symbol, "raw": quote},
                }
        except Exception as e:
            logger.debug(f"[AlphaVantage] stock query failed: {e}")
        return None

    def _query_forex(self, question: str) -> dict | None:
        import re

        import httpx
        m = re.search(r'\b(USD|EUR|JPY|GBP|VND|CNY|KRW)\b', question, re.I)
        if not m:
            return None
        from_curr = m.group(1).upper()
        to_curr = "USD" if from_curr != "USD" else "EUR"
        try:
            r = httpx.get(self.BASE_URL, params={
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": from_curr,
                "to_currency": to_curr,
                "apikey": self.api_key,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            rate = data.get("Realtime Currency Exchange Rate", {})
            if rate:
                return {
                    "value": rate.get("5. Exchange Rate", ""),
                    "source": "alphavantage",
                    "metadata": {"from": from_curr, "to": to_curr},
                }
        except Exception as e:
            logger.debug(f"[AlphaVantage] forex query failed: {e}")
        return None

    def _query_crypto(self) -> dict | None:
        import httpx
        try:
            r = httpx.get(self.BASE_URL, params={
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": "BTC",
                "to_currency": "USD",
                "apikey": self.api_key,
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
            rate = data.get("Realtime Currency Exchange Rate", {})
            if rate:
                return {
                    "value": rate.get("5. Exchange Rate", ""),
                    "source": "alphavantage",
                    "metadata": {"from": "BTC", "to": "USD"},
                }
        except Exception as e:
            logger.debug(f"[AlphaVantage] crypto query failed: {e}")
        return None
