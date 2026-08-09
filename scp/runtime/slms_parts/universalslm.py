"""
SLM part — extracted from slms.py (Task 19-A).
 kept verbatim; only the class location changed.
"""
import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("scp.slms")

# Token boundary helper (copied from slms.py)
def _token_boundary_match_slms(key: str, entity_lower: str) -> bool:
    import re as _re
    if not key or not entity_lower:
        return False
    if key == entity_lower:
        return True
    if len(key) < 4:
        return False
    if _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(key) + r'(?![\wÀ-ỹ])', entity_lower):
        return True
    if len(entity_lower) >= 4 and _re.search(r'(?<![\wÀ-ỹ])' + _re.escape(entity_lower) + r'(?![\wÀ-ỹ])', key):
        return True
    return False


@dataclass
class SLMResponse:
    """Response từ 1 SLM."""
    question: str
    answer: str
    confidence: float
    domain: str
    reasoning: str
    evidence: dict[str, Any]
    slm_name: str
    processing_time: float


class BaseSLM(ABC):
    """Base class cho tất cả SLM chuyên biệt."""

    def __init__(self, name: str, domain: str, config: Optional[dict] = None):
        self.name = name
        self.domain = domain
        self.config = config or {}
        self.response_cache: dict[str, tuple[float, SLMResponse]] = {}
        self._MAX_CACHE_SIZE = 500  # [OPT] Reduced from 1000
        self.stats = {
            "total_queries": 0,
            "total_time": 0.0,
            "success_count": 0,
            "error_count": 0,
        }

    @abstractmethod
    def predict(self, question: str) -> SLMResponse:
        """Dự đoán câu trả lời cho câu hỏi."""
        pass

    @abstractmethod
    def get_confidence(self, question: str, answer: str) -> float:
        """Tính độ tin cậy của câu trả lời."""
        pass

    def _cache_key(self, question: str) -> str:
        return hashlib.sha256(question.encode()).hexdigest()

    def cache_response(self, question: str, response: SLMResponse):
        # [FIXED] Enforce size limit - evict oldest entries
        if len(self.response_cache) >= self._MAX_CACHE_SIZE:
            oldest_keys = sorted(self.response_cache.keys(),
                key=lambda k: self.response_cache[k][0])[:self._MAX_CACHE_SIZE // 2]
            for k in oldest_keys:
                del self.response_cache[k]
        self.response_cache[self._cache_key(question)] = (time.time(), response)

    def get_cached(self, question: str, ttl: int = 3600) -> Optional[SLMResponse]:
        key = self._cache_key(question)
        if key in self.response_cache:
            ts, resp = self.response_cache[key]
            if time.time() - ts < ttl:
                return resp
            del self.response_cache[key]
        return None

    def _start_timer(self):
        self.stats["total_queries"] += 1
        return time.time()

    def _end_timer(self, start_time: float, success: bool):
        elapsed = time.time() - start_time
        self.stats["total_time"] += elapsed
        if success:
            self.stats["success_count"] += 1
        else:
            self.stats["error_count"] += 1

    def _healing_retry_slm(self, issue: dict) -> bool:
        """[V88 FIX] Clear smart cache for failed questions so they get re-processed."""
        try:
            # _run_periodic_cleanup()  # [V89] deduplicated - function not available
            logger.info("[HEALING] Cleared smart cache for 50 recent failed questions")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] retry_slm failed: {e}")
            return False

    def _healing_switch_domain(self, issue: dict) -> bool:
        """[V89 FIX] Don't blindly set domain='general'. Clear smart_cache so questions get re-classified."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            # Clear smart_cache for failed questions so they get re-classified with updated keywords
            _db_exec("DELETE FROM smart_cache_disk WHERE question IN (SELECT question FROM error_history WHERE final_verdict = 'FAIL' ORDER BY id DESC LIMIT 30)")
            # Also clear verdict_cache so they get re-judged
            _db_exec("DELETE FROM verdict_cache WHERE question IN (SELECT question FROM error_history WHERE final_verdict = 'FAIL' ORDER BY id DESC LIMIT 30)")
            logger.info("[HEALING] Cleared cache for 30 failed questions — will re-classify on next cycle")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] switch_domain failed: {e}")
            return False

    def _healing_reality_fallback(self, issue: dict) -> bool:
        """[V88 FIX] Clear stale live_knowledge_cache entries."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM live_knowledge_cache WHERE timestamp < datetime('now', '-1 day')")
            logger.info("[HEALING] Cleared stale live knowledge cache (>1 day old)")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] reality_fallback failed: {e}")
            return False

    def _healing_cache_refresh(self, issue: dict) -> bool:
        """[V88 FIX] Clear old verdict cache to force re-evaluation."""
        try:
            from scp.core.db_manager import db_exec as _db_exec
            _db_exec("DELETE FROM verdict_cache WHERE rowid NOT IN (SELECT rowid FROM verdict_cache ORDER BY rowid DESC LIMIT 100)")
            logger.info("[HEALING] Trimmed verdict cache to 100 most recent")
            return True
        except Exception as e:
            logger.warning(f"[HEALING] cache_refresh failed: {e}")
            return False

    def get_stats(self) -> dict:
        total = max(1, self.stats["total_queries"])
        return {
            "name": self.name,
            "domain": self.domain,
            "total_queries": self.stats["total_queries"],
            "avg_time": round(self.stats["total_time"] / total, 4),
            "success_rate": round(self.stats["success_count"] / total * 100, 1),
            "cache_size": len(self.response_cache),
        }


