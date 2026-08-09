"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""

"""
GeographyDataSource - Data source cho Địa lý
"""
import logging
from typing import Any, Optional

import requests

from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


class GeographyDataSource(IDataSource):
    """
    Data source cho các câu hỏi Địa lý.
    Hỗ trợ: dân số, diện tích, thủ đô, tọa độ.
    """

    def __init__(self):
        self._cache: dict[str, Any] = {}

        # Local database for common geography data (fallback)
        self._local_data = {
            # Countries - Capital
            'việt nam': {'capital': 'Hà Nội', 'population': 97000000, 'area': 331212},
            'vietnam': {'capital': 'Hanoi', 'population': 97000000, 'area': 331212},
            'hanoi': {'capital': 'Hà Nội', 'population': 8000000, 'area': 0},
            'tp hcm': {'capital': 'Hồ Chí Minh', 'population': 9000000, 'area': 0},
            'ho chi minh': {'capital': 'Hồ Chí Minh', 'population': 9000000, 'area': 0},
            'thành phố hồ chí minh': {'capital': 'Hồ Chí Minh', 'population': 9000000, 'area': 0},

            # Major cities
            'paris': {'capital': 'Paris', 'population': 2161000, 'area': 105},
            'london': {'capital': 'London', 'population': 8982000, 'area': 1572},
            'tokyo': {'capital': 'Tokyo', 'population': 13960000, 'area': 2194},
            'new york': {'capital': 'Washington D.C.', 'population': 8336817, 'area': 783},
            'sydney': {'capital': 'Canberra', 'population': 5312000, 'area': 12368},
            'berlin': {'capital': 'Berlin', 'population': 3644826, 'area': 892},
            'moscow': {'capital': 'Moscow', 'population': 11920000, 'area': 2561},
            'beijing': {'capital': 'Beijing', 'population': 21540000, 'area': 16411},
            'singapore': {'capital': 'Singapore', 'population': 5450000, 'area': 733},
            'dubai': {'capital': 'Abu Dhabi', 'population': 3331000, 'area': 1287},

            # Countries
            'usa': {'capital': 'Washington D.C.', 'population': 331000000, 'area': 9833520},
            'united states': {'capital': 'Washington D.C.', 'population': 331000000, 'area': 9833520},
            'uk': {'capital': 'London', 'population': 67330000, 'area': 242495},
            'united kingdom': {'capital': 'London', 'population': 67330000, 'area': 242495},
            'japan': {'capital': 'Tokyo', 'population': 125800000, 'area': 377975},
            'france': {'capital': 'Paris', 'population': 67750000, 'area': 640679},
            'germany': {'capital': 'Berlin', 'population': 83240000, 'area': 357022},
            'australia': {'capital': 'Canberra', 'population': 25980000, 'area': 7692024},
            'china': {'capital': 'Beijing', 'population': 1412000000, 'area': 9596961},
            'russia': {'capital': 'Moscow', 'population': 144100000, 'area': 17098242},
            'india': {'capital': 'New Delhi', 'population': 1380000000, 'area': 3287263},
            'brazil': {'capital': 'Brasília', 'population': 212600000, 'area': 8515767},
        }

    @property
    def name(self) -> str:
        return "GeographyDataSource"

    @property
    def priority(self) -> int:
        return 2  # Medium priority

    @property
    def ttl(self) -> int:
        return 86400  # 1 day - geography data changes slowly

    def get_supported_intents(self) -> list[str]:
        return [
            'population',
            'capital',
            'area',
            'coordinates',
            'country_info',
            'city_info',
        ]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        if intent in self.get_supported_intents():
            return True
        if entity:
            entity_lower = entity.lower()
            return entity_lower in self._local_data
        return False

    def fetch(self, intent: str, entity: str, **kwargs) -> Optional[dict[str, Any]]:
        """Lấy dữ liệu địa lý."""
        if not entity:
            return None

        entity_lower = entity.lower()

        # [V104.31 #5] Unsupported intent → None (was: silent fallback to capital)
        supported = set(self.get_supported_intents())
        if intent not in supported:
            return None

        # Try local database first
        if entity_lower in self._local_data:
            data = self._local_data[entity_lower]
            if intent not in data:
                pass  # fall through to API
            else:
                value = data.get(intent)
                # [V104.25 #7] area/population == 0 → None sentinel
                if intent in ('area', 'population') and (value == 0 or value is None):
                    return None
                return {
                    'value': value,
                    'source': 'Local Geography Database',
                    'metadata': data
                }

        # Try to fetch from REST Countries API
        result = self._fetch_from_api(entity_lower, intent)
        if result:
            return result

        return None

    def _fetch_from_api(self, entity: str, intent: str = 'capital') -> Optional[dict[str, Any]]:
        """Fallback: Lấy từ REST Countries API."""
        # [V104.31 #1] Map intent → field (was: always returns capital)
        INTENT_TO_FIELD = {
            'capital': 'capital', 'population': 'population', 'area': 'area',
            'country_info': 'region', 'city_info': 'capital',
        }
        target_field = INTENT_TO_FIELD.get(intent)
        if target_field is None:
            return None

        try:
            url = f"https://restcountries.com/v3.1/name/{entity}"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data:
                    country = data[0]
                    capital_val = country.get('capital', [''])[0] if country.get('capital') else ''
                    population_val = country.get('population', 0) or 0
                    area_val = country.get('area', 0) or 0
                    region_val = country.get('region', '')

                    if target_field == 'capital':
                        value = capital_val
                    elif target_field == 'population':
                        value = population_val if population_val > 0 else None
                    elif target_field == 'area':
                        value = area_val if area_val > 0 else None
                    elif target_field == 'region':
                        value = region_val if region_val else None
                    else:
                        value = None

                    return {
                        'value': value,
                        'source': 'REST Countries API',
                        'metadata': {
                            'population': population_val if population_val > 0 else None,
                            'area': area_val if area_val > 0 else None,
                            'capital': capital_val,
                            'region': region_val,
                        }
                    }
        except Exception as e:
            logger.warning(f"[Geography] API fetch failed: {e}")
        return None

    def health_check(self) -> bool:
        """[V104.31 #4] Real ping — REST Countries API cached 60s."""
        import time
        cache_key = '_health_cache'
        cache_ts_key = '_health_cache_ts'
        now = time.time()
        if cache_key in self._cache and now - self._cache.get(cache_ts_key, 0) < 60:
            return self._cache[cache_key]
        api_ok = False
        try:
            response = requests.get("https://restcountries.com/v3.1/name/vietnam?fields=capital", timeout=3)
            api_ok = response.status_code == 200
        except Exception:
            api_ok = False
        healthy = api_ok or bool(self._local_data)
        self._cache[cache_key] = healthy
        self._cache[cache_ts_key] = now
        return healthy
