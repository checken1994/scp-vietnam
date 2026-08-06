"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
SCP V14 — API Utilities
APICache + fetch_with_retry (V29: tích hợp CircuitBreaker).
"""
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("scp.api_utils")

class APICache:
    def __init__(self, ttl=300):
        self.cache: dict[str, tuple[float, Any]] = {}
        self.ttl = ttl

    def _key(self, url_template, entity):
        if isinstance(entity, (list, tuple)):
            entity_str = "|".join(str(e) for e in entity)
        else:
            entity_str = str(entity)
        return f"{url_template}::{entity_str}"

    def get(self, url_template, entity):
        key = self._key(url_template, entity)
        if key in self.cache:
            ts, val = self.cache[key]
            if time.time() - ts < self.ttl:
                return val
            del self.cache[key]
        return None

    def set(self, url_template, entity, value):
        key = self._key(url_template, entity)
        self.cache[key] = (time.time(), value)
        if len(self.cache) > 10000:
            items = sorted(self.cache.items(), key=lambda x: x[1][0])
            for i in range(len(self.cache) // 5):
                del self.cache[items[i][0]]

    def clear(self):
        self.cache.clear()

    def stats(self):
        return {"cache_size": len(self.cache), "ttl_seconds": self.ttl}


# ============================================================
# [V29] CIRCUIT BREAKER REGISTRY — auto-detect API by URL
# ============================================================
def _detect_breaker(url: str):
    """Detect appropriate circuit breaker dựa trên URL."""
    try:
        from scp.core.circuit_breaker import (
            CircuitBreakerRegistry,
            get_coingecko_breaker,
            get_frankfurter_breaker,
            get_openmeteo_breaker,
            get_pubchem_breaker,
            get_restcountries_breaker,
            get_wikipedia_breaker,
        )
        url_lower = url.lower()
        if 'pubchem.ncbi.nlm.nih.gov' in url_lower:
            return get_pubchem_breaker()
        if 'coingecko.com' in url_lower:
            return get_coingecko_breaker()
        if 'frankfurter.app' in url_lower:
            return get_frankfurter_breaker()
        if 'open-meteo.com' in url_lower:
            return get_openmeteo_breaker()
        if 'wikipedia.org' in url_lower:
            return get_wikipedia_breaker()
        if 'restcountries.com' in url_lower:
            return get_restcountries_breaker()
        # Generic breaker cho APIs khác
        if 'api.' in url_lower or 'http' in url_lower:
            # Use domain as breaker name
            from urllib.parse import urlparse
            domain = urlparse(url).netloc or "unknown"
            return CircuitBreakerRegistry().get(domain, fail_threshold=5, cooldown_sec=60)
        return None
    except Exception as e:
        logger.debug(f"Breaker detect error: {e}")
        return None


# [SECURITY FIX] Safe URL validation — prevents SSRF/LFR via urllib
# urllib supports file://, ftp:// schemes → attacker can read /etc/passwd
# This function validates URL before any urllib call
import urllib.parse as _url_parse


def _validate_url_safe(url: str) -> str:
    """[SECURITY FIX] Validate URL is http/https only. Raises ValueError if not safe.

    Prevents SSRF (file://, ftp://, gopher://) and LFR (file:///etc/passwd).
    Call this before ANY urllib.request.urlopen() call.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL is empty or not a string")
    parsed = _url_parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' not allowed (only http/https)")
    if not parsed.hostname:
        raise ValueError("URL missing hostname")
    return url


def fetch_with_retry(url, headers, timeout=10, max_retries=3):
    """
    [V29] Fetch URL với retry + circuit breaker.

    Circuit breaker tự động OPEN khi API fail nhiều → skip call.
    """
    if headers is None:
        headers = {"User-Agent": "SCP/1.0"}

    # [V29] Check circuit breaker
    breaker = _detect_breaker(url)
    if breaker and not breaker.allow():
        logger.debug(f"[CircuitBreaker] Skip call to {url} (OPEN)")
        return None

    # [SECURITY FIX] Validate URL scheme before any urllib call
    try:
        _validate_url_safe(url)
    except ValueError as e:
        logger.warning(f"[SECURITY] URL validation failed: {e}")
        return None

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 — scheme validated by _validate_url_safe above
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — scheme validated above; nosec B310 — URL validated by SCP allowlist above
                result = json.loads(resp.read().decode('utf-8'))
                # [V29] Record success
                if breaker:
                    breaker.record_success()
                return result
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # [V104.34 #41] TẠI SAO: 429 is transient (rate limit), not client error
                # Was: treated as 4xx → record_success → breaker never trips
                breaker.record_failure() if breaker else None
                return None  # let caller retry with backoff
            elif 400 <= e.code < 500:
                # Client error — not a server failure, don't trip breaker
                if breaker:
                    breaker.record_success()  # API responded, just bad request
                return None
            # 5xx server error → record failure
            if breaker:
                breaker.record_failure(reason=f"HTTP {e.code}")
            if attempt < max_retries - 1: time.sleep(2 ** attempt)
        except Exception as e:
            # Network/timeout error → record failure
            if breaker:
                breaker.record_failure(reason=str(e)[:100])
            if attempt < max_retries - 1: time.sleep(2 ** attempt)
    return None
