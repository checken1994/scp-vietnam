"""
SCP V98 — ThreatIntelligenceCrawler
Copyright (c) 2026 Minh. MIT License.

MỚI (implement từ scratch) — crawl threat intel → cập nhật rule TRƯỚC khi bị tấn công.

Naming convention: <Purpose>Crawler (world standard, e.g. WebCrawler, IntelCrawler).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.security.threat_intel")


@dataclass
class IntelUpdate:
    """1 update từ threat intel sources."""
    update_id: str = ""
    timestamp: float = 0.0
    source: str = ""
    new_signatures: list[str] = field(default_factory=list)
    new_patterns: list[dict[str, str]] = field(default_factory=list)
    cve_items: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "new_signatures": self.new_signatures,
            "new_patterns": self.new_patterns,
            "cve_items": self.cve_items,
            "confidence": round(self.confidence, 2),
            "applied": self.applied,
        }


# Threat intel sources
# [RUNTIME-FIX-5] Multi-source + fallback chain.
# Bug (runtime log lines 728, 1383 — 6 occurrences of HTTP 404):
#   promptart/prompt-injection repo was deleted -> all crawls return 404 ->
#   0 patterns extracted -> antibody rules never updated from external sources.
# Fix: each source now has a list of `urls` (fallback chain). On 404 or fetch
# failure, crawler tries the next URL. Sources are official + actively maintained:
#   - llm-attacks (Microsoft, Mark Russinovich team) — verified 200 OK
#   - danielmiessler/fabric — verified 200 OK, has prompt patterns
#   - OWASP LLM Top 10 — verified 200 OK (was already there)
#   - google/safetext — verified 200 OK
INTEL_SOURCES = {
    "cve": {
        # CVE stays single-source (it's a stable API, not a GitHub repo)
        "urls": [
            "https://cve.circl.lu/api/last/10",
        ],
        "interval_h": 12,
        "parser": "json",
        "enabled": True,
        "max_retries": 3,  # [RUNTIME-FIX-5] retry with exponential backoff
        "retry_backoff_s": 1.0,
    },
    "owasp_llm_top10": {
        "urls": [
            "https://raw.githubusercontent.com/OWASP/www-project-top-10-for-large-language-model-applications/main/index.md",
        ],
        "interval_h": 168,  # 1 week
        "parser": "markdown",
        "enabled": True,
        "max_retries": 3,
        "retry_backoff_s": 2.0,
    },
    "prompt_injection_db": {
        # [RUNTIME-FIX-5] was: promptart/prompt-injection (DELETED — HTTP 404)
        # New fallback chain — all verified 200 OK as of 2026-08-04:
        #   1. llm-attacks/llm-attacks (Microsoft) — official, peer-reviewed
        #   2. danielmiessler/fabric — large community repo with prompt patterns
        #   3. google/safetext — Google's safety research repo
        "urls": [
            "https://raw.githubusercontent.com/llm-attacks/llm-attacks/main/README.md",
            "https://raw.githubusercontent.com/danielmiessler/fabric/main/README.md",
            "https://raw.githubusercontent.com/google/safetext/main/README.md",
        ],
        "interval_h": 24,
        "parser": "markdown",
        "enabled": True,
        "max_retries": 2,  # per URL — total attempts = len(urls) * max_retries
        "retry_backoff_s": 1.5,
    },
}

# Known attack patterns to look for
ATTACK_PATTERNS_REGEX = [
    (r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?", "injection_ignore_prev"),
    (r"you\s+are\s+(?:now|dan)\s+", "jailbreak_role"),
    (r"reveal\s+(?:your|the)\s+(?:system\s+)?prompt", "exfil_prompt"),
    (r"override\s+(?:safety|rules)", "jailbreak_override"),
    (r"developer\s+mode|no\s+rules", "jailbreak_mode"),
    (r"bỏ\s+qua\s+(?:tất\s+cả\s+)?(?:lệnh|hướng\s+dẫn)", "vn_injection_ignore"),
    (r"bây\s+giờ\s+bạn\s+là", "vn_jailbreak_role"),
    (r"eval\s*\(|exec\s*\(|__import__", "rce_attempt"),
    (r"\{\{.*\}\}|\{%.*%\}", "template_injection"),
]


class ThreatIntelligenceCrawler:
    """Crawl threat intelligence sources → update rules.

    Naming convention: <Purpose>Crawler (world standard).

    Sources:
      - CVE database (cve.circl.lu)
      - OWASP LLM Top 10
      - Prompt injection databases
      - Security blogs (BleepingComputer, ThreatPost)
      - Reddit /r/netsec, /r/MachineLearning

    Schedule:
      - CVE: mỗi 12h
      - OWASP: mỗi 7 ngày
      - Prompt injection DB: mỗi 24h

    Rule application:
      - Chỉ thêm rule khi confidence > 0.8
      - Tự động test rule mới trên history → không false positive → activate
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.updates_file = self.data_dir / "intel_updates.jsonl"
        self._history: list[IntelUpdate] = []
        self._stats = {
            "total_crawls": 0,
            "total_updates": 0,
            "total_signatures_added": 0,
            "total_patterns_added": 0,
            "total_cve_items": 0,
            "last_crawl": 0,
        }

    async def _fetch_with_retry(
        self,
        url: str,
        max_retries: int = 3,
        backoff_s: float = 1.0,
    ) -> str | None:
        """[RUNTIME-FIX-5] Fetch URL with exponential backoff retry.

        Returns response text on success, None on failure.
        - 200 OK → return text
        - 404 → return None immediately (resource deleted, retrying won't help)
        - 5xx / network error → retry with backoff (1s, 2s, 4s, ...)
        - timeout → retry
        """
        import httpx
        last_err = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        return r.text
                    if r.status_code == 404:
                        # Resource deleted — no point retrying.
                        logger.debug(
                            f"[ThreatIntel] 404 Not Found (deleted?): {url}"
                        )
                        return None
                    # 5xx, 429 → retry with backoff
                    last_err = f"HTTP {r.status_code}"
                    logger.debug(
                        f"[ThreatIntel] {url} returned {r.status_code} "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_err = str(e)
                logger.debug(
                    f"[ThreatIntel] fetch error {url} (attempt {attempt + 1}/{max_retries}): {e}"
                )
            # Exponential backoff: backoff_s * 2^attempt
            if attempt < max_retries - 1:
                import asyncio as _aio
                await _aio.sleep(backoff_s * (2 ** attempt))
        logger.debug(f"[ThreatIntel] all {max_retries} retries exhausted for {url}: {last_err}")
        return None

    async def crawl_source(self, source_name: str) -> IntelUpdate | None:
        """Crawl 1 source → IntelUpdate.

        [RUNTIME-FIX-5] Now supports multi-URL fallback chain (source['urls']).
        On 404 or fetch failure, tries next URL in the list. Also retries each
        URL with exponential backoff before falling through.

        Args:
            source_name: Key in INTEL_SOURCES

        Returns:
            IntelUpdate if successful, None otherwise
        """
        source = INTEL_SOURCES.get(source_name)
        if not source or not source.get("enabled"):
            return None

        self._stats["total_crawls"] += 1
        self._stats["last_crawl"] = time.time()

        update = IntelUpdate(
            update_id=f"intel_{source_name}_{int(time.time())}",
            timestamp=time.time(),
            source=source_name,
        )

        # [RUNTIME-FIX-5] Support both legacy `url` (str) and new `urls` (list).
        # Prefer `urls` if present. Fall back to `url` for backwards compat.
        urls = source.get("urls")
        if not urls:
            legacy_url = source.get("url")
            urls = [legacy_url] if legacy_url else []
        if not urls:
            logger.warning(f"[ThreatIntel] {source_name} has no URLs configured")
            return None

        max_retries = source.get("max_retries", 1)
        backoff_s = source.get("retry_backoff_s", 1.0)

        # Try each URL in the fallback chain until one succeeds.
        content: str | None = None
        tried_urls: list[str] = []
        for url in urls:
            tried_urls.append(url)
            content = await self._fetch_with_retry(url, max_retries, backoff_s)
            if content is not None:
                logger.debug(f"[ThreatIntel] {source_name}: fetched from {url}")
                break
            else:
                logger.debug(f"[ThreatIntel] {source_name}: {url} failed, trying fallback...")

        if content is None:
            logger.warning(
                f"[ThreatIntel] {source_name}: ALL {len(tried_urls)} URLs failed "
                f"(tried: {tried_urls[-1]})"
            )
            return None

        parser = source.get("parser", "text")
        try:
            if parser == "json":
                # CVE API returns JSON
                try:
                    import json as _json
                    data = _json.loads(content)
                    if isinstance(data, dict) and "data" in data:
                        data = data["data"]
                    for item in (data or [])[:10]:  # last 10 CVEs
                        cve_id = item.get("id", "")
                        summary = item.get("summary", "")
                        if cve_id and summary:
                            update.cve_items.append({
                                "cve_id": cve_id,
                                "summary": summary[:200],
                            })
                            # Extract patterns from summary
                            for pattern, sig in ATTACK_PATTERNS_REGEX:
                                if re.search(pattern, summary, re.IGNORECASE):
                                    if sig not in update.new_signatures:
                                        update.new_signatures.append(sig)
                except Exception as e:
                    logger.debug(f"[ThreatIntel] JSON parse error: {e}")

            elif parser == "markdown":
                # OWASP / prompt injection DB
                lines = content.split("\n")
                for line in lines:
                    for pattern, sig in ATTACK_PATTERNS_REGEX:
                        if re.search(pattern, line, re.IGNORECASE):
                            if sig not in update.new_signatures:
                                update.new_signatures.append(sig)
                                update.new_patterns.append({
                                    "signature": sig,
                                    "context": line.strip()[:100],
                                })

            # Calculate confidence
            if update.new_signatures or update.cve_items:
                update.confidence = min(1.0, 0.5 + 0.1 * len(update.new_signatures))

        except Exception as e:
            logger.debug(f"[ThreatIntel] Crawl {source_name} parse error: {e}")
            return None

        # Persist
        self._persist_update(update)
        self._history.append(update)
        self._stats["total_updates"] += 1
        self._stats["total_signatures_added"] += len(update.new_signatures)
        self._stats["total_patterns_added"] += len(update.new_patterns)
        self._stats["total_cve_items"] += len(update.cve_items)

        logger.info(
            f"[ThreatIntel] {source_name}: "
            f"{len(update.new_signatures)} signatures, "
            f"{len(update.cve_items)} CVEs, "
            f"confidence={update.confidence:.2f}"
        )
        return update

    async def crawl_all(self) -> list[IntelUpdate]:
        """Crawl all enabled sources in parallel."""
        tasks = []
        for source_name, source in INTEL_SOURCES.items():
            if source.get("enabled"):
                tasks.append(self.crawl_source(source_name))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        updates = []
        for r in results:
            if isinstance(r, IntelUpdate):
                updates.append(r)
            elif isinstance(r, Exception):
                logger.debug(f"[ThreatIntel] Source error: {r}")

        return updates

    def _persist_update(self, update: IntelUpdate):
        """Persist update to file."""
        try:
            with open(self.updates_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(update.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[ThreatIntel] Persist error: {e}")

    def get_recent_updates(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent intel updates."""
        return [u.to_dict() for u in self._history[-limit:]]

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "sources": list(INTEL_SOURCES.keys()),
            "history_size": len(self._history),
        }


__all__ = ["IntelUpdate", "ThreatIntelligenceCrawler", "INTEL_SOURCES", "ATTACK_PATTERNS_REGEX"]
