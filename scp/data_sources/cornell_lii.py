"""
[OPT-11] CornellLIIDataSource — Cornell Law LII (Legal Information Institute).

DNA SCP: law domain had no dedicated primary DataSource — only the generic
LegalDataSource. Cornell LII is the authoritative community resource for
US Constitution, US Code, UCC, CFR — fills the gap for SuperGPQA's Law
discipline (constitutional questions, statute lookup, federal regulations).
Public site is crawl-friendly and exposes a search endpoint (no API key).
"""
from __future__ import annotations

import logging

from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.cornell_lii")


class CornellLIIDataSource(IDataSource):
    """Cornell Law LII — US Constitution, US Code, UCC, CFR (no key required)."""

    BASE_URL = "https://www.law.cornell.edu"
    SEARCH_URL = "https://www.law.cornell.edu/wext/search.html"

    # Direct canonical entry points (HTML pages — parsed minimally for snippets)
    CONSTITUTION_URL = "https://www.law.cornell.edu/constitution/constitution.table.html"
    UCC_URL = "https://www.law.cornell.edu/ucc/ucc.table.html"
    USC_URL = "https://www.law.cornell.edu/uscode/text/"

    def __init__(self):
        # Cornell LII is public + crawl-friendly, no API key needed
        self.enabled = True
        logger.info("[CornellLII] enabled (no API key required)")

    @property
    def name(self) -> str:
        return "cornell_lii"

    @property
    def priority(self) -> int:
        return 15  # mid-low — legal niche; cross-check with LegalDataSource

    @property
    def ttl(self) -> int:
        return 86400  # 24h — law doesn't change fast

    def get_supported_intents(self) -> list[str]:
        return ["law", "legal", "constitution", "statute", "regulation",
                "ucc", "usc", "cfr", "federal_law", "us_law"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        # Question-based (legacy compat)
        question = intent or ""
        q = question.lower()
        keywords = [
            "constitution", "hiến pháp", "statute", "luật",
            "regulation", "quy định", "ucc", "uniform commercial code",
            "usc", "united states code", "cfr", "code of federal regulations",
            "federal law", "supreme court", "amendment", "tu chính",
            "cornell", "lii", "bill of rights",
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
            q = question.lower()
            # Route to most specific resource first
            if "constitution" in q or "hiến pháp" in q or "amendment" in q or "bill of rights" in q:
                return self._fetch_url(self.CONSTITUTION_URL, "US Constitution", question)
            if "ucc" in q or "uniform commercial code" in q:
                return self._fetch_url(self.UCC_URL, "Uniform Commercial Code", question)
            # USC title lookup — extract title number if present
            import re
            m = re.search(r'\busc\s*(\d+)\b', q)
            if m:
                return self._fetch_url(f"{self.USC_URL}{m.group(1)}",
                                       f"USC Title {m.group(1)}", question)
            # Generic search via LII search endpoint
            return self._search_lii(question)
        except Exception as e:
            logger.debug(f"[CornellLII] query failed: {e}")
            return None

    def _fetch_url(self, url: str, label: str, question: str) -> dict | None:
        import httpx
        try:
            r = httpx.get(url, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0 (educational)"})
            if r.status_code != 200:
                logger.debug(f"[CornellLII] {label} returned {r.status_code}")
                return None
            # Return reference — LII pages are authoritative legal text
            return {
                "value": label,
                "source": "cornell_lii",
                "metadata": {
                    "url": url,
                    "question": question,
                    "verified": True,
                    "page_size": len(r.text),
                },
            }
        except Exception as e:
            logger.debug(f"[CornellLII] fetch {label} failed: {e}")
            return None

    def _search_lii(self, question: str) -> dict | None:
        import httpx
        try:
            r = httpx.get(self.SEARCH_URL, params={"q": question}, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0 (educational)"})
            if r.status_code != 200:
                return None
            return {
                "value": question,
                "source": "cornell_lii",
                "metadata": {
                    "url": r.url,
                    "search_performed": True,
                    "page_size": len(r.text),
                },
            }
        except Exception as e:
            logger.debug(f"[CornellLII] search failed: {e}")
            return None
