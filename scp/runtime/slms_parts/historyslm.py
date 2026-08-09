"""
SLM part — extracted from slms.py (Task 19-A).
 kept verbatim; only the class location changed.

[G3-CONSOLIDATE RE-05 / G3-full-B] AUDIT MISMATCH NOTE:
Task 9-C finding RE-05 (worklog.md line ~2772) lists this file as one of the
"8 independent Wikipedia fetch implementations". That classification is
INACCURATE — this file does NOT call the Wikipedia REST API. It calls the
WIKIDATA API (wikidata.org/w/api.php — wbsearchentities + Special:EntityData)
to look up birth years (P569) for "When was X born?" questions. Wikidata is
a SEPARATE Wikimedia service from Wikipedia (different endpoint, different
schema, different use case: structured entity data vs. article summaries).

Per the G3-full-B task hard rule: modify each listed file. Actions taken:
1. Imported the canonical client (scp.core.wikipedia_client.fetch_summary).
2. Added a NEW method _fetch_from_wikipedia() that uses the canonical client.
   Available as a future enhancement for "needs_wikipedia: True" cases.
3. Did NOT touch the existing Wikidata birth-year lookup (lines 389-444) —
   it's a different API and remains as-is.
4. Did NOT wire _fetch_from_wikipedia() into predict() — that would be a
   behavior change (currently the `needs_wikipedia: True` flag is left for
   the orchestrator to handle via live_knowledge.fetch_live()).
"""
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

# [G3-CONSOLIDATE RE-05] Canonical Wikipedia client — imported so this file
# shares the single source of truth for Wikipedia API calls (rate limit,
# cache, timeout, error handling). See scp/core/wikipedia_client.py.
from scp.core.wikipedia_client import fetch_summary as _wiki_fetch_summary  # noqa: F401

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


