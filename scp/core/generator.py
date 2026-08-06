"""
SCP V90 MINIMAL — GeneratorKhamPha (simplified)
Generates random exploration questions for curiosity/prediction modules.
"""
import logging
import random

logger = logging.getLogger("scp.generator")


class KhamPhaHistory:
    """Track generated questions to avoid duplicates."""
    def __init__(self):
        self._seen = set()

    def add(self, q: str):
        self._seen.add(q)

    def has(self, q: str) -> bool:
        return q in self._seen


class GeneratorKhamPha:
    """Minimal question generator — picks from pools."""

    _POOLS = {
        "math": [
            "Tính {a} + {b}", "Tính {a} * {b}", "Tính {a} - {b}",
            "Số nguyên tố gần {a} là gì?", "Bình phương của {a} bằng bao nhiêu?",
            "Căn bậc hai của {a} bằng bao nhiêu?", "{a} có chia hết cho 3 không?",
        ],
        "science": [
            "Nhiệt độ nóng chảy của nước là bao nhiêu?",
            "Tốc độ ánh sáng là bao nhiêu km/s?",
            "Số nguyên tử trong phân tử nước là mấy?",
            "Khối lượng Trái Đất là bao nhiêu kg?",
        ],
        "general": [
            "Thủ đô của nước nào có tên bắt đầu bằng chữ {letter}?",
            "Con vật nào nhanh nhất trên cạn?",
            "Sông dài nhất thế giới là sông nào?",
        ],
    }

    def __init__(self):
        self.history = KhamPhaHistory()

    def sinh_ngau_nhien(self) -> dict:
        """Generate a random question spec."""
        domain = random.choice(list(self._POOLS.keys()))  # noqa: S311
        template = random.choice(self._POOLS[domain])  # noqa: S311
        a = random.randint(2, 999)  # noqa: S311
        b = random.randint(2, 99)  # noqa: S311
        letter = random.choice("ABCDEFGHIKLMNOPQRSTUVWXY")  # noqa: S311
        question = template.format(a=a, b=b, letter=letter)
        self.history.add(question)
        return {"question": question, "domain": domain}
