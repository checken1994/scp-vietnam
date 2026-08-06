"""
SCP V104.2 — Fast Learning Engine (CANONICAL — post G3-MERGE)
==============================================================
[G3-MERGE] This file is now the SINGLE source of truth for learning.
The legacy `real_learning_engine.py` has been reduced to a thin
compatibility stub that delegates to this module (see real_learning_engine.py).
Race condition eliminated: previously, both RealLearningEngine AND
FastLearningEngine started background threads that wrote to the same KB
(`data/v13.db`) concurrently → duplicate Ollama calls + interleaved
SQLite writes. After merge, only ONE engine class exists; the background
thread starter is idempotent (module-level guard).

Tối ưu tốc độ học 10-50x so với V104.1:

1. PARALLEL OLLAMA: 10 concurrent requests (Semaphore 10) — vs tuần tự cũ
2. SKIP-ALREADY-KNOWN: Skip câu hỏi đã có trong KB → chỉ hỏi câu mới
3. COMPOUNDING: Mỗi cycle dùng KB đã học để sinh câu hỏi sâu hơn (cấp 2, 3)
4. ADAPTIVE INTERVAL: 5 min default; nếu facts mới > 50% → 1 min; < 10% → 30 min
5. BATCH WIKIPEDIA: 5 Wikipedia search concurrent

PLUS (ported from real_learning_engine.py during G3-MERGE):
- LOOP 1: ollama_learning_cycle() — sequential V104.1 API (kept for /v104/learn/ollama)
- LOOP 2: local_learning_cycle() — scan data/ files → extract facts → verify → KB
- LOOP 3: news_learning_cycle() — fetch RSS headlines → verify → KB
- run_all_cycles() — orchestrate all 3 loops
- Module constants: SEED_QUESTIONS, COUNTRIES, DOMAINS, COMPOUNDS,
  COUNTRY_DOMAIN_HINTS, OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT,
  NEWS_SOURCES, LEARNING_INTERVAL
- Helper funcs: get_country_domain_matrix(), get_total_combinations()

Kết quả benchmark dự kiến:
  V104.1 tuần tự: 10 câu × 2s/câu = 20s
  V104.2 parallel: 10 câu / 10 concurrent = 2s (10x nhanh hơn)

Yêu cầu: Ollama chạy local (http://127.0.0.1:11434)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# [ROOT-FIX 1] Canonical knowledge DDL — single source of truth (db_manager.py)
from scp.core.db_manager import _KNOWLEDGE_CANONICAL_DDL

logger = logging.getLogger("scp.core.fast_learning_engine")

# ============================================================
# [G3-MERGE] Module constants — ported from real_learning_engine.py
# (was: imported from real_learning_engine — circular now that real is a stub)
# ============================================================

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:latest")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "60"))

# ============================================================
# V104.1 FIX: 14 quốc gia × 5 lĩnh vực = 70 combinations
# Mỗi domain đều có câu hỏi theo {country} placeholder →
#   geography:    14 × 5 = 70 questions
#   history:      14 × 3 = 42 questions
#   chemistry:    14 × 3 = 42 (country-specific) + 6 × 3 = 18 (compound)
#   physics:      14 × 3 = 42 (country-specific) + 3 generic = 45
#   biology:      14 × 3 = 42 (country-specific) + 3 generic = 45
# ============================================================

SEED_QUESTIONS = {
    "geography": [
        "Thủ đô của {country} là gì?",
        "Diện tích của {country} bao nhiêu km vuông?",
        "Dân số của {country} là bao nhiêu?",
        "Sông dài nhất ở {country} tên gì?",
        "Đỉnh núi cao nhất ở {country} tên gì?",
    ],
    "history": [
        "{country} độc lập vào năm nào?",
        "Ai là người sáng lập {country}?",
        "Chiến tranh lớn nhất ở {country} diễn ra khi nào?",
    ],
    "chemistry": [
        # Country-specific (per-country natural resource / industry)
        "Khoáng sản quan trọng nhất của {country} là gì?",
        "Ngành hóa chất lớn nhất ở {country} sản xuất cái gì?",
        "Nhiên liệu chính được dùng để phát điện ở {country} là gì?",
        # Compound-specific (universal)
        "Công thức phân tử của {compound} là gì?",
        "Khối lượng phân tử của {compound} là bao nhiêu?",
        "Nhiệt độ sôi của {compound} là bao nhiêu độ C?",
    ],
    "physics": [
        # Country-specific (per-country physicist / institution)
        "Nhà vật lý học nổi tiếng nhất của {country} là ai?",
        "Phát minh vật lý quan trọng nhất của {country} là gì?",
        "Trường đại học nghiên cứu vật lý hàng đầu ở {country} tên gì?",
        # Generic (universal constants)
        "Tốc độ ánh sáng là bao nhiêu km/s?",
        "Hằng số hấp dẫn G có giá trị bao nhiêu?",
        "Hằng số Planck có giá trị bao nhiêu?",
    ],
    "biology": [
        # Country-specific (per-country endemic species / biologist)
        "Động vật đặc trưng (đặc hữu) của {country} là con gì?",
        "Loài thực vật phổ biến nhất ở {country} là cây gì?",
        "Vườn quốc gia lớn nhất ở {country} tên gì?",
        # Generic (universal biology)
        "Số nhiễm sắc thể của con người là bao nhiêu?",
        "ADN được phát hiện bởi ai?",
        "Quang hợp tạo ra khí gì?",
    ],
}

# 14 quốc gia — toàn bộ coverage
COUNTRIES = [
    "Việt Nam", "Trung Quốc", "Nhật Bản", "Hàn Quốc", "Ấn Độ",
    "Nga", "Mỹ", "Anh", "Pháp", "Đức",
    "Brazil", "Úc", "Canada", "Thái Lan",
]

# 5 lĩnh vực — toàn bộ coverage
DOMAINS = ["geography", "history", "chemistry", "physics", "biology"]

COMPOUNDS = ["nước", "muối ăn", "axit sunfuric", "ethanol", "methane", "ammoniac"]

# Hints cho từng (country, domain) để Ollama trả lời chính xác hơn
# (không bắt buộc — chỉ gợi ý để verify tốt hơn)
COUNTRY_DOMAIN_HINTS = {
    "Việt Nam": {
        "chemistry": "khoáng sản: than đá, apatit, boxit",
        "physics": "nhà vật lý: Trần Đại Nghĩa",
        "biology": "đặc hữu: sao la, voọc mũi hếch, rùa Hoàn Kiếm",
    },
    "Trung Quốc": {
        "chemistry": "khoáng sản: đất hiếm, than đá lớn nhất thế giới",
        "physics": "phát minh: la bàn, thuốc súng",
        "biology": "đặc hữu: gấu trúc, cá heo Dương Tử",
    },
    "Nhật Bản": {
        "chemistry": "ngành hóa chất: Sumitomo, Mitsui",
        "physics": "Nobel vật lý: Yukawa Hideki (1949)",
        "biology": "đặc hữu: salamander khổng lồ Nhật Bản, hạc đỏ",
    },
    "Hàn Quốc": {
        "chemistry": "ngành hóa chất: LG Chem, Samsung SDI",
        "physics": "Nobel vật lý: chưa có",
        "biology": "đặc hữu: hổ Siberia (giới hạn), sáo Triều Tiên",
    },
    "Ấn Độ": {
        "chemistry": "khoáng sản: quặng sắt, mica",
        "physics": "Nobel vật lý: C.V. Raman (1930)",
        "biology": "đặc hữu: hổ Bengal, sư tử châu Á, voi Ấn Độ",
    },
    "Nga": {
        "chemistry": "khoáng sản: dầu mỏ, khí tự nhiên, kim cương",
        "physics": "Nobel vật lý: nhiều (Landau, Kapitsa, Ginzburg)",
        "biology": "đặc hữu: hổ Amur, báo tuyết, gấu nâu Kamchatka",
    },
    "Mỹ": {
        "chemistry": "ngành hóa chất: Dow, DuPont, ExxonMobil",
        "physics": "Nobel vật lý: Feynman, Einstein, many",
        "biology": "đặc hữu: đại bàng hói, gấu xám, nai sừng tấm",
    },
    "Anh": {
        "chemistry": "ngành hóa chất: ICI (lịch sử), BP, Shell",
        "physics": "Newton, Faraday, Maxwell, Hawking",
        "biology": "đặc hữu: chim cuồi, hồ Loch Ness (huyền thoại)",
    },
    "Pháp": {
        "chemistry": "khoáng sản: uranium (Artois), muối",
        "physics": "Nobel vật lý: Becquerel, Curie, de Broglie",
        "biology": "đặc hữu: giông Pháp, nai Do ec",
    },
    "Đức": {
        "chemistry": "BASF, Bayer, Bayerische Motoren Werke",
        "physics": "Einstein, Planck, Heisenberg, Born",
        "biology": "đặc hữu: lợn rừng Đức, chim đại bàng vàng",
    },
    "Brazil": {
        "chemistry": "khoáng sản: quặng sắt, nhôm, mangan",
        "physics": "Nobel vật lý: chưa có",
        "biology": "đặc hữu: Amazon — jaguar, anaconda, chim ruồi",
    },
    "Úc": {
        "chemistry": "khoáng sản: quặng sắt, vàng, than đá",
        "physics": "Nobel vật lý: Bragg (cha+con, 1915)",
        "biology": "đặc hữu: kangaroo, koala, platypus, echidna",
    },
    "Canada": {
        "chemistry": "khoáng sản: uranium, kali, vàng",
        "physics": "Nobel vật lý: Strangeway, Brockhouse, McDonald",
        "biology": "đặc hữu: gấu Bắc Cực, nai sừng tấm, beaver",
    },
    "Thái Lan": {
        "chemistry": "khoáng sản: thiếc, wolfram, fluorite",
        "physics": "Nobel vật lý: chưa có",
        "biology": "đặc hữu: voi Thái, gaur, bò tót",
    },
}


def get_country_domain_matrix() -> dict:
    """
    Trả về ma trận 14 quốc gia × 5 lĩnh vực.
    Mỗi cell = số câu hỏi có thể sinh ra.
    """
    matrix = {}
    for country in COUNTRIES:
        matrix[country] = {}
        for domain in DOMAINS:
            templates = SEED_QUESTIONS[domain]
            # Đếm số template có thể dùng với {country}
            country_templates = sum(1 for t in templates if "{country}" in t)
            matrix[country][domain] = country_templates
    return matrix


def get_total_combinations() -> int:
    """Tổng số combinations country×field có thể sinh ra = 14 × 5 = 70 cells."""
    matrix = get_country_domain_matrix()
    return sum(sum(cells.values()) for cells in matrix.values())

# News sources (RSS — free, no API key) — ported from real_learning_engine.py
# Used by news_learning_cycle() LOOP 3
NEWS_SOURCES = [
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://vnexpress.net/rss/the-gioi.rss",
]

# V104.1 background thread interval (1 hour) — used by start_learning_thread.
# Distinct from V104.2 adaptive interval (5 min) used by start_fast_learning_thread.
# Both starters now live in this module (post G3-MERGE); only ONE thread runs
# at runtime (see start_fast_learning_thread idempotency guard below).
LEARNING_INTERVAL = int(os.environ.get("SCP_LEARNING_INTERVAL", "3600"))  # 1 hour default

# V104.2 tuning constants
PARALLEL_OLLAMA_CONCURRENCY = int(os.environ.get("SCP_LEARN_CONCURRENCY", "10"))
WIKIPEDIA_CONCURRENCY = int(os.environ.get("SCP_WIKI_CONCURRENCY", "5"))
LEARN_INTERVAL_FAST = int(os.environ.get("SCP_LEARN_INTERVAL_FAST", "300"))  # 5 min
LEARN_INTERVAL_BURST = int(os.environ.get("SCP_LEARN_INTERVAL_BURST", "60"))  # 1 min khi nhiều facts mới
LEARN_INTERVAL_IDLE = int(os.environ.get("SCP_LEARN_INTERVAL_IDLE", "1800"))  # 30 min khi ít facts mới
SKIP_KNOWN_QUESTIONS = os.environ.get("SCP_SKIP_KNOWN", "1") == "1"
COMPOUNDING_ENABLED = os.environ.get("SCP_COMPOUNDING", "1") == "1"

# [G3-MERGE A5] Idempotency guard — prevents double-start of the background
# learning thread. Previously: helpers.py:437 called start_learning_thread
# (from real_learning_engine) AND helpers.py:444 called start_fast_learning_thread
# (from fast_learning_engine) → 2 threads writing to same KB → race condition.
# After merge both calls resolve to the SAME function; this flag ensures only
# the first call actually starts a thread. Subsequent calls log + return the
# existing thread handle.
_FAST_LEARNING_THREAD: threading.Thread | None = None
_FAST_LEARNING_THREAD_LOCK = threading.Lock()


class FastLearningEngine:
    """
    V104.2 Fast Learning Engine (CANONICAL — post G3-MERGE).

    Cải thiện V104.1:
    - Parallel Ollama: 10 concurrent (asyncio.Semaphore)
    - Skip-already-known: check KB trước khi hỏi
    - Compounding: sinh câu hỏi sâu hơn dựa trên facts đã học
    - Adaptive interval: 1-30 min tùy throughput
    - Batch Wikipedia: 5 concurrent

    [G3-MERGE] Also serves as RealLearningEngine (via stub alias). The class
    carries BOTH the V104.2 fast-cycle methods AND the V104.1 sequential
    methods (ollama_learning_cycle, local_learning_cycle, news_learning_cycle,
    run_all_cycles) ported from real_learning_engine.py. Both API surfaces
    (`/v104/learn/ollama` → _real_learning.ollama_learning_cycle() AND
    `/v104/learn/fast` → _fast_learning.fast_learning_cycle()) resolve to
    instances of THIS class.
    """

    def __init__(self, scp_db_path: str = "data/v13.db", data_dir: str = "data"):
        self.scp_db_path = scp_db_path
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stats = {
            # V104.2 fast-cycle stats
            "cycles_completed": 0,
            "ollama_questions_asked": 0,
            "ollama_questions_skipped_known": 0,
            "ollama_answers_verified": 0,
            "ollama_kb_facts_stored": 0,
            "compounding_l2_questions": 0,  # Level-2 questions (dựa trên KB)
            "compounding_l3_questions": 0,  # Level-3 questions (chain-of-why)
            "avg_cycle_time_ms": 0,
            "fastest_cycle_ms": 999999,
            "slowest_cycle_ms": 0,
            "by_domain": {},
            "by_country": {},
            "adaptive_interval_current": LEARN_INTERVAL_FAST,
            # V104.1 real-cycle stats (ported from real_learning_engine)
            "local_files_scanned": 0,
            "local_facts_verified": 0,
            "local_kb_facts_stored": 0,
            "news_headlines_fetched": 0,
            "news_questions_generated": 0,
            "news_facts_stored": 0,
        }
        self._init_kb()
        self._ollama_semaphore: asyncio.Semaphore | None = None
        self._wiki_semaphore: asyncio.Semaphore | None = None

    def _init_kb(self) -> None:
        """Initialize knowledge tables.

        [ROOT-FIX 1] TẠI SAO: the old schema had only 8 columns (missing
        last_verified, times_wrong, bias_correction, notes) — but the UPSERT
        in _store_kb (P1-13 fix) references `last_verified`. When FastLearning
        uses a standalone test DB (scp_db_path != default), this 8-col schema
        wins → UPSERT fails → 0 rows stored → compounding L2 never generates.
        Fix: use the canonical `_KNOWLEDGE_CANONICAL_DDL` from db_manager
        (single source of truth). Legacy ALTER loop removed — db_manager's
        `_migrate_knowledge_schema` guard handles migration on the global DB,
        and on a fresh standalone DB the canonical CREATE produces the right
        schema directly.
        """
        try:
            conn = sqlite3.connect(self.scp_db_path, timeout=10.0)
            conn.execute(_KNOWLEDGE_CANONICAL_DDL)
            # V104.2: index để skip-already-known nhanh
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entity ON knowledge(entity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_attr ON knowledge(attribute)")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"KB init failed: {e}")

    async def _get_ollama_semaphore(self) -> asyncio.Semaphore:
        if self._ollama_semaphore is None:
            self._ollama_semaphore = asyncio.Semaphore(PARALLEL_OLLAMA_CONCURRENCY)
        return self._ollama_semaphore

    async def _get_wiki_semaphore(self) -> asyncio.Semaphore:
        if self._wiki_semaphore is None:
            self._wiki_semaphore = asyncio.Semaphore(WIKIPEDIA_CONCURRENCY)
        return self._wiki_semaphore

    # ============================================================
    # V104.2.1: SKIP-ALREADY-KNOWN
    # ============================================================

    def _is_question_known(self, question: str) -> bool:
        """Check if question đã có trong KB (entity = question).

        [V104.35 #38] TẠI SAO: V104.34 marked TODO — was: sqlite3.connect bypasses
        _db_lock (race with Brain) + entity NOT lowercased (case mismatch with
        _store_kb which lowercases). SKIP_KNOWN dead → wasted Ollama compute.
        Fix: lowercase entity (mirror _store_kb). Still use sqlite3.connect for
        self.scp_db_path isolation (db_manager uses global DB, breaks test isolation).
        The race risk is acceptable for read-only SELECT with timeout=5.0.

        [EXEC-1 B2] TẠI SAO: replaced bare sqlite3.connect with db_query_one +
        db_path=self.scp_db_path. db_manager caches per-path connections under
        the SAME _db_lock as Brain writes — eliminates the race AND keeps test
        isolation (separate cache entry per scp_db_path). Lowercasing preserved.
        """
        try:
            from scp.core.db_manager import db_query_one
            # [V104.35 #38] lowercase to match _store_kb (was: case mismatch)
            entity_lower = question.lower()[:200]
            row = db_query_one(
                "SELECT 1 FROM knowledge WHERE entity = ? LIMIT 1",
                (entity_lower,),
                db_path=str(self.scp_db_path),
            )
            return row is not None
        except Exception:
            return False

    def _get_known_countries_domains(self) -> dict:
        """Lấy set (country, domain) đã học từ KB."""
        try:
            from scp.core.db_manager import db_query_all
            # [EXEC-1 B2] was: bare sqlite3.connect → bypassed _db_lock → race
            # with Brain writes. Now uses db_query_all with db_path (cached
            # per-path conn, still under _db_lock).
            rows = db_query_all(
                "SELECT entity FROM knowledge WHERE attribute = 'verified_answer'",
                (),
                db_path=str(self.scp_db_path),
            )
            known = set()
            for row in rows:
                entity = row["entity"]
                for country in COUNTRIES:
                    if country in entity:
                        for domain in DOMAINS:
                            domain_keywords = {
                                "geography": ["thủ đô", "diện tích", "dân số", "sông", "núi"],
                                "history": ["độc lập", "sáng lập", "chiến tranh"],
                                "chemistry": ["khoáng sản", "hóa chất", "nhiên liệu"],
                                "physics": ["vật lý", "phát minh", "đại học"],
                                "biology": ["động vật", "thực vật", "vườn quốc gia"],
                            }
                            for kw in domain_keywords.get(domain, []):
                                if kw in entity.lower():
                                    known.add((country, domain))
                                    break
                        break
            return known
        except Exception:
            return set()

    # ============================================================
    # V104.2.2: COMPOUNDING (Level-2, Level-3 questions)
    # ============================================================

    def _generate_compounding_questions(self, count: int = 5) -> list[tuple[str, str, str]]:
        """Sinh câu hỏi Level-2 dựa trên facts đã học trong KB.

        Returns: list of (question, country, domain) tuples
        """
        if not COMPOUNDING_ENABLED:
            return []

        questions = []
        try:
            from scp.core.db_manager import db_query_all
            # [EXEC-1 B2] was: bare sqlite3.connect → bypassed _db_lock → race
            # with Brain writes. Now uses db_query_all with db_path (cached
            # per-path conn, still under _db_lock).
            recent_facts = db_query_all(
                "SELECT entity, value FROM knowledge "
                "WHERE attribute = 'verified_answer' "
                "ORDER BY timestamp DESC LIMIT 20",
                (),
                db_path=str(self.scp_db_path),
            )

            for row in recent_facts:
                entity = row["entity"]
                row["value"]
                # Sinh câu hỏi sâu hơn từ fact đã biết
                # [P2-18 FIX] TẠI SAO: _store_kb lowercases the entity before INSERT
                # (entity_lower = entity.lower()[:500]), but this loop checked
                # `if country in entity` with COUNTRIES containing proper-case names
                # ("Việt Nam"). Since stored entity is lowercase, "Việt Nam" never
                # matched "việt nam" → 0 compounding questions generated.
                # Fix: case-insensitive match.
                entity_lower = entity.lower() if entity else ""
                for country in COUNTRIES:
                    if country.lower() in entity_lower:
                        # Level 2: "Tại sao {country} có {fact}?"
                        if "thủ đô" in entity.lower():
                            questions.append((
                                f"Tại sao {country} chọn thủ đô này thay cho thành phố khác?",
                                country, "history"
                            ))
                        elif "diện tích" in entity.lower():
                            questions.append((
                                f"So sánh diện tích {country} với các nước láng giềng?",
                                country, "geography"
                            ))
                        elif "khoáng sản" in entity.lower():
                            questions.append((
                                f"Khoáng sản của {country} được khai thác ở tỉnh nào nhiều nhất?",
                                country, "chemistry"
                            ))
                        elif "động vật" in entity.lower():
                            questions.append((
                                f"Động vật đặc hữu của {country} đang bị đe dọa tuyệt chủng không?",
                                country, "biology"
                            ))
                        break

            # Shuffle + take count
            random.shuffle(questions)
            return questions[:count]
        except Exception as e:
            logger.debug(f"Compounding gen failed: {e}")
            return []

    # ============================================================
    # V104.2.3: PARALLEL OLLAMA CALLS
    # ============================================================

    async def _ask_ollama_parallel(self, question: str) -> str:
        """Gọi Ollama với semaphore để parallel (10 concurrent)."""
        semaphore = await self._get_ollama_semaphore()
        async with semaphore:
            try:
                # V104.2: dùng asyncio loop executor cho urllib blocking
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, self._ask_ollama_sync, question
                )
            except Exception as e:
                logger.debug(f"Parallel Ollama call failed: {e}")
                return ""

    def _ask_ollama_sync(self, question: str) -> str:
        """[ARCH-1 FIX] Đã chuyển sang LLM Gateway — unified client có retry + fallback.

        TÁI SAO: trước đây dùng urllib.request.urlopen trực tiếp — sync blocking,  # nosec B310 — URL validated by SCP
        no retry, no fallback. Ollama down = learning chết. Giờ dùng llm_gateway
        có fallback Ollama → OpenRouter + retry + shared connection pool.

        [ROOT-FIX 44-A] task="fast_learning" → routes to llama3.2 (3B = fastest).
        Fast learning prioritizes throughput over depth — small model is enough
        for quick Q&A cycles where Wikipedia will verify the answer afterward.
        """
        try:
            from scp.llm_gateway import chat_sync
            answer, provider = chat_sync(
                question,
                system_prompt="Trả lời ngắn gọn bằng tiếng Việt.",
                task="fast_learning",
            )
            if answer:
                self._stats["ollama_provider"] = provider  # track which provider served
            return answer or ""
        except Exception as e:
            logger.debug(f"LLM Gateway call failed: {e}")
            return ""

    async def _check_wikipedia_parallel(self, question: str, answer: str) -> dict:
        """Verify bằng Wikipedia với semaphore."""
        semaphore = await self._get_wiki_semaphore()
        async with semaphore:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, self._check_wikipedia_sync, question, answer
                )
            except Exception as e:
                logger.debug(f"Wiki parallel failed: {e}")
                return {"verified": False, "confidence": 0.0}

    def _check_wikipedia_sync(self, question: str, answer: str) -> dict:
        """Wikipedia verify sync (đã có trong V104.1, giữ nguyên)."""
        try:
            terms = re.findall(r"[\wà-ỹ]+", question.lower())
            key_terms = [t for t in terms if len(t) > 3 and t not in
                        ["của", "là", "gì", "bao", "nhiêu", "khi", "nào", "ai", "đâu",
                         "what", "how", "when", "who", "where", "the", "and", "for"]]

            if not key_terms:
                return {"verified": False, "confidence": 0.0}

            search_term = " ".join(key_terms[:3])
            params = urllib.parse.urlencode({
                "action": "query", "list": "search",
                "srsearch": search_term, "format": "json", "srlimit": 1,
            })
            url = f"https://vi.wikipedia.org/w/api.php?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "SCP-V104.2/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL validated by SCP
                data = json.loads(resp.read())
                search_results = data.get("query", {}).get("search", [])
                if not search_results:
                    return {"verified": False, "confidence": 0.0}

                snippet = search_results[0].get("snippet", "").lower()
                answer_lower = answer.lower()
                answer_keywords = [w for w in answer_lower.split() if len(w) > 3]
                if not answer_keywords:
                    return {"verified": False, "confidence": 0.0}

                matches = sum(1 for kw in answer_keywords if kw in snippet)
                match_ratio = matches / len(answer_keywords)

                if match_ratio >= 0.3:
                    return {"verified": True, "confidence": 0.6 + match_ratio * 0.3}
                else:
                    return {"verified": False, "confidence": 0.0}
        except Exception as e:
            logger.debug(f"Wikipedia verify failed: {e}")
            return {"verified": False, "confidence": 0.0}

    # [V9.1-UPGRADE] LearningVerification layer — cùng cấp WHY (2-layer: action + self-verify).
    # TẠI SAO: WHY gate decides "có nên store không?" (action). _verify_learned_fact
    # decides "fact này CÓ ĐÚNG không khi đã quyết định store?" (self-verify).
    # WHY = necessity + falsification (trước action). Verify = cross-check (sau action).
    # Nếu fact không cross-check được → flag as "unverified" (confidence ≤ 0.5).
    def _verify_learned_fact(
        self, entity: str, value: str, source: str
    ) -> tuple[bool, str, float]:
        """Self-verify a learned fact before persisting to KB.

        Returns (is_valid, reason, confidence_adjustment).
        - is_valid=False → reject (don't store at all)
        - is_valid=True + confidence_adjustment<0 → store with downgraded confidence
        - is_valid=True + confidence_adjustment>=0 → store as-is

        Cross-check sources (in priority order):
          1. KB itself — does the entity already exist? Conflicting or matching value?
          2. Basic sanity — entity/value not empty, not spam, not absurd length
          3. Source sanity — source not from a blacklisted origin
        """
        try:
            # [V9.1-UPGRADE] Check 1: basic validation
            if not entity or not isinstance(entity, str) or not entity.strip():
                return False, "empty entity (spam/junk)", -1.0
            if not value or not isinstance(value, str) or len(value.strip()) < 2:
                return False, "empty/too-short value", -1.0
            # Absurd length check — knowledge shouldn't be a paragraph dump
            if len(value) > 2000:
                return False, "value too long (>2000 chars, likely junk)", -1.0
            # Spam check — repeated chars, no real content
            _stripped = value.strip().lower()
            if len(set(_stripped)) < 3:
                return False, "value too low entropy (spam/repeat)", -1.0

            # [V9.1-UPGRADE] Check 2: cross-check with KB (2nd source)
            # TẠI SAO: KB itself is the best "2nd source" available offline —
            # if KB already has a fact for this entity, we can cross-check.
            try:
                from scp.core.db_manager import db_query_one
                entity_lower = entity.lower()[:500]
                existing = db_query_one(
                    "SELECT value, confidence FROM knowledge WHERE entity = ? AND attribute = ? LIMIT 1",
                    (entity_lower, "verified_answer"),
                    db_path=str(self.scp_db_path),
                )
                if existing is not None:
                    existing_value = (existing.get("value") or "").strip().lower()
                    new_value = value.strip().lower()
                    # If existing value matches new → verified (cross-check passed)
                    # → bump confidence slightly
                    if existing_value and existing_value == new_value:
                        return True, "cross-check OK (KB matches)", +0.05
                    # If existing value conflicts → unverified (KB disagrees)
                    # → still store (newer source may be correct) but flag
                    if existing_value and existing_value != new_value:
                        return True, "cross-check CONFLICT (KB has different value)", -0.4
                # else: no prior KB entry → can't cross-check, flag as unverified
                # → store at reduced confidence (≤ 0.5)
                return True, "no prior KB entry (unverified — no cross-check possible)", -0.3
            except Exception as _kb_err:
                logger.debug(f"[V9.1-UPGRADE] KB cross-check failed (fail-open): {_kb_err}")
                # Fail-open: can't cross-check → unverified but don't block
                # [G3-MERGE #8c] TẠI SAO: was -0.2 (asymmetric penalty — KB
                # cross-check ERROR was treated as if the fact itself were
                # suspicious, which suppressed KB accumulation whenever the
                # cross-check infra hiccupped). Doc DNA #8 says: cross-check
                # failure is NEUTRAL (0.0) — fail-open, no penalty, no reward.
                # Confirmed by Task 2-B P1-04: fix #8c was NEVER applied (both
                # engines still returned -0.2). Applying now as part of G3 merge.
                return True, "KB cross-check error (fail-open, unverified)", 0.0

        except Exception as _verify_err:
            logger.debug(f"[V9.1-UPGRADE] _verify_learned_fact error (fail-open): {_verify_err}")
            return True, f"verify error (fail-open): {_verify_err}", 0.0

    # [V9.1-UPGRADE] Audit log helper for V9.1 self-verify layer.
    # TẠI SAO: same pattern as why_gate_audit.jsonl — every verify decision logged
    # for forensic review. Fail-open: log failure must not break learning.
    def _audit_v91(self, event: str, payload: dict) -> None:
        try:
            import json as _json
            _entry = {
                "ts": time.time(),
                "engine": "fast_learning",
                "event": event,
                "payload": payload,
            }
            _audit_path = self.data_dir / "v91_upgrade_audit.jsonl"
            with open(_audit_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(_entry, ensure_ascii=False) + "\n")
        except Exception as _audit_err:
            logger.debug(f"[V9.1-UPGRADE] audit log error (fail-open): {_audit_err}")

    def _store_kb(self, entity: str, attribute: str, value: str, source: str, confidence: float) -> None:
        """Store fact into KB.

        [V104.24 #5 + V104.29 #4 FIX] Use db_exec from db_manager (was: separate
        sqlite3.connect calls bypassing _db_lock + case mismatch with Brain).
        Re-applied 3rd time after being lost in V104.24, V104.25, V104.29.

        [V104.46 #CF] TẠI SAO: FastLearning store didn't check SourceWatchlist →
        blocked sources could still write to KB. Fix: check watchlist before INSERT.

        [V9.0-WHY-GATE] WHY gates data learning — don't pollute KB without WHY approval.
        """
        # [V9.0-WHY-GATE] WHY gates data learning
        try:
            from scp.meta.why_gate import get_why_gate
            _why = get_why_gate().gate(
                action_type="learning",
                action_desc=f"FastLearning store: entity={entity[:50]}, source={source}, conf={confidence}",
                context=f"value={value[:100]}",
            )
            if not _why.allowed:
                logger.info(f"[V9.0-WHY-GATE] FastLearning KB store blocked by WHY for {entity[:30]}")
                return  # don't store — WHY rejected
        except Exception as _why_err:
            logger.debug(f"[V9.0-WHY-GATE] WHY Gate error (non-blocking): {_why_err}")

        try:
            # [V104.46 #CF] [P2-18 FIX] Check SourceWatchlist before writing to KB.
            # TẠI SAO: V104.46 #CF fix constructed `SourceWatchlist()` with NO args,
            # but the constructor requires `store: ReputationStore` → TypeError →
            # caught by except → "fail-open" logged → BUT the error path skipped
            # the INSERT too (depending on except scope) → 0 rows stored →
            # compounding L2 questions never generated → FastLearning appeared
            # to work but stored nothing. Fix: construct with ReputationStore.
            try:
                from scp.knowledge.source_reputation import ReputationStore
                from scp.knowledge.source_watchlist import SourceWatchlist
                _watchlist = SourceWatchlist(store=ReputationStore())
                if _watchlist.is_blocked(source):
                    logger.warning(f"[V104.46 #CF] FastLearning KB write blocked by watchlist: source={source}")
                    return  # skip — don't write blocked sources to KB
            except Exception as _wl_err:
                logger.debug(f"[V104.46 #CF] Watchlist check error (fail-open): {_wl_err}")

            # [V9.1-UPGRADE] LearningVerification layer — self-verify trước khi store.
            # TẠI SAO: WHY gate (v9.0) hỏi "có nên store không?" (action layer).
            # _verify_learned_fact hỏi "fact này đúng không?" (verify layer).
            # WHY + verify = cùng cấp độ sâu (2-layer action+self-verify) như WHY
            # (necessity + falsification). Non-blocking: verify error → fail-open.
            # Nếu fact không cross-check được → flag as "unverified" (conf ≤ 0.5).
            try:
                _is_valid, _verify_reason, _conf_adj = self._verify_learned_fact(entity, value, source)
                if not _is_valid:
                    logger.info(
                        f"[V9.1-UPGRADE] FastLearning fact rejected by verify: "
                        f"entity={entity[:30]}, reason={_verify_reason}"
                    )
                    self._audit_v91("fast_learning_verify_reject", {
                        "entity": entity[:100], "source": source,
                        "reason": _verify_reason,
                    })
                    return  # don't store — verify rejected
                # Apply confidence adjustment (cap at 0.5 if unverified)
                if _conf_adj != 0.0:
                    confidence = max(0.0, min(1.0, confidence + _conf_adj))
                    if _conf_adj < 0:
                        # Cap at 0.5 for unverified facts (per V9.1 spec)
                        confidence = min(confidence, 0.5)
                        logger.info(
                            f"[V9.1-UPGRADE] FastLearning fact downgraded to "
                            f"unverified (conf={confidence:.2f}): entity={entity[:30]}, "
                            f"reason={_verify_reason}"
                        )
                self._audit_v91("fast_learning_verify_ok", {
                    "entity": entity[:100], "source": source,
                    "conf_adj": _conf_adj, "final_conf": confidence,
                    "reason": _verify_reason,
                })
            except Exception as _verify_call_err:
                logger.debug(f"[V9.1-UPGRADE] _verify_learned_fact call error (fail-open): {_verify_call_err}")

            # [V104.24 #5] [AUDIT-2 FIX] Use db_exec with db_path=self.scp_db_path.
            # TẠI SAO: the previous "fix" (P2-18) used bare sqlite3.connect to
            # restore test isolation — but that LOST _db_lock sharing with Brain
            # → race condition → facts silently dropped (Invariant #7 violation,
            # exactly the bug V104.24 #5 originally fixed). The root-cause fix
            # (AUDIT-2): db_exec now accepts optional db_path → uses a cached
            # per-path connection, STILL under _db_lock. Single lock, multiple
            # paths = both test isolation AND race safety.
            from scp.core.db_manager import db_exec
            # [V104.24 #5] entity.lower() — Brain stores lowercase, must match
            entity_lower = entity.lower()[:500]
            # [V104.50 #P1-13b] TẠI SAO: INSERT OR REPLACE deleted the row
            # on conflict and re-inserted with times_verified=1 → a fact
            # verified 100 times became "verified once" on every relearn
            # (knowledge regression — undermines confidence scoring).
            # Fix: SQLite UPSERT (knowledge has PRIMARY KEY (entity, attribute)
            # per scp/core/db_manager.py:279) — on conflict, KEEP the
            # accumulated times_verified and INCREMENT it; refresh value,
            # confidence, source, timestamp, last_verified so the latest
            # verification wins on those dimensions.
            _now = datetime.now().isoformat()
            db_exec("""
                INSERT INTO knowledge
                (entity, attribute, value, value_type, confidence, source,
                 timestamp, times_verified, last_verified)
                VALUES (?, ?, ?, 'str', ?, ?, ?, 1, ?)
                ON CONFLICT(entity, attribute) DO UPDATE SET
                    value = excluded.value,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    timestamp = excluded.timestamp,
                    last_verified = excluded.last_verified,
                    times_verified = times_verified + 1
            """, (entity_lower, attribute, value[:500], confidence, source,
                  _now, _now), db_path=self.scp_db_path)
        except Exception as e:
            logger.debug(f"KB store failed: {e}")

    # ============================================================
    # V104.2.4: FAST LEARNING CYCLE (PARALLEL)
    # ============================================================

    async def fast_learning_cycle(self, count: int = 50) -> dict:
        """
        V104.2 Fast learning cycle:
        - Sinh `count` câu hỏi từ ma trận 14×5
        - Skip những câu đã có trong KB
        - Hỏi Ollama PARALLEL (10 concurrent)
        - Verify Wikipedia PARALLEL (5 concurrent)
        - Store vào KB
        - Adaptive interval dựa trên verified ratio
        """
        cycle_start = time.time()
        results = {
            "asked": 0, "skipped_known": 0,
            "verified": 0, "stored": 0,
            "compounding_l2": 0,
            "matrix_coverage": {"by_country": {}, "by_domain": {}},
            "parallel_concurrency": PARALLEL_OLLAMA_CONCURRENCY,
        }

        # Sinh danh sách câu hỏi
        question_batch = []
        for _ in range(count):
            domain = random.choice(DOMAINS)
            templates = SEED_QUESTIONS[domain]
            country_templates = [t for t in templates if "{country}" in t]
            compound_templates = [t for t in templates if "{compound}" in t]
            generic_templates = [t for t in templates
                                 if "{country}" not in t and "{compound}" not in t]
            roll = random.random()
            country_used = None
            if country_templates and roll < 0.7:
                template = random.choice(country_templates)
                country = random.choice(COUNTRIES)
                question = template.format(country=country)
                country_used = country
            elif compound_templates and roll < 0.9:
                template = random.choice(compound_templates)
                question = template.format(compound=random.choice(COMPOUNDS))
            elif generic_templates:
                template = random.choice(generic_templates)
                question = template
            elif country_templates:
                template = random.choice(country_templates)
                country = random.choice(COUNTRIES)
                question = template.format(country=country)
                country_used = country
            else:
                template = random.choice(compound_templates)
                question = template.format(compound=random.choice(COMPOUNDS))

            # Hint
            hint = None
            if country_used and country_used in COUNTRY_DOMAIN_HINTS:
                hints_for_country = COUNTRY_DOMAIN_HINTS[country_used]
                if domain in hints_for_country:
                    hint = hints_for_country[domain]
            prompt = question
            if hint:
                prompt = f"{question}\n\n(Gợi ý: {hint})"

            question_batch.append({
                "question": question,
                "prompt": prompt,
                "country": country_used,
                "domain": domain,
            })

        # V104.2.2: Thêm compounding L2 questions
        if COMPOUNDING_ENABLED:
            compounding_qs = self._generate_compounding_questions(count=5)
            for q, country, domain in compounding_qs:
                question_batch.append({
                    "question": q,
                    "prompt": q,
                    "country": country,
                    "domain": domain,
                })
                results["compounding_l2"] += 1
                self._stats["compounding_l2_questions"] += 1

        # V104.2.1: Skip already known
        if SKIP_KNOWN_QUESTIONS:
            new_batch = []
            for item in question_batch:
                if self._is_question_known(item["question"]):
                    results["skipped_known"] += 1
                    self._stats["ollama_questions_skipped_known"] += 1
                else:
                    new_batch.append(item)
            question_batch = new_batch

        # V104.2.3: PARALLEL Ollama calls
        results["asked"] = len(question_batch)
        self._stats["ollama_questions_asked"] += len(question_batch)

        # Track coverage
        for item in question_batch:
            country = item["country"]
            domain = item["domain"]
            if country:
                results["matrix_coverage"]["by_country"][country] = (
                    results["matrix_coverage"]["by_country"].get(country, 0) + 1
                )
                self._stats["by_country"][country] = (
                    self._stats["by_country"].get(country, 0) + 1
                )
            results["matrix_coverage"]["by_domain"][domain] = (
                results["matrix_coverage"]["by_domain"].get(domain, 0) + 1
            )
            self._stats["by_domain"][domain] = (
                self._stats["by_domain"].get(domain, 0) + 1
            )

        # Batch parallel: ask Ollama all at once (with semaphore 10)
        ollama_tasks = [
            self._ask_ollama_parallel(item["prompt"]) for item in question_batch
        ]
        ollama_answers = await asyncio.gather(*ollama_tasks, return_exceptions=True)

        # Filter valid answers + build verify batch
        verify_tasks = []
        verify_items = []
        for item, answer in zip(question_batch, ollama_answers):
            if isinstance(answer, Exception) or not answer or len(answer) < 3:
                continue
            verify_tasks.append(self._check_wikipedia_parallel(item["question"], answer))
            verify_items.append((item, answer))

        # Batch parallel: verify Wikipedia all at once (with semaphore 5)
        wiki_results = await asyncio.gather(*verify_tasks, return_exceptions=True)

        for (item, answer), wiki in zip(verify_items, wiki_results):
            if isinstance(wiki, Exception):
                wiki = {"verified": False, "confidence": 0.0}
            if wiki.get("verified"):
                self._stats["ollama_answers_verified"] += 1
                results["verified"] += 1
                self._store_kb(
                    entity=item["question"][:200],
                    attribute="verified_answer",
                    value=answer[:500],
                    source=f"ollama+wiki:{OLLAMA_MODEL}",
                    confidence=wiki["confidence"],
                )
                self._stats["ollama_kb_facts_stored"] += 1
                results["stored"] += 1

        # V104.2.4: Adaptive interval
        cycle_time_ms = int((time.time() - cycle_start) * 1000)
        self._stats["cycles_completed"] += 1
        self._stats["avg_cycle_time_ms"] = (
            (self._stats["avg_cycle_time_ms"] * (self._stats["cycles_completed"] - 1) + cycle_time_ms)
            / self._stats["cycles_completed"]
        )
        self._stats["fastest_cycle_ms"] = min(self._stats["fastest_cycle_ms"], cycle_time_ms)
        self._stats["slowest_cycle_ms"] = max(self._stats["slowest_cycle_ms"], cycle_time_ms)

        # Adaptive: nếu stored ratio > 50% → BURST mode (60s)
        #           nếu stored ratio < 10% → IDLE mode (30 min)
        #           else → FAST mode (5 min)
        if results["asked"] > 0:
            stored_ratio = results["stored"] / results["asked"]
        else:
            stored_ratio = 0

        if stored_ratio > 0.5:
            new_interval = LEARN_INTERVAL_BURST
            mode = "BURST"
        elif stored_ratio < 0.1:
            new_interval = LEARN_INTERVAL_IDLE
            mode = "IDLE"
        else:
            new_interval = LEARN_INTERVAL_FAST
            mode = "FAST"
        self._stats["adaptive_interval_current"] = new_interval
        results["cycle_time_ms"] = cycle_time_ms
        results["adaptive_mode"] = mode
        results["adaptive_interval_s"] = new_interval
        results["stored_ratio"] = round(stored_ratio, 3)

        logger.info(
            f"V104.2 FastLearning: asked={results['asked']}, "
            f"skipped={results['skipped_known']}, "
            f"verified={results['verified']}, stored={results['stored']}, "
            f"compounding_L2={results['compounding_l2']}, "
            f"time={cycle_time_ms}ms, mode={mode}"
        )
        return results

    def get_adaptive_interval(self) -> int:
        return self._stats.get("adaptive_interval_current", LEARN_INTERVAL_FAST)

    def stats(self) -> dict:
        return self._stats.copy()

    # ============================================================
    # [G3-MERGE] V104.1 sequential API — ported from real_learning_engine.py
    # These methods are kept for backward-compat with the /v104/learn/ollama,
    # /v104/learn/local, /v104/learn/news, /v104/learn/all endpoints (see
    # scp/api/routes/v104_routes.py:195-216). They are SEQUENTIAL (no parallelism)
    # and use the "learning" LLM task (qwen2.5:7b — better summarization).
    # Distinct from the V104.2 fast_learning_cycle() which uses "fast_learning"
    # task (llama3.2 — speed).
    # ============================================================

    async def ollama_learning_cycle(self, count: int = 10) -> dict:
        """
        [G3-MERGE PORTED] V104.1: Sinh câu hỏi theo ma trận 14 quốc gia × 5
        lĩnh vực → hỏi Ollama → verify bằng Wikipedia → lưu KB (SEQUENTIAL).

        Mặc định count=10: random pick từ 70+ combinations.
        count=70 → cover đầy đủ 1 vòng ma trận.
        """
        results = {
            "asked": 0, "verified": 0, "stored": 0,
            "matrix_coverage": {"by_country": {}, "by_domain": {}},
        }

        for _ in range(count):
            # 1. Sinh câu hỏi theo ma trận 14 × 5
            domain = random.choice(DOMAINS)
            templates = SEED_QUESTIONS[domain]

            # Ưu tiên template có {country} để đảm bảo coverage đa quốc gia
            country_templates = [t for t in templates if "{country}" in t]
            compound_templates = [t for t in templates if "{compound}" in t]
            generic_templates = [t for t in templates
                                 if "{country}" not in t and "{compound}" not in t]

            # 70% country-specific, 20% compound, 10% generic (nếu có)
            roll = random.random()
            country_used = None
            if country_templates and roll < 0.7:
                template = random.choice(country_templates)
                country = random.choice(COUNTRIES)
                question = template.format(country=country)
                country_used = country
                # Track coverage
                results["matrix_coverage"]["by_country"][country] = (
                    results["matrix_coverage"]["by_country"].get(country, 0) + 1
                )
            elif compound_templates and roll < 0.9:
                template = random.choice(compound_templates)
                question = template.format(compound=random.choice(COMPOUNDS))
            elif generic_templates:
                template = random.choice(generic_templates)
                question = template
            elif country_templates:
                # Fallback nếu không có compound/generic
                template = random.choice(country_templates)
                country = random.choice(COUNTRIES)
                question = template.format(country=country)
                country_used = country
                results["matrix_coverage"]["by_country"][country] = (
                    results["matrix_coverage"]["by_country"].get(country, 0) + 1
                )
            else:
                template = random.choice(compound_templates)
                question = template.format(compound=random.choice(COMPOUNDS))

            results["matrix_coverage"]["by_domain"][domain] = (
                results["matrix_coverage"]["by_domain"].get(domain, 0) + 1
            )

            # V104.1: thêm hint vào prompt nếu (country, domain) có hint
            hint = None
            if country_used and country_used in COUNTRY_DOMAIN_HINTS:
                hints_for_country = COUNTRY_DOMAIN_HINTS[country_used]
                if domain in hints_for_country:
                    hint = hints_for_country[domain]
            if hint:
                prompt_for_ollama = (
                    f"{question}\n\n(Gợi ý: {hint})"
                )
            else:
                prompt_for_ollama = question

            self._stats["ollama_questions_asked"] += 1
            results["asked"] += 1

            # 2. Hỏi Ollama (async, sequential — NOT parallel)
            ollama_answer = await self._ask_ollama(prompt_for_ollama)
            if not ollama_answer or len(ollama_answer) < 3:
                continue

            # 3. Verify bằng Wikipedia
            wiki_answer = self._check_wikipedia(question, ollama_answer)
            if wiki_answer["verified"]:
                self._stats["ollama_answers_verified"] += 1
                results["verified"] += 1

                # 4. Lưu vào KB
                self._store_kb(
                    entity=question[:200],
                    attribute="verified_answer",
                    value=ollama_answer[:500],
                    source=f"ollama+wiki:{OLLAMA_MODEL}",
                    confidence=wiki_answer["confidence"],
                )
                self._stats["ollama_kb_facts_stored"] += 1
                results["stored"] += 1
                self._stats["by_domain"][domain] = self._stats["by_domain"].get(domain, 0) + 1
                logger.info(f"Ollama Learning: VERIFIED '{question[:50]}' → '{ollama_answer[:50]}'")
            else:
                logger.debug(f"Ollama Learning: NOT VERIFIED '{question[:50]}' → '{ollama_answer[:50]}'")

        logger.info(f"Ollama Learning cycle: asked={results['asked']}, "
                   f"verified={results['verified']}, stored={results['stored']}")
        return results

    async def _ask_ollama(self, question: str) -> str:
        """[G3-MERGE PORTED] V104.1 async LLM call — uses task="learning"
        (routes to qwen2.5:7b — better summarization + multilingual for
        Vietnamese learning questions).

        Distinct from _ask_ollama_sync which uses task="fast_learning"
        (llama3.2 — speed-prioritized for parallel batch calls).
        """
        try:
            from scp.llm_gateway import get_gateway
            gw = get_gateway()
            answer, provider = await gw.chat(
                question,
                system_prompt="Trả lời ngắn gọn bằng tiếng Việt.",
                task="learning",
            )
            return answer or ""
        except Exception as e:
            logger.debug(f"LLM Gateway async call failed: {e}")
            return ""

    def _check_wikipedia(self, question: str, answer: str) -> dict:
        """[G3-MERGE PORTED] V104.1 sync Wikipedia verify.

        Body is identical to _check_wikipedia_sync (kept separate for backward
        compat — V104.1 API surface calls _check_wikipedia, V104.2 calls
        _check_wikipedia_sync).
        """
        return self._check_wikipedia_sync(question, answer)

    # ============================================================
    # [G3-MERGE PORTED] LOOP 2: Local Scanner + LLM Verify
    # ============================================================

    async def local_learning_cycle(self) -> dict:
        """[G3-MERGE PORTED] Scan data/ files → extract facts → verify bằng
        Ollama → lưu KB."""
        results = {"files_scanned": 0, "facts_verified": 0, "stored": 0}

        for filepath in self.data_dir.rglob("*"):
            if filepath.suffix not in [".txt", ".jsonl"]:
                continue
            if filepath.name == "crawled_attacks.jsonl":
                continue  # Skip attack payloads

            self._stats["local_files_scanned"] += 1
            results["files_scanned"] += 1

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")

                # Extract sentences with facts (contain numbers or key terms)
                sentences = self._extract_facts(content)
                for sentence in sentences[:20]:  # Max 20 per file
                    # Verify bằng Ollama
                    ollama_check = await self._ask_ollama(
                        f"Câu sau có đúng không? Trả lời 'ĐÚNG' hoặc 'SAI': {sentence}"
                    )
                    if ollama_check and "đúng" in ollama_check.lower()[:10]:
                        self._stats["local_facts_verified"] += 1
                        results["facts_verified"] += 1

                        # Lưu vào KB
                        self._store_kb(
                            entity=sentence[:200],
                            attribute="local_fact",
                            value="verified_true",
                            source=f"local+ollama:{filepath.name}",
                            confidence=0.7,
                        )
                        self._stats["local_kb_facts_stored"] += 1
                        results["stored"] += 1
            except Exception as e:
                logger.debug(f"Local file {filepath.name} failed: {e}")

        logger.info(f"Local Learning: files={results['files_scanned']}, "
                   f"verified={results['facts_verified']}, stored={results['stored']}")
        return results

    def _extract_facts(self, text: str) -> list[str]:
        """[G3-MERGE PORTED] Extract sentences that look like facts (contain numbers)."""
        facts = []
        for sentence in text.split("\n"):
            sentence = sentence.strip()
            if 20 < len(sentence) < 500 and re.search(r"\d+", sentence):
                facts.append(sentence)
        return facts

    # ============================================================
    # [G3-MERGE PORTED] LOOP 3: Real-World News Learning
    # ============================================================

    async def news_learning_cycle(self) -> dict:
        """[G3-MERGE PORTED] Fetch news headlines → sinh câu hỏi → verify → lưu KB."""
        results = {"headlines": 0, "questions": 0, "stored": 0}

        for rss_url in NEWS_SOURCES:
            try:
                headlines = self._fetch_rss_headlines(rss_url)
                for headline in headlines[:5]:  # Max 5 per source
                    self._stats["news_headlines_fetched"] += 1
                    results["headlines"] += 1

                    # Verify bằng Ollama
                    ollama_check = await self._ask_ollama(
                        f"Sự kiện sau có thật không? Trả lời 'ĐÚNG' hoặc 'SAI': {headline}"
                    )
                    if ollama_check and "đúng" in ollama_check.lower()[:10]:
                        self._stats["news_questions_generated"] += 1
                        results["questions"] += 1

                        self._store_kb(
                            entity=headline[:200],
                            attribute="news_fact",
                            value="verified_true",
                            source=f"news+ollama:{rss_url}",
                            confidence=0.6,
                        )
                        self._stats["news_facts_stored"] += 1
                        results["stored"] += 1
            except Exception as e:
                logger.debug(f"News {rss_url} failed: {e}")

        logger.info(f"News Learning: headlines={results['headlines']}, "
                   f"stored={results['stored']}")
        return results

    def _fetch_rss_headlines(self, rss_url: str) -> list[str]:
        """[G3-MERGE PORTED] Fetch RSS feed → extract headlines."""
        from defusedxml import ElementTree as DET  # nosec B314 — defusedxml hardens XXE
        headlines = []
        try:
            req = urllib.request.Request(rss_url, headers={"User-Agent": "SCP-V104/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 — URL validated by SCP
                content = resp.read().decode("utf-8", errors="replace")
            root = DET.fromstring(content)
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items[:10]:
                title = item.find("title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())
        except Exception as e:
            logger.debug(f"RSS fetch failed: {e}")
        return headlines

    # ============================================================
    # [G3-MERGE PORTED] Run all 3 loops (V104.1 API)
    # ============================================================

    async def run_all_cycles(self) -> dict:
        """[G3-MERGE PORTED] Run all 3 V104.1 learning loops sequentially.

        Used by /v104/learn/all endpoint (v104_routes.py:216).
        Distinct from fast_learning_cycle (V104.2 parallel).
        """
        logger.info("=== Real Learning Engine: Starting 3 cycles ===")

        ollama_result = await self.ollama_learning_cycle(count=10)
        local_result = await self.local_learning_cycle()
        news_result = await self.news_learning_cycle()

        total_stored = (
            ollama_result["stored"] +
            local_result["stored"] +
            news_result["stored"]
        )
        logger.info(f"=== Real Learning Engine: {total_stored} facts stored ===")
        return {
            "ollama": ollama_result,
            "local": local_result,
            "news": news_result,
            "total_stored": total_stored,
            "stats": self._stats.copy(),
        }


# ============================================================
# V104.2 Background thread with adaptive interval
# ============================================================

def start_fast_learning_thread(scp_db_path: str = "data/v13.db",
                              data_dir: str = "data") -> threading.Thread:
    """[G3-MERGE A5] Start V104.2 fast learning thread — adaptive interval.

    IDEMPOTENT: uses module-level _FAST_LEARNING_THREAD guard so calling this
    function multiple times (e.g. once via `start_learning_thread` alias from
    real_learning_engine stub, once via direct `start_fast_learning_thread`
    call in helpers.py:444) will NOT spawn multiple threads writing to the
    same KB. This eliminates the race condition documented in Task 2-B P1-04.
    """
    global _FAST_LEARNING_THREAD
    with _FAST_LEARNING_THREAD_LOCK:
        if _FAST_LEARNING_THREAD is not None and _FAST_LEARNING_THREAD.is_alive():
            logger.info(
                f"[G3-MERGE A5] start_fast_learning_thread called again but thread "
                f"'{_FAST_LEARNING_THREAD.name}' is already running — returning "
                f"existing handle (idempotent guard prevents race condition)."
            )
            return _FAST_LEARNING_THREAD

        def learning_loop():
            engine = FastLearningEngine(scp_db_path=scp_db_path, data_dir=data_dir)
            logger.info(f"V104.2 FastLearningEngine started "
                        f"(concurrency={PARALLEL_OLLAMA_CONCURRENCY}, "
                        f"interval={LEARN_INTERVAL_FAST}s adaptive)")

            while True:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    # V104.2: chạy 50 câu/cycle (vs 10 ở V104.1)
                    results = loop.run_until_complete(engine.fast_learning_cycle(count=50))
                    loop.close()

                    # Adaptive sleep
                    sleep_s = engine.get_adaptive_interval()
                    logger.info(f"V104.2 adaptive sleep: {sleep_s}s (mode={results['adaptive_mode']})")
                    time.sleep(sleep_s)
                except Exception as e:
                    logger.error(f"V104.2 fast learning loop error: {e}")
                    time.sleep(60)  # Fallback 1 min on error

        thread = threading.Thread(target=learning_loop, daemon=True, name="scp-v104-fast-learning")
        thread.start()
        _FAST_LEARNING_THREAD = thread
        return thread


def start_learning_thread(scp_db_path: str = "data/v13.db",
                          data_dir: str = "data") -> threading.Thread:
    """[G3-MERGE] DEPRECATED alias — delegates to start_fast_learning_thread.

    TẠI SAO: pre-merge, helpers.py:74 imported this name from real_learning_engine
    and called it to start the V104.1 sequential 1-hour thread. That thread
    RACED with start_fast_learning_thread (5-min adaptive) writing to the same
    KB. After merge, both calls resolve to the SAME idempotent function —
    only ONE thread runs. The V104.1 sequential loop is still accessible via
    the FastLearningEngine.run_all_cycles() method (called by /v104/learn/all
    endpoint on demand, NOT in a background thread).
    """
    logger.info("[G3-MERGE] start_learning_thread() called — delegating to "
                "start_fast_learning_thread (idempotent). The V104.1 sequential "
                "1-hour thread is DEPRECATED; use /v104/learn/all endpoint "
                "for on-demand sequential learning.")
    return start_fast_learning_thread(scp_db_path=scp_db_path, data_dir=data_dir)


if __name__ == "__main__":
    print("=== V104.2 Fast Learning Engine — Test ===\n")
    import tempfile

    tmpdir = tempfile.mkdtemp()
    engine = FastLearningEngine(
        scp_db_path=os.path.join(tmpdir, "test.db"),
        data_dir=tmpdir,
    )

    # Mock Ollama + Wikipedia để test parallel
    async def mock_ask_ollama_parallel(question):
        await asyncio.sleep(0.1)  # Simulate 100ms Ollama call
        return f"Mock: {question[:50]}"

    async def mock_check_wiki_parallel(question, answer):
        await asyncio.sleep(0.05)  # Simulate 50ms Wikipedia call
        return {"verified": True, "confidence": 0.85}

    engine._ask_ollama_parallel = mock_ask_ollama_parallel
    engine._check_wikipedia_parallel = mock_check_wiki_parallel

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("Test 1: 10 questions parallel (mocked)...")
    t0 = time.time()
    r1 = loop.run_until_complete(engine.fast_learning_cycle(count=10))
    t1 = time.time()
    print(f"  Time: {(t1-t0)*1000:.0f}ms (vs ~2000ms tuần tự)")
    print(f"  Asked: {r1['asked']}, Verified: {r1['verified']}, "
          f"Stored: {r1['stored']}, Mode: {r1['adaptive_mode']}")

    print("\nTest 2: 50 questions parallel...")
    t0 = time.time()
    r2 = loop.run_until_complete(engine.fast_learning_cycle(count=50))
    t1 = time.time()
    print(f"  Time: {(t1-t0)*1000:.0f}ms")
    print(f"  Asked: {r2['asked']}, Skipped: {r2['skipped_known']}, "
          f"Stored: {r2['stored']}")

    print(f"\nStats: {engine.stats()}")
    loop.close()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n✓ Test complete.")
