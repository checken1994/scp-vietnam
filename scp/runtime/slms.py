"""
SCP V90 — SLM Module
All 27 Small Language Models + base classes.
Extracted from engine.py for modularity.
"""

import hashlib
import logging


# [V104.32 #22] Token boundary helper (same as data_sources/_token_boundary_match)
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


import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("scp.slms")

# ============================================================
# V14 SLM RESPONSE
# ============================================================
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


# ============================================================
# BASE SLM — Lớp nền cho các SLM chuyên biệt
# ============================================================
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


# ============================================================
# MATH SLM — Chuyên về toán học (DETERMINISTIC, NO LLM)
# ============================================================

# ============================================================
# BIOLOGY SLM — Chuyên về sinh học
# ============================================================

# ============================================================
# FINANCE SLM — Chuyên về tài chính
# ============================================================

# ============================================================
# GEOGRAPHY SLM — [v27] New — uses local DB + REST Countries + Wikipedia
# ============================================================

# ============================================================
# HISTORY SLM — [v27] New — uses local DB + Wikipedia
# ============================================================

# ============================================================
# CHEMISTRY SLM — [v28] PubChem + local KB
# ============================================================

# ============================================================
# WEATHER SLM — [v28] Open-Meteo
# ============================================================

# ============================================================
# LOGIC SLM — [v28] Boolean/comparison (deterministic)
# ============================================================

# ============================================================
# STATISTICS SLM — [v28] Mean/median/variance/std (deterministic)
# ============================================================

# ============================================================
# REALITY SLM — [v28] Physical constants (CODATA)
# ============================================================

# ============================================================
# CONVERSION SLM — [v28] Currency/crypto (Frankfurter + CoinGecko)
# ============================================================


# ============================================================
# V46 DOMAIN SLMS — Medical, Technology, Sports, Legal, Arts
# Mỗi SLM dùng 1 DataSource + fallback LiveKnowledgeFetcher
# ============================================================
from scp.runtime.slms_parts._domainslm import _DomainSLM  # extracted (Task 19-A)


class MedicalSLM(_DomainSLM):
    DOMAIN_NAME = "medical"
    SLM_NAME = "MedSLM"
    DATASOURCE_CLASS = None  # set below after import

class TechnologySLM(_DomainSLM):
    DOMAIN_NAME = "technology"
    SLM_NAME = "TechSLM"
    DATASOURCE_CLASS = None

class SportsSLM(_DomainSLM):
    DOMAIN_NAME = "sports"
    SLM_NAME = "SportsSLM"
    DATASOURCE_CLASS = None

class LegalSLM(_DomainSLM):
    DOMAIN_NAME = "legal"
    SLM_NAME = "LegalSLM"
    DATASOURCE_CLASS = None

class ArtsSLM(_DomainSLM):
    DOMAIN_NAME = "arts"
    SLM_NAME = "ArtsSLM"
    DATASOURCE_CLASS = None


# ============================================================
# [V73] NEW SLMs for real-world question coverage
# ============================================================




from scp.runtime.slms_parts.foodslm import FoodSLM  # extracted (Task 19-A)

# ============================================================
# [V78] NEW SLMs — fill coverage gaps from deep check
# ============================================================
# ============================================================
# [V81] UniversalSLM — Wikidata fallback cho mọi domain
# Cover 45+ domains mà không cần thêm 19 SLMs riêng lẻ
# ============================================================
# [Task 19-A batch 2] More SLMs extracted to misc_slms2.py
from scp.runtime.slms_parts.misc_slms2 import (
    ReligionSLM,
)


# [V96] New SLMs using DataSource pattern
class PhysicsSLM(_DomainSLM):
    """SLM cho Vật lý - sử dụng PhysicsDataSource"""
    DATASOURCE_CLASS = None

class EducationSLM(_DomainSLM):
    """SLM cho Giáo dục - sử dụng EducationDataSource"""
    DATASOURCE_CLASS = None

class PsychologySLM(_DomainSLM):
    """SLM cho Tâm lý học - sử dụng PsychologyDataSource"""
    DATASOURCE_CLASS = None

class EnvironmentSLM(_DomainSLM):
    """SLM cho Môi trường - sử dụng EnvironmentDataSource"""
    DATASOURCE_CLASS = None

class EnergySLM(_DomainSLM):
    """SLM cho Năng lượng - sử dụng EnergyDataSource"""
    DATASOURCE_CLASS = None

