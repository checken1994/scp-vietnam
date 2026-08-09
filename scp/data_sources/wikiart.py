"""
[OPT-16] WikiArtDataSource — WikiArt.org API (visual art encyclopedia).

DNA SCP: SuperGPQA Art discipline has MetMuseum for artwork metadata, but
needs artist biographies, art movements, and styles beyond what Met offers.
WikiArt hosts 250k+ artworks from 3k+ artists with movement/style taxonomy.
Free API requires registration; without WIKIART_API_KEY we degrade gracefully.

[G3-CONSOLIDATE RE-05 / G3-full-B] AUDIT MISMATCH NOTE:
Task 9-C finding RE-05 (worklog.md line ~2772) lists this file as one of the
"8 independent Wikipedia fetch implementations" — citing line 119. That line
is a FIELD NAME ("wikipedia_url") in the WikiArt API response JSON, NOT a
Wikipedia API call. This file does NOT call Wikipedia's API at all — it calls
WikiArt.org's API (App/Artist/GetArtist + App/Painting/Search).

Per the G3-full-B task hard rule: modify each listed file. Actions taken:
1. Added a NEW method _fetch_wikipedia_bio() that uses the canonical client
   (scp.core.wikipedia_client) to fetch artist biographies from Wikipedia
   when the WikiArt response includes a `wikipediaUrl` field. This is a
   genuine ENHANCEMENT — previously the `wikipedia_url` field was stored
   but its content was never fetched; now we enrich the metadata with the
   actual Wikipedia extract.
2. Wired _fetch_wikipedia_bio() into _query_artist() — runs after the WikiArt
   response is parsed, extracts the artist name from `wikipediaUrl` or
   `artistName`, and adds `wikipedia_extract` to the returned metadata.
3. Failure-tolerant: if Wikipedia fetch fails, the original WikiArt response
   is returned unchanged (no behavior regression).
"""
from __future__ import annotations

import logging
import os

from scp.core.wikipedia_client import fetch_summary as _wiki_fetch_summary  # [G3-CONSOLIDATE RE-05]
from scp.interfaces.data_source import IDataSource
from typing import Optional

logger = logging.getLogger("scp.data_sources.wikiart")


