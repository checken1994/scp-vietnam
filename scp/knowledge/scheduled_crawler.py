"""
SCP V100 — Scheduled Data Crawler
==================================
Tự thu thập data đa lĩnh vực theo lịch trình — chạy song song với pipeline.

Crawler sources (free, no API key needed):
  - Wikipedia: random articles per domain
  - Wikidata: structured facts
  - REST Countries: geography data
  - Open-Meteo: weather data
  - PubChem: chemistry elements
  - arXiv: science papers

Schedule:
  - Mỗi 6h: crawl 1 domain rotation (math→physics→chemistry→...)
  - Mỗi 1h: refresh realtime data (weather, prices)
  - Mỗi 12h: curiosity questions (self-generated)

Storage:
  - Domain-separated JSONL files (via DomainKnowledgeStore)
  - SHA-256 integrity
  - TTL per Trust Tier
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.knowledge.crawler")


# Crawl targets per domain
CRAWL_TARGETS = {
    "geography": {
        "source": "restcountries",
        "url": "https://restcountries.com/v3.1/all?fields=name,capital,population,region",
        "parser": "restcountries",
        "questions_template": [
            "Thủ đô của {country} là gì?",
            "Dân số của {country} bao nhiêu?",
            "{country} thuộc khu vực nào?",
        ],
    },
    "chemistry": {
        "source": "pubchem",
        "url": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{entity}/property/MolecularFormula,MolecularWeight/JSON",
        "parser": "pubchem",
        "questions_template": [
            "Khối lượng phân tử của {entity} là bao nhiêu?",
            "Công thức phân tử của {entity} là gì?",
        ],
    },
    "science": {
        "source": "wikipedia",
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/{entity}",
        "parser": "wikipedia",
        "questions_template": [
            "{entity} là gì?",
            "Thông tin về {entity}?",
        ],
    },
}

# Entities to crawl per domain
CRAWL_ENTITIES = {
    "geography": ["Vietnam", "Japan", "France", "Germany", "Brazil", "India", "China",
                   "United States", "United Kingdom", "Russia", "Australia", "Canada",
                   "Italy", "Spain", "South Korea", "Thailand", "Singapore", "Indonesia"],
    "chemistry": ["water", "oxygen", "carbon dioxide", "glucose", "ethanol",
                   "sodium chloride", "methane", "ammonia", "sulfuric acid", "hydrogen"],
    "science": ["gravity", "photosynthesis", "DNA", "evolution", "relativity",
                 "quantum mechanics", "black hole", "climate change", "atomic structure"],
}


@dataclass
class CrawlResult:
    """Kết quả 1 crawl operation."""
    domain: str
    source: str
    entity: str
    question: str
    answer: str
    source_url: str
    confidence: float
    success: bool
    error: str = ""
    timestamp: float = field(default_factory=time.time)


class ScheduledDataCrawler:
    """Tự thu thập data đa lĩnh vực theo lịch trình.

    Naming convention: <Purpose>Crawler (world standard).

    Runs in background thread, không block pipeline.
    """

    def __init__(self, knowledge_store=None, data_dir: str = "data"):
        self.knowledge_store = knowledge_store
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stats = {
            "total_crawls": 0,
            "total_success": 0,
            "total_failures": 0,
            "total_records_stored": 0,
            "last_crawl": 0,
            "by_domain": {},
        }
        self._crawl_log = self.data_dir / "crawl_log.jsonl"

    async def crawl_domain(self, domain: str, max_entities: int = 5) -> list[CrawlResult]:
        """Crawl 1 domain — fetch data for N entities.

        Args:
            domain: Domain to crawl (geography, chemistry, science, ...)
            max_entities: Max entities to crawl

        Returns:
            List of CrawlResult
        """
        target = CRAWL_TARGETS.get(domain)
        if not target:
            logger.warning(f"[Crawler] No crawl target for domain: {domain}")
            return []

        entities = CRAWL_ENTITIES.get(domain, [])[:max_entities]
        results = []

        for entity in entities:
            self._stats["total_crawls"] += 1
            result = await self._crawl_entity(domain, target, entity)
            results.append(result)

            if result.success:
                self._stats["total_success"] += 1
                # Store in KnowledgeStore
                if self.knowledge_store:
                    self.knowledge_store.store(
                        question=result.question,
                        answer=result.answer,
                        domain=domain,
                        source=result.source,
                        source_url=result.source_url,
                        confidence=result.confidence,
                        collected_by="scheduled_crawl",
                        verified_by=[result.source],
                    )
                    self._stats["total_records_stored"] += 1
            else:
                self._stats["total_failures"] += 1

            # Track per-domain stats
            self._stats["by_domain"][domain] = self._stats["by_domain"].get(domain, 0) + 1

            # Log
            self._log_crawl(result)

        self._stats["last_crawl"] = time.time()
        logger.info(f"[Crawler] Domain '{domain}': {len(results)} crawled, "
                     f"{sum(1 for r in results if r.success)} success")
        return results

    async def _crawl_entity(self, domain: str, target: dict, entity: str) -> CrawlResult:
        """Crawl 1 entity from 1 source."""
        import httpx

        source = target["source"]
        url = target["url"].replace("{entity}", entity.replace(" ", "%20"))
        parser = target.get("parser", "text")

        result = CrawlResult(
            domain=domain,
            source=source,
            entity=entity,
            question="",
            answer="",
            source_url=url,
            confidence=0.5,
            success=False,
        )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    result.error = f"HTTP {r.status_code}"
                    return result

                data = r.json() if parser != "text" else r.text

                # Parse based on source
                if parser == "restcountries":
                    # Find entity in list
                    for country in data if isinstance(data, list) else [data]:
                        name = country.get("name", {}).get("common", "") if isinstance(country.get("name"), dict) else str(country.get("name", ""))
                        if entity.lower() in name.lower():
                            capital = country.get("capital", [""])[0] if country.get("capital") else "unknown"
                            population = country.get("population", 0)
                            region = country.get("region", "unknown")
                            # Generate Q&A
                            for template in target["questions_template"]:
                                q = template.format(country=name)
                                if "thủ đô" in q.lower() or "capital" in q.lower():
                                    a = f"Thủ đô của {name} là {capital}"
                                elif "dân số" in q.lower() or "population" in q.lower():
                                    a = f"Dân số của {name} là {population:,}"
                                elif "khu vực" in q.lower() or "region" in q.lower():
                                    a = f"{name} thuộc khu vực {region}"
                                else:
                                    a = f"{name}"
                                result.question = q
                                result.answer = a
                                result.confidence = 0.8
                                result.success = True
                                break
                            break

                elif parser == "wikipedia":
                    extract = data.get("extract", "")
                    title = data.get("title", entity)
                    if extract:
                        for template in target["questions_template"]:
                            q = template.format(entity=title)
                            a = extract[:500]
                            result.question = q
                            result.answer = a
                            result.confidence = 0.6
                            result.success = True
                            break

                elif parser == "pubchem":
                    properties = data.get("PropertyTable", {}).get("Properties", [{}])[0]
                    formula = properties.get("MolecularFormula", "unknown")
                    weight = properties.get("MolecularWeight", 0)
                    for template in target["questions_template"]:
                        q = template.format(entity=entity)
                        if "khối lượng" in q.lower() or "weight" in q.lower():
                            a = f"Khối lượng phân tử của {entity} là {weight}"
                        elif "công thức" in q.lower() or "formula" in q.lower():
                            a = f"Công thức phân tử của {entity} là {formula}"
                        else:
                            a = f"{entity}: {formula}, MW={weight}"
                        result.question = q
                        result.answer = a
                        result.confidence = 0.9  # PubChem = Tier 1
                        result.success = True
                        break

        except Exception as e:
            result.error = str(e)[:200]
            logger.debug(f"[Crawler] {domain}/{entity}: {e}")

        return result

    async def crawl_all_domains(self, max_per_domain: int = 3) -> dict[str, list[CrawlResult]]:
        """Crawl all domains in parallel."""
        tasks = []
        for domain in CRAWL_TARGETS:
            tasks.append(self.crawl_domain(domain, max_entities=max_per_domain))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for domain, result in zip(CRAWL_TARGETS.keys(), results):
            if isinstance(result, Exception):
                output[domain] = []
                logger.warning(f"[Crawler] {domain} error: {result}")
            else:
                output[domain] = result

        return output

    async def run_scheduled(self, interval_hours: float = 6):
        """Run crawler on schedule — every N hours.

        Should be started as background task.
        """
        logger.info(f"[Crawler] Scheduled crawler started — every {interval_hours}h")
        interval_s = interval_hours * 3600
        last_run = 0

        while True:
            now = time.time()
            if now - last_run >= interval_s:
                try:
                    results = await self.crawl_all_domains(max_per_domain=3)
                    total = sum(len(r) for r in results.values())
                    success = sum(1 for r in results.values() for c in r if c.success)
                    logger.info(f"[Crawler] Cycle: {total} crawled, {success} success")
                except Exception as e:
                    logger.warning(f"[Crawler] Cycle error: {e}")
                last_run = now
            await asyncio.sleep(60)

    def _log_crawl(self, result: CrawlResult):
        """Log crawl result."""
        try:
            entry = {
                "timestamp": result.timestamp,
                "domain": result.domain,
                "source": result.source,
                "entity": result.entity,
                "success": result.success,
                "question": result.question[:200],
                "answer": result.answer[:200],
                "error": result.error,
            }
            with open(self._crawl_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Silent except: {e}")

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "domains_configured": len(CRAWL_TARGETS),
            "entities_available": sum(len(v) for v in CRAWL_ENTITIES.values()),
        }


__all__ = ["CrawlResult", "ScheduledDataCrawler", "CRAWL_TARGETS", "CRAWL_ENTITIES"]
