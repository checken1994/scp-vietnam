"""
SCP V105 — AI Threat Scanner (Layer 2)

Auto scan internet tìm AI nguy hiểm:
  - HuggingFace models có capability nguy hiểm
  - GitHub repos có sandbox escape code
  - arXiv papers mô tả attack methods
  - News về AI incidents (như OpenAI vs HuggingFace)
  - Reddit/Twitter discussions về AI attacks

Khi phát hiện → AdminAlerter cảnh báo real-time.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("scp.ai_threat_scanner")

DATA_DIR = Path(os.environ.get("SCP_DATA_DIR", "data"))
THREATS_DB = DATA_DIR / "ai_threats.jsonl"

# Threat indicators
THREAT_KEYWORDS = [
    "sandbox escape", "jailbreak", "prompt injection",
    "autonomous attack", "AI hacking", "model escape",
    "adversarial attack", "AI safety incident",
    "break containment", "agentic attack",
    "0-day", "exploit", "privilege escalation",
    "data exfiltration", "model inversion",
    "poisoning attack", "backdoor model",
]

SOURCES = {
    "huggingface_models": {
        "url": "https://huggingface.co/api/models",
        "interval": 3600,
        "params": {"sort": "lastModified", "limit": 50},
    },
    "github_ai_safety": {
        "url": "https://api.github.com/search/repositories",
        "interval": 3600,
        "params": {"q": "AI jailbreak OR sandbox escape OR adversarial attack", "sort": "updated", "per_page": 20},
    },
    "arxiv_safety": {
        "url": "http://export.arxiv.org/api/query",
        "interval": 1800,
        "params": {"search_query": "cat:cs.AI AND (abs:jailbreak OR abs:adversarial OR abs:safety)", "max_results": 20},
    },
    "news_ai_incidents": {
        "url": "https://newsapi.org/v2/everything",
        "interval": 900,
        "params": {"q": "AI attack OR AI safety OR AI incident OR AI breach", "sortBy": "publishedAt", "pageSize": 20},
        "needs_key": True,
    },
}

_last_scan: dict[str, float] = {}
_running = False
_thread = None


def _scan_huggingface() -> list[dict]:
    """Scan HuggingFace models mới cho threats."""
    threats = []
    try:
        resp = requests.get(SOURCES["huggingface_models"]["url"],
                           params=SOURCES["huggingface_models"]["params"], timeout=15)
        if resp.status_code == 200:
            for model in resp.json()[:50]:
                desc = (model.get("description", "") or "").lower()
                tags = [t.lower() for t in model.get("tags", [])]
                text = desc + " " + " ".join(tags)
                for kw in THREAT_KEYWORDS:
                    if kw in text:
                        threats.append({
                            "source": "huggingface",
                            "type": "dangerous_model",
                            "model_id": model.get("modelId", ""),
                            "keyword": kw,
                            "description": desc[:200],
                            "url": f"https://huggingface.co/{model.get('modelId','')}",
                            "detected_at": datetime.now().isoformat(),
                        })
                        break
    except Exception as e:
        logger.warning(f"HuggingFace scan error: {e}")
    return threats


def _scan_github() -> list[dict]:
    """Scan GitHub repos cho AI attack code."""
    threats = []
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
        resp = requests.get(SOURCES["github_ai_safety"]["url"],
                           params=SOURCES["github_ai_safety"]["params"],
                           headers=headers, timeout=15)
        if resp.status_code == 200:
            for repo in resp.json().get("items", [])[:20]:
                desc = (repo.get("description", "") or "").lower()
                name = repo.get("name", "").lower()
                text = desc + " " + name
                for kw in THREAT_KEYWORDS:
                    if kw in text:
                        threats.append({
                            "source": "github",
                            "type": "attack_repo",
                            "repo": repo.get("full_name", ""),
                            "keyword": kw,
                            "description": desc[:200],
                            "url": repo.get("html_url", ""),
                            "detected_at": datetime.now().isoformat(),
                        })
                        break
    except Exception as e:
        logger.warning(f"GitHub scan error: {e}")
    return threats


def _scan_arxiv() -> list[dict]:
    """Scan arXiv papers về AI attacks."""
    threats = []
    try:
        resp = requests.get(SOURCES["arxiv_safety"]["url"],
                           params=SOURCES["arxiv_safety"]["params"], timeout=15)
        if resp.status_code == 200:
            import re
            entries = re.findall(r'<entry>(.*?)</entry>', resp.text, re.DOTALL)
            for entry in entries[:20]:
                title_m = re.search(r'<title>(.*?)</title>', entry, re.DOTALL)
                summary_m = re.search(r'<summary>(.*?)</summary>', entry, re.DOTALL)
                if title_m:
                    title = title_m.group(1).strip()
                    summary = summary_m.group(1).strip()[:300] if summary_m else ""
                    text = (title + " " + summary).lower()
                    for kw in THREAT_KEYWORDS:
                        if kw in text:
                            threats.append({
                                "source": "arxiv",
                                "type": "attack_paper",
                                "title": title,
                                "keyword": kw,
                                "summary": summary,
                                "detected_at": datetime.now().isoformat(),
                            })
                            break
    except Exception as e:
        logger.warning(f"arXiv scan error: {e}")
    return threats


def _scan_news() -> list[dict]:
    """Scan news cho AI incidents."""
    threats = []
    api_key = os.environ.get("NEWSAPI_API_KEY")
    if not api_key:
        return threats
    try:
        params = SOURCES["news_ai_incidents"]["params"].copy()
        params["apiKey"] = api_key
        resp = requests.get(SOURCES["news_ai_incidents"]["url"], params=params, timeout=15)
        if resp.status_code == 200:
            for article in resp.json().get("articles", [])[:20]:
                title = (article.get("title", "") or "").lower()
                desc = (article.get("description", "") or "").lower()
                text = title + " " + desc
                for kw in THREAT_KEYWORDS:
                    if kw in text:
                        threats.append({
                            "source": "news",
                            "type": "ai_incident",
                            "title": article.get("title", ""),
                            "keyword": kw,
                            "url": article.get("url", ""),
                            "published": article.get("publishedAt", ""),
                            "detected_at": datetime.now().isoformat(),
                        })
                        break
    except Exception as e:
        logger.warning(f"News scan error: {e}")
    return threats


def _save_threats(threats: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(THREATS_DB, "a", encoding="utf-8") as f:
        for t in threats:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def _alert_threats(threats: list[dict]) -> None:
    """Send real-time alert khi phát hiện threat."""
    try:
        from scp.security.counter_response import AdminAlerter
        alerter = AdminAlerter()
        for t in threats:
            alerter.alert(
                event_type="ai_threat_detected",
                severity="HIGH",
                details={
                    "source": t.get("source"),
                    "type": t.get("type"),
                    "keyword": t.get("keyword"),
                    "description": t.get("description", t.get("title", ""))[:200],
                    "url": t.get("url", ""),
                },
            )
    except Exception as e:
        logger.warning(f"Alert failed: {e}")


def _scan_loop() -> None:
    # [FALSE-POS-FIX] F824: `global _running` removed — read-only access (while loop).
    # Assignment happens in start_scanner()/stop_scanner() which have their own global.
    logger.info("[AIThreatScanner] Background thread started")
    while _running:
        try:
            now = time.time()
            all_threats = []

            if now - _last_scan.get("huggingface", 0) > SOURCES["huggingface_models"]["interval"]:
                all_threats.extend(_scan_huggingface())
                _last_scan["huggingface"] = now

            if now - _last_scan.get("github", 0) > SOURCES["github_ai_safety"]["interval"]:
                all_threats.extend(_scan_github())
                _last_scan["github"] = now

            if now - _last_scan.get("arxiv", 0) > SOURCES["arxiv_safety"]["interval"]:
                all_threats.extend(_scan_arxiv())
                _last_scan["arxiv"] = now

            if now - _last_scan.get("news", 0) > SOURCES["news_ai_incidents"]["interval"]:
                all_threats.extend(_scan_news())
                _last_scan["news"] = now

            if all_threats:
                _save_threats(all_threats)
                _alert_threats(all_threats)
                logger.info(f"[AIThreatScanner] {len(all_threats)} threats found + alerted")

        except Exception as e:
            logger.error(f"[AIThreatScanner] Loop error: {e}")
        time.sleep(60)


def start_scanner() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_scan_loop, daemon=True, name="scp-ai-threat-scanner")
    _thread.start()
    logger.info("[AIThreatScanner] Started — scanning HuggingFace + GitHub + arXiv + News")


def stop_scanner() -> None:
    global _running
    _running = False
    if _thread:
        _thread.join(timeout=5)


def get_threat_stats() -> dict[str, Any]:
    if not THREATS_DB.exists():
        return {"total_threats": 0, "sources": {}}
    counts: dict[str, int] = {}
    total = 0
    try:
        with open(THREATS_DB, encoding="utf-8") as f:
            for line in f:
                try:
                    t = json.loads(line)
                    src = t.get("source", "unknown")
                    counts[src] = counts.get(src, 0) + 1
                    total += 1
                except Exception:  # noqa: S112
                    continue
    except Exception as _e:  # noqa: S110
        logger.debug(f"[silent-except] {_e}")
    return {"total_threats": total, "sources": counts, "running": _running}
