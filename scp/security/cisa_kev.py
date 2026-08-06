"""
CISA KEV (Known Exploited Vulnerabilities) feed — real-time CVE exploitation data.

Source: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
Updated: CISA updates this catalog multiple times per week.

Usage:
    from scp.security.cisa_kev import CisaKevFeed
    feed = CisaKevFeed(data_dir="data")
    feed.refresh()  # fetch latest
    is_exploited = feed.is_exploited("CVE-2026-20963")
    recent = feed.get_recent(limit=10)
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("scp.security.cisa_kev")

# [FIX] CISA blocks direct access (403 Akamai). Use GitHub mirror instead.
CISA_KEV_URL = "https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json"
CISA_KEV_URL_FALLBACK = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours — CISA updates multiple times/week


class CisaKevFeed:
    """CISA Known Exploited Vulnerabilities feed.

    Provides:
      - is_exploited(cve_id) → bool
      - get_vuln(cve_id) → dict (full record)
      - get_recent(limit) → list[dict]
      - get_by_product(product) → list[dict]
      - get_by_vendor(vendor) → list[dict]
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.data_dir / "cisa_kev.json"
        self._vulns: dict[str, dict] = {}  # cveID -> record
        self._last_refresh = 0
        self._load_cache()

    def _load_cache(self):
        """Load from local cache file."""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self._vulns = {v.get("cveID", ""): v for v in data.get("vulnerabilities", [])}
                self._last_refresh = data.get("timestamp", 0)
                logger.info(f"[CISA KEV] Loaded {len(self._vulns)} vulns from cache")
            except Exception as e:
                logger.warning(f"[CISA KEV] Cache load error: {e}")

    def refresh(self, force: bool = False) -> dict:
        """Fetch latest from CISA. Returns summary dict.

        Args:
            force: If True, ignore cache TTL.
        """
        if not force and time.time() - self._last_refresh < CACHE_TTL_SECONDS:
            return {"action": "skipped", "reason": "cache fresh", "count": len(self._vulns)}

        try:
<<<<<<< HEAD
            if not CISA_KEV_URL.startswith(("https://", "http://")):
                raise ValueError(f"Unsupported URL scheme for CISA KEV: {CISA_KEV_URL}")
            req = urllib.request.Request(  # noqa: S310 — scheme validated above
=======
            req = urllib.request.Request(
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
                CISA_KEV_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
<<<<<<< HEAD
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — scheme validated above
=======
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — URL validated by SCP
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
                data = json.loads(resp.read().decode("utf-8"))

            vulns = data.get("vulnerabilities", [])
            self._vulns = {v.get("cveID", ""): v for v in vulns}
            self._last_refresh = time.time()

            # Save cache
            self.cache_file.write_text(
                json.dumps({"timestamp": self._last_refresh, "vulnerabilities": vulns}, ensure_ascii=False),
                encoding="utf-8",
            )

            logger.info(f"[CISA KEV] Refreshed: {len(self._vulns)} vulns")
            return {"action": "refreshed", "count": len(self._vulns), "catalog_version": data.get("catalogVersion", "")}
        except Exception as e:
            logger.warning(f"[CISA KEV] Refresh failed: {e}")
            return {"action": "failed", "error": str(e)}

    def is_exploited(self, cve_id: str) -> bool:
        """Check if a CVE is in CISA KEV (actively exploited)."""
        return cve_id.upper() in self._vulns

    def get_vuln(self, cve_id: str) -> dict | None:
        """Get full record for a CVE."""
        return self._vulns.get(cve_id.upper())

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get most recently added vulnerabilities."""
        sorted_vulns = sorted(
            self._vulns.values(),
            key=lambda v: v.get("dateAdded", ""),
            reverse=True,
        )
        return sorted_vulns[:limit]

    def get_by_product(self, product: str) -> list[dict]:
        """Get vulns by product name."""
        p_lower = product.lower()
        return [v for v in self._vulns.values() if p_lower in v.get("product", "").lower()]

    def get_by_vendor(self, vendor: str) -> list[dict]:
        """Get vulns by vendor name."""
        v_lower = vendor.lower()
        return [v for v in self._vulns.values() if v_lower in v.get("vendorProject", "").lower()]

    def stats(self) -> dict:
        """Get feed stats."""
        return {
            "total_vulns": len(self._vulns),
            "last_refresh": self._last_refresh,
            "cache_age_hours": round((time.time() - self._last_refresh) / 3600, 1) if self._last_refresh else None,
        }