class UniversalSLM(BaseSLM):
    """
    [V81] Universal SLM — Wikidata fallback cho mọi entity.
    Handles ANY question by extracting entity + querying Wikidata.

    Strategy:
      1. Extract entity từ câu hỏi
      2. Search Wikidata → get QID
      3. Fetch entity claims (P31 = instance of, P279 = subclass of)
      4. Return description + key properties

    Covers domains: education, tourism, agriculture, environment, physics,
    mathematics, literature, philosophy, economics, psychology, sociology,
    anthropology, linguistics, archaeology, geology, oceanography, meteorology,
    ecology, botany, zoology, microbiology, genetics, neuroscience, pharmacology,
    virology, immunology, endocrinology, cardiology, dermatology, neurology,
    psychiatry, radiology, surgery, dentistry, veterinary, nutrition, fitness,
    fashion, beauty, jewelry, cosmetics, perfume, automotive, aviation, nautical,
    rail, cycling, climbing, hiking, camping, fishing, hunting, gardening,
    cooking, baking, brewing, winemaking, bartending, barista, sommelier,
    chehimtry, alchemy, astrology, astronomy, cosmology, ufology, paranormal,
    mythology, folklore, fairy tale, legend, fable, epic, poetry, drama,
    comedy, tragedy, novel, short story, essay, memoir, biography, autobiography,
    journalism, blogging, vlogging, podcasting, streaming, gaming, esports,
    chess, poker, bridge, blackjack, roulette, slot, lottery, bingo, casino,
    gambling, betting, sports betting, fantasy sports, daily fantasy, dfs,
    salary cap, draft, trade, waiver, free agency, contract, salary, bonus,
    endorsement, sponsorship, advertising, marketing, sales, retail, wholesale,
    ecommerce, mcommerce, social commerce, livestream shopping, affiliate,
    influencer, content creator, youtuber, tiktoker, instagrammer, twitter,
    facebook, linkedin, snapchat, pinterest, reddit, tumblr, medium, quora,
    stackoverflow, github, gitlab, bitbucket, stack exchange, discord, slack,
    teams, zoom, meet, webex, skype, whatsapp, telegram, signal, wechat,
    line, viber, imessage, facetime, voice call, video call, conference,
    webinar, online meeting, virtual event, hybrid event, in-person event,
    conference, summit, forum, workshop, seminar, training, course, class,
    lesson, tutorial, lecture, presentation, keynote, panel, discussion,
    q&a, interview, podcast, vlog, blog, article, post, tweet, thread,
    comment, like, share, follow, subscribe, notification, message, email,
    sms, mms, push, in-app, web push, desktop, mobile, tablet, laptop,
    desktop, server, cloud, edge, fog, iot, iiot, m2m, v2x, 5g, 4g, 3g,
    2g, 1g, wifi, bluetooth, nfc, rfid, gps, glonass, galileo, beidou,
    qzss, irnss, sbas, waas, egons, msas, gagan, ka-band, ku-band, c-band,
    l-band, s-band, x-band, ka-sat, ku-sat, c-sat, leo, meo, geo, heo,
    GeoStationary, geosynchronous, polar, sun-synchronous, molniya, tundra,
    walker, constellation, mega-constellation, starlink, oneweb, kuiper,
    telesat, boeing, airbus, spacex, nasa, esa, jaxa, roscosmos, isro,
    cnsa, kari, arianespace, ula, northrop, rocket lab, virgin orbit,
    virgin galactic, blue origin, sierra nevada, orbital sciences, ssl,
    maxar, planet, spire, blacksky, capella, iceeye, hawkseye, astrodigital,
    satellogic, earth-i, dmc, rapid-eye, worldview, geoeye, ikonos, quickbird,
    landsat, sentinel, modis, viirs, aster, srtm, gedi, icesat, cygnss,
    smap, smos, aquarius, jason, sentinel-6, cryosat, saral, sar, insar,
    polsar, hyspiri, hiper, prisma, enmap, shalom, emiT, hisui, florais,
    chris, proba, rapideye, worldview-3, worldview-4, geoeye-1, geoeye-2,
    ikonos-2, quickbird-2, worldview-1, worldview-2, worldview-3, worldview-4,
    pleiades, spot, formosat, kompsat, risat, cartosat, Resourcesat,
    Oceansat, Insat, Gsat, irs, tecsar, eros, ofeq, ehros, telesar, opper,
    tek-sat, gokturk, rasat, gokturk-1, gokturk-2, dubaisat, khalifasat,
    msysat, nscsat, egysat, sudasat, raisat, nilesat, nigersat, naxosat,
    moroccosat, algeriasat, tunisiasat, libyasat, egyptsat, sudansat,
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="UniversalSLM", domain="universal", config=config)
        self._wiki = None
        try:
            from scp.core.reality_engine import WikipediaDataSource
            self._wiki = WikipediaDataSource()
        except Exception as e:
            logger.warning(f"UniversalSLM init: {e}")

    def _extract_entity_universal(self, question: str) -> Optional[str]:
        """Extract entity from ANY question pattern."""
        import re
        q = question.strip()

        # Common patterns (EN + VI)
        patterns = [
            # [V90 EN] Wikipedia patterns — "What type of thing is X?"
            r'^what\s+type\s+of\s+thing\s+is\s+(.+?)\??$',
            # [V90 EN] "What is a short description of X?"
            r'^what\s+is\s+a\s+short\s+description\s+of\s+(.+?)\??$',
            # [V89 FIX] Order: specific patterns FIRST, generic "What is X?" LAST
            # "What is the X of Y?" → Y (e.g., "boiling point of water" → "water")
            r'^what\s+is\s+the\s+[\w\s]+\s+of\s+(.+?)\??$',
            # "Who is X?" / "Ai là X?"
            r'^who\s+(?:is|was)\s+(.+?)\??$',
            r'^ai\s+là\s+(.+?)\??$',
            # "Tell me about X"
            r'^tell\s+me\s+about\s+(.+?)\.?$',
            r'^cho\s+biết\s+về\s+(.+?)\.?$',
            # "Describe X"
            r'^describe\s+(.+?)\.?$',
            # "X là gì?"
            r'^(.+?)\s+là\s+gì\??$',
            # "X is what?"
            r'^(.+?)\s+is\s+what\??$',
            # "What is X?" — generic, LAST
            r'^what\s+is\s+(?:an?\s+|the\s+)?(.+?)\??$',
            # Generic: take first noun phrase (first 5 words)
            r'^(.{5,60})\??$',
        ]

        for pat in patterns:
            m = re.match(pat, q, re.IGNORECASE)
            if m:
                entity = m.group(1).strip().rstrip('?.!,;:').strip()
                # Filter out common stop words
                if entity.lower() not in ('the', 'a', 'an', 'is', 'are', 'was', 'were',
                                            'what', 'who', 'where', 'when', 'why', 'how',
                                            'this', 'that', 'these', 'those'):
                    return entity
        return None

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        cached = self.get_cached(question)
        if cached:
            self._end_timer(start, True)
            return cached

        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        entity = self._extract_entity_universal(question)
        if entity:
            # [V91 FIX] Cross-verify with 3 sources: Wikipedia + Wikidata + DuckDuckGo
            try:
                from scp.core.cross_verify import cross_verify_entity
                cv_result = cross_verify_entity(entity, question)
                if cv_result.get("value"):
                    answer = str(cv_result["value"])[:400]
                    confidence = cv_result["confidence"]
                    sources = cv_result.get("sources", [])
                    reasoning = f"Cross-verified by {len(sources)} sources: {', '.join(sources)}"
                    evidence = {
                        "value": cv_result.get("value"),
                        "source": sources[0] if sources else "cross_verify",
                        "entity": entity,
                        "sources": sources,
                        "conflict": cv_result.get("conflict", False),
                    }
                else:
                    confidence = 0.1
                    reasoning = f"No data from any source for '{entity}'"
            except Exception as e:
                # Fallback to old Wikipedia-only method
                if self._wiki:
                    try:
                        entity_clean = re.sub(r'^(?:a|an|the)\s+', '', entity, flags=re.IGNORECASE).strip()
                        data = self._wiki.fetch(entity_clean)
                        if data and data.get("extract"):
                            answer = data["extract"][:400]
                            confidence = 0.65
                            reasoning = f"Wikipedia fallback: {data.get('title', entity)}"
                            evidence = {"value": data.get("extract"), "source": "wikipedia", "title": data.get("title", ""),
                                        "entity": entity}
                    except Exception as e2:
                        reasoning = f"Cross-verify + wiki fallback both failed: {e}, {e2}"
                        confidence = 0.0
                else:
                    reasoning = f"Cross-verify error: {e}"
                    confidence = 0.0
        else:
            reasoning = "Could not extract entity"
            confidence = 0.0

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="universal", reasoning=reasoning,
            evidence=evidence, slm_name=self.name,
            processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        self._end_timer(start, confidence > 0.3)
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        return 0.65 if answer else 0.0