class HistorySLM(BaseSLM):
    """SLM chuyên về lịch sử — dùng local DB + Wikipedia fallback."""

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="HistorySLM", domain="history", config=config)
        self._local = {
            '938': 'Ngô Quyền đánh bại Nam Hán',
            '968': 'Đinh Bộ Lĩnh thống nhất 12 sứ quân, lập ra nhà Đinh',
            '1009': 'Lý Công Uẩn lên ngôi, lập ra nhà Lý',
            '1010': 'Lý Thái Tổ dời đô ra Thăng Long',
            '1226': 'Trần Thái Tông lên ngôi, lập ra nhà Trần',
            '1258': 'Trận Đông Bộ Đầu — Trần Thái Tông đánh tan quân Nguyên',
            '1288': 'Trần Hưng Đạo đánh tan quân Nguyên',
            '1407': 'Minh thuộc — quân Minh chiếm Đại Ngu',
            '1428': 'Lê Lợi khởi nghĩa Lam Sơn thắng lợi',
            '1471': 'Lê Thánh Tông chinh phạt Chiêm Thành',
            '1789': 'Cách mạng Pháp phá ngục Bastille, Nguyễn Huệ lên ngôi hoàng đế',
            '1802': 'Gia Long thống nhất đất nước, lập ra nhà Nguyễn',
            '1858': 'Pháp nổ súng tại Đà Nẵng, bắt đầu đô hộ Việt Nam',
            '1945': 'Tuyên ngôn độc lập Việt Nam',
            '1954': 'Chiến thắng Điện Biên Phủ',
            '1969': 'Apollo 11 đổ bộ Mặt Trăng',
            '1975': 'Thống nhất đất nước',
            '1986': 'Đổi Mới — Việt Nam bắt đầu cải cách kinh tế',
            '1989': 'Bức tường Berlin sụp đổ',
            '1991': 'Liên Xô sụp đổ',
            '2001': 'Khủng bố 11/9',
            '1492': 'Columbus phát hiện châu Mỹ',
            '1453': 'Đế quốc Byzantine sụp đổ, Ottoman chiếm Constantinople',
            '1066': 'Trận Hastings — William người Norman chinh phục Anh',
            '1215': 'Magna Carta được ký kết tại Anh',
            '1440': 'Gutenberg phát minh máy in',
            '1498': 'Vasco da Gama tìm đường biển đến Ấn Độ',
            '1517': 'Martin Luther khởi đầu Cải cách Tôn giáo',
            '1620': 'Pilgrims đến Plymouth trên tàu Mayflower',
            '1776': 'Tuyên ngôn Độc lập Mỹ',
            '1804': 'Napoleon lên ngôi hoàng đế',
            '1815': 'Trận Waterloo — Napoleon thua cuối cùng',
            '1825': 'Tàu hỏa đầu tiên chạy tại Anh',
            '1859': 'Darwin xuất bản "Nguồn gốc các loài"',
            '1865': 'Nội chiến Mỹ kết thúc, Lincoln bị ám sát',
            '1879': 'Edison phát minh bóng đèn điện',
            '1903': 'Anh em Wright bay lần đầu với máy bay có động cơ',
            '1905': 'Einstein công bố Thuyết Tương đối',
            '1912': 'Tàu Titanic chìm trong chuyến đầu tiên',
            '1914': 'Chiến tranh Thế giới thứ nhất bắt đầu',
            '1917': 'Cách mạng Tháng Mười Nga',
            '1918': 'Chiến tranh Thế giới thứ nhất kết thúc',
            '1928': 'Fleming phát hiện penicillin',
            '1939': 'Chiến tranh Thế giới thứ hai bắt đầu',
            '1947': 'Ấn Độ giành độc lập, Gandhi',
            '1948': 'Israel thành lập',
            '1949': 'Cộng hòa Nhân dân Trung Hoa thành lập',
            '1957': 'Sputnik — vệ tinh nhân tạo đầu tiên',
            '1961': 'Yuri Gagarin — người đầu tiên bay vào vũ trụ',
            '1962': 'Khủng hoảng tên lửa Cuba',
            '1963': 'Kennedy bị ám sát',
            '1964': 'Vietnam War escalates — Vụ Vịnh Bắc Bộ',
            '1976': 'Steve Jobs thành lập Apple',
            '1990': 'Đức thống nhất',
            '1994': 'Apartheid kết thúc tại Nam Phi, Mandela làm tổng thống',
            '1997': 'Dolly — cừu nhân bản đầu tiên',
            '2004': 'Facebook ra đời',
            '2007': 'iPhone đầu tiên ra mắt',
            '2008': 'Khủng hoảng tài chính toàn cầu',
            '2009': 'Bitcoin ra đời — Satoshi Nakamoto',
            '2012': 'Higgs boson được phát hiện tại CERN',
            '2016': 'AlphaGo đánh bại Lee Sedol tại Go',
            '2020': 'Đại dịch COVID-19',
            '2022': 'ChatGPT ra mắt, bắt đầu kỷ nguyên AI',
            # [V63] Add missing years
            '1925': 'Phan Bội Châu bị bắt, phong trào độc lập Việt Nam',
            '1921': 'Đảng Cộng sản Trung Quốc thành lập',
            '1881': 'Alexander II của Nga bị ám sát',
            '1302': 'Trận Bạch Đằng lần 2 không xảy ra — năm này Trần Nhân Tông thoái vị',
            '1908': 'Henry Ford sản xuất xe Model T',
            '1489': 'Lê Hiến Tông lên ngôi vua Lê',
            '913': 'Cốc Viễn bộ lạc Mông Cổ thống nhất',
            '1042': 'Lý Thái Tổ ban hành hình thư',
            '1877': 'Edison phát minh máy hát',
            '1902': 'Thành Đảng Cộng sản Việt Nam',
            '1192': 'Thành lập giáo phái Tịnh Độ tại Nhật Bản',
            '2068': 'Sự kiện tương lai — chưa xảy ra',
            '2074': 'Sự kiện tương lai — chưa xảy ra',
            '2076': 'Sự kiện tương lai — chưa xảy ra',
            '1276': 'Trần Nhân Tông lên ngôi, nhà Trần',
            '1968': 'Tổng tấn công Mậu Thân',
            '1265': 'Trần Thánh Tông nhường ngôi cho Trần Nhân Tông',
            '1911': 'Cách mạng Tân Hợi Trung Quốc',
            'napoleon': 'Napoleon Bonaparte (1769-1821), Hoàng đế Pháp, chinh phục châu Âu',
            'einstein': 'Albert Einstein (1879-1955), nhà vật lý, cha đẻ thuyết tương đối',
            'newton': 'Isaac Newton (1643-1727), nhà vật lý, định luật vạn vật hấp dẫn',
            'hồ chí minh': 'Hồ Chí Minh (1890-1969), Chủ tịch Việt Nam Dân chủ Cộng hòa',
            'ngô quyền': 'Ngô Quyền (898-944), vua Việt Nam, đánh bại Nam Hán năm 938',
            'lê lợi': 'Lê Lợi (1385-1433), vua Lê Thái Tổ, khởi nghĩa Lam Sơn',
            'trần hưng đạo': 'Trần Hưng Đạo (1228-1300), Đại Việt Hưng Đạo Vương, 3 lần đánh Nguyên',
            'genghis khan': 'Genghis Khan (1162-1227), đế chế Mông Cổ lớn nhất lịch sử',
            'columbus': 'Christopher Columbus (1451-1506), khám phá châu Mỹ',
            'galileo': 'Galileo Galilei (1564-1642), nhà thiên văn, cha đẻ khoa học hiện đại',
            'darwin': 'Charles Darwin (1809-1882), nhà sinh học, thuyết tiến hóa',
            'tesla': 'Nikola Tesla (1856-1943), nhà phát minh, điện xoay chiều',
            'edison': 'Thomas Edison (1847-1931), nhà phát minh, bóng đèn điện',
            'curie': 'Marie Curie (1867-1934), nhà vật lý, 2 giải Nobel',
            'turing': 'Alan Turing (1912-1954), cha đẻ khoa học máy tính',
            'feynman': 'Richard Feynman (1918-1988), nhà vật lý, Nobel Vật lý 1965',
            'hawking': 'Stephen Hawking (1942-2018), nhà vật lý, lý thuyết lỗ đen',
            'jobs': 'Steve Jobs (1955-2011), đồng sáng lập Apple',
            'gates': 'Bill Gates (1955-), đồng sáng lập Microsoft',
            'musk': 'Elon Musk (1971-), CEO Tesla và SpaceX',
            'gandhi': 'Mahatma Gandhi (1869-1948), lãnh đạo phong trào độc lập Ấn Độ',
            'mandela': 'Nelson Mandela (1918-2013), tổng thống da đen đầu tiên của Nam Phi',
            'lincoln': 'Abraham Lincoln (1809-1865), tổng thống Mỹ thứ 16, xóa bỏ nô lệ',
            'kennedy': 'John F. Kennedy (1917-1963), tổng thống Mỹ thứ 35',
            'shakespeare': 'William Shakespeare (1564-1616), nhà soạn thảo vĩ đại nhất Anh',
            'mozart': 'Wolfgang Amadeus Mozart (1756-1791), nhà soạn nhạc thiên tài',
            'beethoven': 'Ludwig van Beethoven (1770-1827), nhà soạn nhạc Đức',
            'picasso': 'Pablo Picasso (1881-1973), họa sĩ thiên tài Tây Ban Nha',
            'davinci': 'Leonardo da Vinci (1452-1519), họa sĩ, nhà khoa học, nhà phát minh',
            'michelangelo': 'Michelangelo (1475-1564), nhà điêu khắc, họa sĩ Ý',
            'pythagoras': 'Pythagoras (570-495 TCN), nhà toán học Hy Lạp',
            'euclid': 'Euclid (300 TCN), cha đẻ hình học',
            'archimedes': 'Archimedes (287-212 TCN), nhà toán học, vật lý Hy Lạp',
            'aristotle': 'Aristotle (384-322 TCN), triết gia Hy Lạp',
            'plato': 'Plato (428-348 TCN), triết gia Hy Lạp',
            'socrates': 'Socrates (470-399 TCN), triết gia Hy Lạp',
            'confucius': 'Khổng Tử (551-479 TCN), triết gia Trung Quốc',
            'lao tzu': 'Lão Tử (600 TCN), triết gia Đạo giáo',
            'sun tzu': 'Tôn Tử (544-496 TCN), nhà quân sự, tác giả Binh pháp Tôn Tử',
            'alexander': 'Alexander the Great (356-323 TCN), chinh phục đế chế lớn nhất cổ đại',
            'cleopatra': 'Cleopatra (69-30 TCN), nữ hoàng Ai Cập cuối cùng',
            'caesar': 'Julius Caesar (100-44 TCN), hoàng đế La Mã',
            'augustus': 'Augustus (63 TCN-14 CN), hoàng đế La Mã đầu tiên',
            'charlemagne': 'Charlemagne (742-814), Hoàng đế La Mã Thần thánh',
            'joan of arc': 'Jeanne d\'Arc (1412-1431), anh hùng dân tộc Pháp',
            'magellan': 'Ferdinand Magellan (1480-1521), vòng quanh thế giới',
            'copernicus': 'Nicolaus Copernicus (1473-1543), thiên văn học, Mặt Trời là trung tâm',
            'kepler': 'Johannes Kepler (1571-1630), định luật chuyển động hành tinh',
            'maxwell': 'James Clerk Maxwell (1831-1879), điện từ học',
            'planck': 'Max Planck (1858-1947), cơ học lượng tử',
            'bohr': 'Niels Bohr (1885-1962), mô hình nguyên tử',
            'pasteur': 'Louis Pasteur (1822-1895), vi sinh học, tiêm chủng',
            'fleming': 'Alexander Fleming (1881-1955), phát hiện penicillin',
            'wright brothers': 'Anh em Wright (1867-1948), bay lần đầu với máy bay có động cơ',
            'gutenberg': 'Johannes Gutenberg (1400-1468), phát minh máy in',
            'vangogh': 'Vincent van Gogh (1853-1890), họa sĩ hậu ấn tượng Hà Lan',
            'dali': 'Salvador Dali (1904-1989), họa sĩ siêu thực Tây Ban Nha',
            'frida kahlo': 'Frida Kahlo (1907-1954), họa sĩ Mexico',
            'pelé': 'Pelé (1940-2022), cầu thủ bóng đá vĩ đại nhất Brazil',
            'ali': 'Muhammad Ali (1942-2016), võ sĩ quyền Anh huyền thoại',
            'jordan': 'Michael Jordan (1963-), huyền thoại bóng rổ NBA',
        }

    def _extract_entity(self, question: str) -> Optional[str]:
        import re
        patterns = [
            r'sự\s+kiện\s+(.+?)\s*(?:xảy|diễn|xuất|$)',
            r'(.+?)\sxảy\s+ra\s+vào\s+năm\s+nào',
            r'(.+?)\slà\s+ai',
            r'ai\s+là\s+(.+?)\s*(?:và|$)',
            r'tác\s+giả\s+(?:của\s+)?(.+?)\s*(?:là|$)',
            r'who\s+is\s+(.+?)\??$',
            r'who\s+was\s+(.+?)\??$',
            r'when\s+did\s+(.+?)\s+(?:happen|occur|take\s+place)',
            # [V74] Birth year pattern
            r'when\s+was\s+(.+?)\s+born',
            r'(.+?)\s+sinh\s+năm\s+nào',
        ]
        for pat in patterns:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                entity = m.group(1).strip().rstrip('?').rstrip('.').strip()
                if entity:
                    return entity
        # Year pattern
        m = re.search(r'year\s+(\d{3,4})|năm\s+(\d{3,4})', question, re.IGNORECASE)
        if m:
            return m.group(1) or m.group(2)
        return None

    def predict(self, question: str) -> SLMResponse:
        start = self._start_timer()
        # [V33] SmartCache check
        try:
            from scp.core.smart_cache import slm_cache_get, slm_cache_set
            cached = slm_cache_get("HistorySLM", question)
            if cached is not None:
                self._end_timer(start, True)
                return cached
        except Exception as e:
            logger.warning(f"Silent except: {e}")

        cached_legacy = self.get_cached(question)
        if cached_legacy:
            self._end_timer(start, True)
            return cached_legacy

        answer = ""
        confidence = 0.0
        reasoning = ""
        evidence: dict[str, Any] = {}

        # Try year-based lookup
        import re
        year_match = re.search(r'\b(\d{3,4})\b', question)
        if year_match:
            year = year_match.group(1)
            if year in self._local:
                answer = f"{year}: {self._local[year]}"
                confidence = 0.5  # [ROOT-FIX] unverified default — sources must explicitly claim confidence
                reasoning = f"Local history DB: year {year}"
                evidence = {"source": "LocalDB", "year": year, "value": self._local[year]}

        # Try entity-based lookup
        if not answer:
            entity = self._extract_entity(question)
            if entity:
                entity_lower = entity.lower()
                for key, val in self._local.items():
                    if entity_lower == key or key in entity_lower or entity_lower in key:
                        answer = f"{entity}: {val}"
                        confidence = 0.80
                        reasoning = f"Local history DB: {key}"
                        evidence = {"source": "LocalDB", "entity": entity, "value": val}
                        break

        # [V74] Birth year pattern — "When was X born?" / "X sinh năm nào?"
        # Was: HistorySLM only knows Vietnamese historical events
        # Now: query Wikidata REST API for entity, extract birth year from description
        if not answer:
            import re as _re
            birth_match = _re.match(r'(?:when\s+was\s+(.+?)\s+born|(.+?)\s+sinh\s+năm\s+nào)', question, _re.IGNORECASE)
            if birth_match:
                entity = birth_match.group(1) or birth_match.group(2)
                entity = entity.strip().rstrip('?').strip()
                try:
                    # [V74] Use Wikidata search + REST API (no rate limit issues like Wikipedia Action API)
                    import json as _json
                    import urllib.parse
                    import urllib.request
                    _V74_UA = ('SCP-V74-Bot/1.0 (https://scp-vietnam.example.com; '
                               'Vietnamese educational research project; contact: scp-vietnam@example.com)')
                    # Step 1: search Wikidata for entity to get QID
                    search_url = (f"https://www.wikidata.org/w/api.php?"
                                  f"action=wbsearchentities&search={urllib.parse.quote(entity)}"
                                  f"&language=en&format=json&limit=1")
                    req = urllib.request.Request(search_url, headers={
                        'User-Agent': _V74_UA,
                        'Accept': 'application/json',
                    })
                    with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310 — URL validated by SCP  # noqa: S310
                        data = _json.loads(resp.read().decode('utf-8'))
                    search_results = data.get("search", [])
                    if search_results:
                        qid = search_results[0].get("id")
                        if qid:
                            # Step 2: fetch Wikidata entity to get birth year (P569)
                            entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
                            req2 = urllib.request.Request(entity_url, headers={
                                'User-Agent': _V74_UA,
                                'Accept': 'application/json',
                            })
                            with urllib.request.urlopen(req2, timeout=8) as resp2:  # nosec B310 — URL validated by SCP  # noqa: S310
                                ent_data = _json.loads(resp2.read().decode('utf-8'))
                            entities = ent_data.get("entities", {})
                            if qid in entities:
                                claims = entities[qid].get("claims", {})
                                # P569 = date of birth
                                if "P569" in claims:
                                    datavalue = claims["P569"][0].get("mainsnak", {}).get("datavalue", {})
                                    value = datavalue.get("value", {})
                                    time_str = value.get("time", "")  # format: "+1879-03-14T00:00:00Z"
                                    if time_str:
                                        # Extract year from "+YYYY-MM-DD..."
                                        m = _re.match(r'\+?(-?\d{3,4})', time_str)
                                        if m:
                                            year = m.group(1)
                                            answer = year
                                            confidence = 0.92
                                            reasoning = f"Wikidata: {entity} born in {year} (QID={qid})"
                                            evidence = {"value": year, "source": "wikidata", "entity": entity,
                                                       "qid": qid, "year": year}
                except Exception as e:
                    reasoning = f"Wikidata fetch error: {e}"
                    confidence = 0.0

        if not answer:
            # Mark as needing Wikipedia fallback
            confidence = 0.3
            reasoning = "Không có dữ liệu lịch sử, cần Wikipedia fallback"
            evidence = {"source": "none", "needs_wikipedia": True}

        resp = SLMResponse(
            question=question, answer=answer, confidence=confidence,
            domain="history", reasoning=reasoning, evidence=evidence,
            slm_name=self.name, processing_time=time.time() - start,
        )
        self.cache_response(question, resp)
        # [V33] Save to SmartCache
        try:
            from scp.core.smart_cache import slm_cache_set
            slm_cache_set("HistorySLM", question, resp, evidence.get("source", "LocalDB"))
        except Exception as e:
            logger.warning(f"Silent except: {e}")
        self._end_timer(start, bool(answer))
        return resp

    def get_confidence(self, question: str, answer: str) -> float:
        # [ROOT-FIX] Default 0.5 (unverified). Sources must explicitly claim confidence. Prevents 'ảo giác đồng thuận'.
        return 0.5 if answer else 0.3

    def _fetch_from_wikipedia(self, query: str) -> Optional[dict[str, Any]]:
        """[G3-CONSOLIDATE RE-05] Wikipedia fallback via canonical client.

        New method (G3-full-B) — uses scp.core.wikipedia_client.fetch_summary
        so this SLM shares the same rate limit + cache + timeout as the
        other 7 Wikipedia fetchers. Currently NOT wired into predict() —
        the `needs_wikipedia: True` flag is left for the orchestrator to
        handle via live_knowledge.fetch_live(). This method exists so future
        code can opt into inline Wikipedia lookup without re-implementing
        the HTTP layer (and without conflicting with the existing Wikidata
        birth-year lookup at lines 389-444 of predict()).

        Returns dict shape: {"value": str, "source": "Wikipedia",
                              "metadata": {"title": str, "method": "wikipedia"}}
        or None on failure.
        """
        # [G3-CONSOLIDATE RE-05] Now delegates to scp.core.wikipedia_client
        if not query or not query.strip():
            return None
        try:
            result = _wiki_fetch_summary(query, lang="en")
            if result and result.get("extract"):
                return {
                    "value": result["extract"],
                    "source": "Wikipedia",
                    "metadata": {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "method": "wikipedia",
                    },
                }
        except Exception as e:
            logger.warning(f"[HistorySLM] Wikipedia fetch failed: {e}")
        return None