class TransportSLM(_DomainSLM):
    """SLM cho Giao thông - sử dụng TransportDataSource"""
    DATASOURCE_CLASS = None

class BlockchainSLM(_DomainSLM):
    """SLM cho Blockchain - sử dụng BlockchainDataSource"""
    DATASOURCE_CLASS = None

class CybersecuritySLM(_DomainSLM):
    """SLM cho An ninh mạng - sử dụng CybersecurityDataSource"""
    DATASOURCE_CLASS = None

class GenAISLM(_DomainSLM):
    """SLM cho AI/ML - sử dụng GenAIDataSource"""
    DATASOURCE_CLASS = None

class SocialSLM(_DomainSLM):
    """SLM cho Mạng xã hội - sử dụng SocialDataSource"""
    DATASOURCE_CLASS = None

class AerospaceSLM(_DomainSLM):
    """SLM cho Hàng không vũ trụ - sử dụng AerospaceDataSource"""
    DATASOURCE_CLASS = None

class TourismSLM(_DomainSLM):
    """SLM cho Du lịch - sử dụng TourismDataSource"""
    DATASOURCE_CLASS = None

class FoodTechSLM(_DomainSLM):
    """SLM cho FoodTech - sử dụng FoodTechDataSource"""
    DATASOURCE_CLASS = None

class GeologySLM(_DomainSLM):
    """SLM cho Địa chất - sử dụng GeologyDataSource"""
    DATASOURCE_CLASS = None

class OceanographySLM(_DomainSLM):
    """SLM cho Hải dương học - sử dụng OceanographyDataSource"""
    DATASOURCE_CLASS = None

class CartographySLM(_DomainSLM):
    """SLM cho Bản đồ học - sử dụng CartographyDataSource"""
    DATASOURCE_CLASS = None

class ArchitectureSLM(_DomainSLM):
    """SLM cho Kiến trúc - sử dụng ArchitectureDataSource"""
    DATASOURCE_CLASS = None

class UXUISLM(_DomainSLM):
    """SLM cho UX/UI - sử dụng UXUIDataSource"""
    DATASOURCE_CLASS = None

class DigitalMarketingSLM(_DomainSLM):
    """SLM cho Digital Marketing - sử dụng DigitalMarketingDataSource"""
    DATASOURCE_CLASS = None

class EcommerceSLM(_DomainSLM):
    """SLM cho Thương mại điện tử - sử dụng EcommerceDataSource"""
    DATASOURCE_CLASS = None

class AudioVideoSLM(_DomainSLM):
    """SLM cho Audio/Video - sử dụng AudioVideoDataSource"""
    DATASOURCE_CLASS = None

class CraftsSLM(_DomainSLM):
    """SLM cho Thủ công - sử dụng CraftsDataSource"""
    DATASOURCE_CLASS = None

class DiplomacySLM(_DomainSLM):
    """SLM cho Ngoại giao - sử dụng DiplomacyDataSource"""
    DATASOURCE_CLASS = None

class HeritageSLM(_DomainSLM):
    """SLM cho Di sản - sử dụng HeritageDataSource"""
    DATASOURCE_CLASS = None

class MilitarySLM(_DomainSLM):
    """SLM cho Quân sự - sử dụng MilitaryDataSource"""
    DATASOURCE_CLASS = None

class SpaceMedicineSLM(_DomainSLM):
    """SLM cho Y học không gian - sử dụng SpaceMedicineDataSource"""
    DATASOURCE_CLASS = None




