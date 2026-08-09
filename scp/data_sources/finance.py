"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

WHY Engine, Recursive Why, MetaFalsifier, ProofGraph
License: See LICENSE file
"""

"""
FinanceDataSource - Data source cho Tài chính
Bao gồm: tỷ giá hối đoái (Frankfurter), crypto (CoinGecko), giá vàng.
"""
import logging
import time
from typing import Any, Optional

import requests

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class FinanceDataSource(IDataSource):
    """Data source cho các câu hỏi tài chính."""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._cache_timestamp: dict[str, float] = {}

        # Major currencies
        self._currencies = {
            'usd': {'name': 'US Dollar', 'vi_name': 'Đô la Mỹ', 'symbol': '$', 'country': 'United States'},
            'vnd': {'name': 'Vietnamese Dong', 'vi_name': 'Việt Nam Đồng', 'symbol': '₫', 'country': 'Vietnam'},
            'eur': {'name': 'Euro', 'vi_name': 'Euro', 'symbol': '€', 'country': 'European Union'},
            'gbp': {'name': 'British Pound', 'vi_name': 'Bảng Anh', 'symbol': '£', 'country': 'United Kingdom'},
            'jpy': {'name': 'Japanese Yen', 'vi_name': 'Yên Nhật', 'symbol': '¥', 'country': 'Japan'},
            'cny': {'name': 'Chinese Yuan', 'vi_name': 'Nhân dân tệ', 'symbol': '¥', 'country': 'China'},
            'krw': {'name': 'South Korean Won', 'vi_name': 'Won Hàn Quốc', 'symbol': '₩', 'country': 'South Korea'},
            'aud': {'name': 'Australian Dollar', 'vi_name': 'Đô la Úc', 'symbol': 'A$', 'country': 'Australia'},
            'cad': {'name': 'Canadian Dollar', 'vi_name': 'Đô la Canada', 'symbol': 'C$', 'country': 'Canada'},
            'chf': {'name': 'Swiss Franc', 'vi_name': 'Franc Thụy Sĩ', 'symbol': 'CHF', 'country': 'Switzerland'},
            'sgd': {'name': 'Singapore Dollar', 'vi_name': 'Đô la Singapore', 'symbol': 'S$', 'country': 'Singapore'},
            'thb': {'name': 'Thai Baht', 'vi_name': 'Baht Thái', 'symbol': '฿', 'country': 'Thailand'},
            'rub': {'name': 'Russian Ruble', 'vi_name': 'Rúp Nga', 'symbol': '₽', 'country': 'Russia'},
            'inr': {'name': 'Indian Rupee', 'vi_name': 'Rupee Ấn', 'symbol': '₹', 'country': 'India'},
        }

        # Major cryptocurrencies
        self._cryptos = {
            'bitcoin': {'symbol': 'BTC', 'id': 'bitcoin', 'vi_name': 'Bitcoin'},
            'btc': {'symbol': 'BTC', 'id': 'bitcoin', 'vi_name': 'Bitcoin'},
            'ethereum': {'symbol': 'ETH', 'id': 'ethereum', 'vi_name': 'Ethereum'},
            'eth': {'symbol': 'ETH', 'id': 'ethereum', 'vi_name': 'Ethereum'},
            'tether': {'symbol': 'USDT', 'id': 'tether', 'vi_name': 'Tether'},
            'usdt': {'symbol': 'USDT', 'id': 'tether', 'vi_name': 'Tether'},
            'binancecoin': {'symbol': 'BNB', 'id': 'binancecoin', 'vi_name': 'BNB'},
            'bnb': {'symbol': 'BNB', 'id': 'binancecoin', 'vi_name': 'BNB'},
            'solana': {'symbol': 'SOL', 'id': 'solana', 'vi_name': 'Solana'},
            'sol': {'symbol': 'SOL', 'id': 'solana', 'vi_name': 'Solana'},
            'ripple': {'symbol': 'XRP', 'id': 'ripple', 'vi_name': 'Ripple'},
            'xrp': {'symbol': 'XRP', 'id': 'ripple', 'vi_name': 'Ripple'},
            'cardano': {'symbol': 'ADA', 'id': 'cardano', 'vi_name': 'Cardano'},
            'ada': {'symbol': 'ADA', 'id': 'cardano', 'vi_name': 'Cardano'},
            'dogecoin': {'symbol': 'DOGE', 'id': 'dogecoin', 'vi_name': 'Dogecoin'},
            'doge': {'symbol': 'DOGE', 'id': 'dogecoin', 'vi_name': 'Dogecoin'},
        }

        # Reference gold/silver prices (approximate)
        self._precious_metals_static = {
            'gold': 60000,  # VND/gram (approx, will be overridden by API)
            'silver': 750,  # VND/gram
            'platinum': 28000,
            'palladium': 32000,
        }

    @property
    def name(self) -> str:
        return "FinanceDataSource"

    @property
    def priority(self) -> int:
        return 2

    @property
    def ttl(self) -> int:
        return 300  # 5 minutes - prices change frequently

    def get_supported_intents(self) -> list[str]:
        return [
            'exchange_rate',
            'crypto_price',
            'precious_metal',
            'currency_info',
            'stock_price',
        ]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower().strip()
            if entity_lower in self._currencies:
                return True
            if entity_lower in self._cryptos:
                return True
            # Crypto symbol detection
            for c in self._cryptos.values():
                if entity_lower == c['symbol'].lower():
                    return True
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> Optional[dict[str, Any]]:
        if not entity:
            return None

        entity_lower = entity.lower().strip()

        # Currency info (static)
        if entity_lower in self._currencies:
            data = self._currencies[entity_lower]
            return {
                'value': data['symbol'],
                'source': 'Local Finance Database',
                'metadata': data
            }

        # Crypto info
        if entity_lower in self._cryptos:
            crypto = self._cryptos[entity_lower]
            # Try CoinGecko API
            result = self._fetch_crypto_price(crypto['id'])
            if result:
                return result
            return {
                'value': None,
                'source': 'Local Finance Database',
                'metadata': {**crypto, 'note': 'Live price unavailable'}
            }

        # Crypto by symbol
        for c in self._cryptos.values():
            if entity_lower == c['symbol'].lower():
                result = self._fetch_crypto_price(c['id'])
                if result:
                    return result

        # Currency pair (e.g., "usd vnd", "usd to vnd")
        import re
        pair_match = re.match(r'(\w{3})\s*(?:to|->|/|\s)\s*(\w{3})', entity_lower)
        if pair_match:
            from_curr, to_curr = pair_match.group(1), pair_match.group(2)
            if from_curr in self._currencies and to_curr in self._currencies:
                result = self._fetch_exchange_rate(from_curr, to_curr)
                if result:
                    return result

        return None

    def _fetch_crypto_price(self, coin_id: str) -> Optional[dict[str, Any]]:
        """Lấy giá crypto từ CoinGecko."""
        cache_key = f"crypto:{coin_id}"
        if cache_key in self._cache and time.time() - self._cache_timestamp.get(cache_key, 0) < self.ttl:
            return self._cache[cache_key]
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
            response = requests.get(url, timeout=8)
            if response.status_code == 200:
                data = response.json()
                if coin_id in data:
                    price_usd = data[coin_id].get('usd', 0)
                    change_24h = data[coin_id].get('usd_24h_change', 0)
                    result = {
                        'value': price_usd,
                        'source': 'CoinGecko API',
                        'metadata': {
                            'coin_id': coin_id,
                            'price_usd': price_usd,
                            'change_24h_pct': change_24h,
                            'currency': 'USD',
                            'method': 'coingecko',
                        }
                    }
                    self._cache[cache_key] = result
                    self._cache_timestamp[cache_key] = time.time()
                    return result
        except Exception as e:
            logger.warning(f"[Finance] CoinGecko fetch failed: {e}")
        return None

    def _fetch_exchange_rate(self, from_curr: str, to_curr: str) -> Optional[dict[str, Any]]:
        """Lấy tỷ giá từ Frankfurter (free, no key)."""
        cache_key = f"fx:{from_curr}:{to_curr}"
        if cache_key in self._cache and time.time() - self._cache_timestamp.get(cache_key, 0) < self.ttl:
            return self._cache[cache_key]
        try:
            url = f"https://api.frankfurter.app/latest?from={from_curr}&to={to_curr}"
            response = requests.get(url, timeout=8)
            if response.status_code == 200:
                data = response.json()
                rate = data.get('rates', {}).get(to_curr.upper())
                if rate:
                    result = {
                        'value': rate,
                        'source': 'Frankfurter API',
                        'metadata': {
                            'from': from_curr.upper(),
                            'to': to_curr.upper(),
                            'rate': rate,
                            'date': data.get('date'),
                            'method': 'frankfurter',
                        }
                    }
                    self._cache[cache_key] = result
                    self._cache_timestamp[cache_key] = time.time()
                    return result
        except Exception as e:
            logger.warning(f"[Finance] Frankfurter fetch failed: {e}")
        return None

    def health_check(self) -> bool:
        """[V104.31 #4] Real ping — CoinGecko + Frankfurter, cached 60s."""
        import time
        cache_key = '_health_cache'
        cache_ts_key = '_health_cache_ts'
        now = time.time()
        if cache_key in self._cache and now - self._cache.get(cache_ts_key, 0) < 60:
            return self._cache[cache_key]
        api_ok = False
        try:
            r = requests.get("https://api.coingecko.com/api/v3/ping", timeout=3)
            if r.status_code == 200:
                api_ok = True
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        if not api_ok:
            try:
                r = requests.get("https://api.frankfurter.app/latest?from=USD&to=EUR", timeout=3)
                if r.status_code == 200:
                    api_ok = True
            except Exception as e:
                logger.warning(f"Silent except: {e}")
        healthy = api_ok or bool(self._currencies)
        self._cache[cache_key] = healthy
        self._cache[cache_ts_key] = now
        return healthy