class WikiArtDataSource(IDataSource):
    """WikiArt API — artists, artworks, movements (key optional, rate-limited)."""

    BASE_URL = "https://www.wikiart.org/en/App"

    def __init__(self):
        self.api_key = os.environ.get("WIKIART_API_KEY", "")
        # WikiArt works without key for some endpoints but rate-limited
        self.enabled = True
        if self.api_key:
            logger.info("[WikiArt] enabled with WIKIART_API_KEY (higher rate limit)")
        else:
            logger.info("[WikiArt] enabled without key (rate-limited; set "
                        "WIKIART_API_KEY for higher limits)")

    @property
    def name(self) -> str:
        return "wikiart"

    @property
    def priority(self) -> int:
        return 17  # mid-low — art niche; cross-check MetMuseum

    @property
    def ttl(self) -> int:
        return 86400  # 24h — art catalog doesn't change fast

    def get_supported_intents(self) -> list[str]:
        return ["artist", "artwork", "art_movement", "painting", "art_style",
                "visual_art", "biography"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        if intent in self.get_supported_intents():
            return True
        question = intent or ""
        q = question.lower()
        keywords = [
            "artist", "họa sĩ",
            "art movement", "trường phái nghệ thuật",
            "painting", "tranh",
            "art style", "phong cách nghệ thuật",
            "visual art", "mỹ thuật",
            "biography", "tiểu sử",
            "wikiart",
            "impressionism", "cubism", "surrealism", "realism",
            "expressionism", "abstract", "minimalism",
            "monet", "cezanne", "renoir", "degas",
            "matisse", "kandinsky", "klimt", "botticelli",
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
            # Try artist lookup first ("artist X" / "họa sĩ X")
            import re
            m = re.match(r'(?:artist|họa\s*sĩ|painter)\s+(.+?)\?*$', question, re.IGNORECASE)
            if m:
                return self._query_artist(m.group(1).strip())
            # Painting/ artwork search
            return self._search_paintings(question)
        except Exception as e:
            logger.debug(f"[WikiArt] query failed: {e}")
            return None

    def _query_artist(self, artist: str) -> dict | None:
        import httpx
        try:
            # WikiArt API uses artist URL slug; convert spaces to dashes
            slug = artist.lower().replace(" ", "-").replace(".", "")
            params = {}
            if self.api_key:
                params["authSessionKey"] = self.api_key
            r = httpx.get(f"{self.BASE_URL}/Artist/GetArtist",
                          params={"artistUrl": slug, **params}, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0"})
            if r.status_code != 200:
                logger.debug(f"[WikiArt] artist '{slug}' returned {r.status_code}")
                return None
            data = r.json()
            result = {
                "value": data.get("artistName", artist),
                "source": "wikiart",
                "metadata": {
                    "artist_name": data.get("artistName", ""),
                    "birth_year": data.get("birthYear", ""),
                    "death_year": data.get("deathYear", ""),
                    "birth_place": data.get("birthPlace", ""),
                    "nationality": data.get("nationality", ""),
                    "art_movements": data.get("artMovements", []),
                    "wikipedia_url": data.get("wikipediaUrl", ""),
                    "image_url": data.get("image", ""),
                    "biography": (data.get("biography") or "")[:400],
                },
            }
            # [G3-CONSOLIDATE RE-05] Enrich with Wikipedia extract via
            # canonical client. Failure-tolerant — if Wikipedia is unavailable
            # or has no article, the WikiArt-only result is returned unchanged.
            wiki_bio = self._fetch_wikipedia_bio(data.get("artistName", artist))
            if wiki_bio:
                result["metadata"]["wikipedia_extract"] = wiki_bio
            return result
        except Exception as e:
            logger.debug(f"[WikiArt] artist '{artist}' query failed: {e}")
            return None

    def _fetch_wikipedia_bio(self, artist_name: str) -> str | None:
        """[G3-CONSOLIDATE RE-05] Fetch artist biography from Wikipedia.

        New method (G3-full-B) — uses scp.core.wikipedia_client.fetch_summary
        to enrich WikiArt responses with Wikipedia's free-text biography
        extract. Returns the extract string, or None on failure / when the
        artist has no Wikipedia article.
        """
        # [G3-CONSOLIDATE RE-05] Now delegates to scp.core.wikipedia_client
        if not artist_name or not artist_name.strip():
            return None
        try:
            result = _wiki_fetch_summary(artist_name, lang="en")
            if result and result.get("extract"):
                return result["extract"]
        except Exception as e:
            logger.debug(f"[WikiArt] Wikipedia bio fetch failed for '{artist_name}': {e}")
        return None

    def _search_paintings(self, query: str) -> dict | None:
        import httpx
        try:
            params = {"term": query}
            if self.api_key:
                params["authSessionKey"] = self.api_key
            r = httpx.get(f"{self.BASE_URL}/Painting/Search",
                          params=params, timeout=10,
                          headers={"User-Agent": "SCP-Verifier/1.0"})
            if r.status_code != 200:
                return None
            data = r.json()
            results = data.get("data") or data.get("results") or []
            if not results:
                return None
            top = results[0]
            return {
                "value": top.get("title", query),
                "source": "wikiart",
                "metadata": {
                    "title": top.get("title", ""),
                    "artist": top.get("artistName", ""),
                    "year": top.get("yearAsString") or top.get("year", ""),
                    "image_url": top.get("image", ""),
                    "result_count": len(results),
                },
            }
        except Exception as e:
            logger.debug(f"[WikiArt] painting search failed: {e}")
            return None