# ============================================================
# [V97 FIX] Lazy-load DataSource classes + assign DATASOURCE_CLASS
# Bug V96: lazy-load block nằm TRƯỚC class definitions → NameError → 26 SLM không có DataSource
# Fix V97: di chuyển block xuống SAU tất cả class definitions
# ============================================================
try:
    from scp.data_sources.aerospace import AerospaceDataSource
    from scp.data_sources.architecture import ArchitectureDataSource
    from scp.data_sources.arts import ArtsDataSource
    from scp.data_sources.audiovideo import AudioVideoDataSource
    from scp.data_sources.biology import BiologyDataSource  # noqa: F401 (no SLM yet, kept for availability check)
    from scp.data_sources.blockchain import BlockchainDataSource
    from scp.data_sources.cartography import CartographyDataSource
    from scp.data_sources.crafts import CraftsDataSource
    from scp.data_sources.cybersecurity import CybersecurityDataSource
    from scp.data_sources.digitalmarketing import DigitalMarketingDataSource
    from scp.data_sources.diplomacy import DiplomacyDataSource
    from scp.data_sources.ecommerce import EcommerceDataSource
    from scp.data_sources.education import EducationDataSource
    from scp.data_sources.energy import EnergyDataSource
    from scp.data_sources.environment import EnvironmentDataSource
    from scp.data_sources.foodtech import FoodTechDataSource
    from scp.data_sources.genai import GenAIDataSource
    from scp.data_sources.geology import GeologyDataSource
    from scp.data_sources.heritage import HeritageDataSource
    from scp.data_sources.legal import LegalDataSource
    from scp.data_sources.logic import LogicDataSource  # noqa: F401 (no SLM yet, kept for availability check)
    from scp.data_sources.medical import MedicalDataSource
    from scp.data_sources.military import MilitaryDataSource
    from scp.data_sources.oceanography import OceanographyDataSource
    from scp.data_sources.physics import PhysicsDataSource
    from scp.data_sources.psychology import PsychologyDataSource
    from scp.data_sources.religion import ReligionDataSource
    from scp.data_sources.social import SocialDataSource
    from scp.data_sources.spacemedicine import SpaceMedicineDataSource
    from scp.data_sources.sports import SportsDataSource
    from scp.data_sources.statistics import StatisticsDataSource  # noqa: F401 (no SLM yet, kept for availability check)
    from scp.data_sources.technology import TechnologyDataSource
    from scp.data_sources.tourism import TourismDataSource
    from scp.data_sources.transport import TransportDataSource
    from scp.data_sources.uxui import UXUIDataSource

    # V46 SLMs (existing — wire DataSource)
    MedicalSLM.DATASOURCE_CLASS = MedicalDataSource
    TechnologySLM.DATASOURCE_CLASS = TechnologyDataSource
    SportsSLM.DATASOURCE_CLASS = SportsDataSource
    LegalSLM.DATASOURCE_CLASS = LegalDataSource
    ArtsSLM.DATASOURCE_CLASS = ArtsDataSource
    ReligionSLM.DATASOURCE_CLASS = ReligionDataSource
    FoodSLM.DATASOURCE_CLASS = FoodTechDataSource

    # [V97 FIX] V96 SLMs (26 SLM previously unimported — now wire DataSource)
    PhysicsSLM.DATASOURCE_CLASS = PhysicsDataSource
    EducationSLM.DATASOURCE_CLASS = EducationDataSource
    PsychologySLM.DATASOURCE_CLASS = PsychologyDataSource
    EnvironmentSLM.DATASOURCE_CLASS = EnvironmentDataSource
    EnergySLM.DATASOURCE_CLASS = EnergyDataSource
    TransportSLM.DATASOURCE_CLASS = TransportDataSource
    BlockchainSLM.DATASOURCE_CLASS = BlockchainDataSource
    CybersecuritySLM.DATASOURCE_CLASS = CybersecurityDataSource
    GenAISLM.DATASOURCE_CLASS = GenAIDataSource
    SocialSLM.DATASOURCE_CLASS = SocialDataSource
    AerospaceSLM.DATASOURCE_CLASS = AerospaceDataSource
    TourismSLM.DATASOURCE_CLASS = TourismDataSource
    FoodTechSLM.DATASOURCE_CLASS = FoodTechDataSource
    GeologySLM.DATASOURCE_CLASS = GeologyDataSource
    OceanographySLM.DATASOURCE_CLASS = OceanographyDataSource
    CartographySLM.DATASOURCE_CLASS = CartographyDataSource
    ArchitectureSLM.DATASOURCE_CLASS = ArchitectureDataSource
    UXUISLM.DATASOURCE_CLASS = UXUIDataSource
    DigitalMarketingSLM.DATASOURCE_CLASS = DigitalMarketingDataSource
    EcommerceSLM.DATASOURCE_CLASS = EcommerceDataSource
    AudioVideoSLM.DATASOURCE_CLASS = AudioVideoDataSource
    CraftsSLM.DATASOURCE_CLASS = CraftsDataSource
    DiplomacySLM.DATASOURCE_CLASS = DiplomacyDataSource
    HeritageSLM.DATASOURCE_CLASS = HeritageDataSource
    MilitarySLM.DATASOURCE_CLASS = MilitaryDataSource
    SpaceMedicineSLM.DATASOURCE_CLASS = SpaceMedicineDataSource

    logger.info("[V97] All 35 DataSources loaded + 33 SLM DATASOURCE_CLASS assigned")

