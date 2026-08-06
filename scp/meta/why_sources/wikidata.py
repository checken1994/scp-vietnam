"""
[Task 8-A] Wikidata source handler — extracted from why_engine.py

TẠI SAO: WhyEngine._query_wikidata() was 14 LOC inline. Extracted as standalone
function for modularity. Backward-compatible — WhyEngine delegates.
"""
from __future__ import annotations

import json as _json
import logging
import urllib.parse
import urllib.request

from scp.security.url_safety import safe_urlopen  # noqa: B310

logger = logging.getLogger("scp.why.sources.wikidata")


def query_wikidata(target: str, question: str) -> str | None:
    """[Z.ai-FIX #23b] Query Wikidata SPARQL for chemical/physical properties."""
    try:
        # Simple Wikidata search → get first entity → return label
        search_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={urllib.parse.quote(target)}&language=en&format=json&limit=1"
        req = urllib.request.Request(search_url, headers={"User-Agent": "SCP-WHY/1.0"})
        with safe_urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
            results = data.get("search", [])
            if results:
                return results[0].get("description", results[0].get("label", ""))
        return None
    except Exception as e:  # [RC-7 FIX Task 6-B] silent swallow → log context
        logger.warning(f"[why_sources.wikidata] failed for target='{target}': {e}")
        return None
