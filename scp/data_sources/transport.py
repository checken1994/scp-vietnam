"""[V96] TransportDataSource"""
import logging

from scp.interfaces.data_source import IDataSource
<<<<<<< HEAD
=======
from typing import Optional
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)

logger = logging.getLogger(__name__)

class TransportDataSource(IDataSource):
    def __init__(self):
        self._cache = {}
        self._modes = {
            "máy bay": "800 km/h",
            "tàu cao tốc": "300 km/h",
            "xe hơi": "120 km/h",
            "xe đạp": "20 km/h",
            "chạy bộ": "10 km/h",
        }

    @property
    def name(self) -> str:
        return "TransportDataSource"

    @property
    def priority(self) -> int:
        return 5

    @property
    def ttl(self) -> int:
        return 86400

    def get_supported_intents(self) -> list[str]:
        return ["lookup", "query", "fact"]

<<<<<<< HEAD
    def can_handle(self, intent: str, entity: str = None) -> bool:
=======
    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
        return True

    def fetch(self, intent: str, entity: str, **kwargs):
        result = self.query(entity or intent)
        if result.get("found"):
            return {"value": result.get("answer", ""), "source": "Transport", "metadata": result}
        return None

    def health_check(self) -> bool:
        return True

    def query(self, q):
        if q in self._cache: return self._cache[q]
        result = {"found": False, "answer": None, "confidence": 0.0}
        for mode, speed in self._modes.items():
            if mode in q:
                result = {"found": True, "answer": f"{mode}: {speed}", "confidence": 1.0}
                break
        self._cache[q] = result
        return result