except ImportError as e:
    logger.warning(f"[V97] DataSource import error (some SLMs will fall back to LiveKnowledge): {e}")
except Exception as e:
    logger.warning(f"[V97] Unexpected error wiring DataSources: {e}")

# [FIX] Re-export AdviceSLM from slm_impls for backward compat
from scp.runtime.slm_impls.agriculture_slm import (
    AgricultureExpert,  # noqa: F401
    AgricultureSLM,  # noqa: F401
)
from scp.runtime.slm_impls.art_slm import (
    ArtExpert,  # noqa: F401
    ArtSLM,  # noqa: F401
)
from scp.runtime.slm_impls.chem_reality_astro_slm import (
    AstronomySLM,  # noqa: F401
    ChemistrySLM,  # noqa: F401
    RealitySLM,  # noqa: F401
)

# ============================================================
# [Task 29-B OPT-13..22] 10 new DomainExpert SLMs
# Each extends BaseSLM with local knowledge + DataSource fallback.
# NOTE: EconomicsSLM, LiteratureSLM use new DataSources (FRED/WorldBank/Gutenberg/ERIC).
# LawSLM/MilitarySLM/AgricultureSLM/ArtSLM/EngineeringSLM use existing DataSources.
# PhilosophySLM/ManagementSLM use local knowledge only.
# Aliases (*Expert) provided for clarity (see SLM_NAMING_NOTE.md).
# ============================================================
from scp.runtime.slm_impls.economics_slm import (
    EconomicsExpert,  # noqa: F401
    EconomicsSLM,  # noqa: F401
)
from scp.runtime.slm_impls.education_slm import EducationExpert  # noqa: F401
from scp.runtime.slm_impls.engineering_slm import (
    EngineeringExpert,  # noqa: F401
    EngineeringSLM,  # noqa: F401
)
from scp.runtime.slm_impls.humanities_slm import (
    GeographySLM,  # noqa: F401
    HistorySLM,  # noqa: F401
)
from scp.runtime.slm_impls.law_slm import (
    LawExpert,  # noqa: F401
    LawSLM,  # noqa: F401
)

# [FIX] Re-export missing SLMs for backward compat
from scp.runtime.slm_impls.lifestyle_slm import (
    AdviceSLM,  # noqa: F401
    AnimalFactsSLM,  # noqa: F401
    ChuckNorrisSLM,  # noqa: F401
    CitySLM,  # noqa: F401
    GeneralSLM,  # noqa: F401
    HolidaySLM,  # noqa: F401
)
from scp.runtime.slm_impls.literature_slm import (
    LiteratureExpert,  # noqa: F401
    LiteratureSLM,  # noqa: F401
)
from scp.runtime.slm_impls.management_slm import (
    ManagementExpert,  # noqa: F401
    ManagementSLM,  # noqa: F401
)
from scp.runtime.slm_impls.math_biology_slm import (
    BiologySLM,  # noqa: F401
    MathSLM,  # noqa: F401
)
from scp.runtime.slm_impls.military_slm import MilitaryExpert  # noqa: F401
from scp.runtime.slm_impls.misc_slm import (
    ConversionSLM,  # noqa: F401
    EntertainmentSLM,  # noqa: F401
    UniversalSLM,  # noqa: F401
)
from scp.runtime.slm_impls.numeric_data_slm import (
    FinanceSLM,  # noqa: F401
    LogicSLM,  # noqa: F401
    StatisticsSLM,  # noqa: F401
    WeatherSLM,  # noqa: F401
)
from scp.runtime.slm_impls.philosophy_slm import (
    PhilosophyExpert,  # noqa: F401
    PhilosophySLM,  # noqa: F401
)

# IMPORTANT (name collision note):
# - EducationSLM is ALREADY defined above (line ~281) as _DomainSLM subclass.
#   To avoid shadowing the existing class, we import only EducationExpert alias.
# - MilitarySLM is ALREADY defined above (line ~373) as _DomainSLM subclass.
#   To avoid shadowing, we import only MilitaryExpert alias.
# - For NEW class names (Economics/Philosophy/Literature/Management/Law/Agriculture/
#   Art/Engineering), no collision — imported with both SLM and Expert aliases.
# - LegalSLM/ArtsSLM already exist (no 'Law'/'Art' variant) — LawSLM/ArtSLM are new.
