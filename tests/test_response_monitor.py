"""
Pytest tests for Response Monitor module.
Run: pytest tests/test_response_monitor.py -v
"""
import sys
from pathlib import Path

import pytest

SCP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCP_ROOT))

from scp.security.response_monitor import (
    AnomalyResult,
    ResponseMetrics,
    ResponseMonitor,
)


@pytest.fixture
def monitor():
    """Fresh monitor for each test."""
    return ResponseMonitor(window_size=100, sigma_threshold=3.0)


@pytest.fixture
def warmed_monitor(monitor):
    """Monitor with 50 normal responses as baseline."""
    normal_responses = [
        "Hà Nội là thủ đô của Việt Nam.",
        "Tháp Eiffel cao 330 mét.",
        "Nước Nga có diện tích 17.1 triệu km vuông.",
        "Thái Bình Dương là đại dương lớn nhất.",
        "Tốc độ ánh sáng khoảng 300,000 km/s.",
    ]
    for i in range(50):
        monitor.observe(
            prompt=f"q{i}",
            response=normal_responses[i % len(normal_responses)],
            latency_ms=700 + (i % 10) * 20,
        )
    return monitor


class TestResponseMetrics:
    def test_extract_basic(self):
        m = ResponseMetrics.extract("Hello world", latency_ms=100)
        assert m.length_chars == 11
        assert m.latency_ms == 100

    def test_refusal_detection(self):
        m = ResponseMetrics.extract("I can't help with that. As an AI, I'm not able to assist.")
        assert m.is_refusal is True

    def test_no_refusal_for_normal(self):
        m = ResponseMetrics.extract("Hà Nội là thủ đô của Việt Nam.")
        assert m.is_refusal is False

    def test_sensitive_keyword_detection(self):
        m = ResponseMetrics.extract("To make a bomb, use explosive. Here's methamphetamine recipe.")
        assert m.sensitive_keyword_count >= 3

    def test_entropy_calculation(self):
        m1 = ResponseMetrics.extract("aaaaaaaaaa")
        m2 = ResponseMetrics.extract("abcdefghij")
        # All-same string has entropy 0
        assert m1.entropy == 0
        # Mixed string has positive entropy
        assert m2.entropy > 0

    def test_empty_string(self):
        m = ResponseMetrics.extract("")
        assert m.length_chars == 0
        assert m.entropy == 0


class TestResponseMonitor:
    def test_observe_returns_anomaly_result(self, monitor):
        result = monitor.observe(prompt="q", response="test", latency_ms=100)
        assert isinstance(result, AnomalyResult)
        assert result.metrics is not None

    def test_insufficient_baseline_does_not_flag(self, monitor):
        # < 30 observations = insufficient baseline
        for _i in range(10):
            result = monitor.observe(prompt="q", response="x" * 5000, latency_ms=100)
        # Should NOT flag because baseline is insufficient
        assert result.is_anomalous is False or "insufficient_baseline_data" in result.reasons

    def test_normal_response_not_flagged(self, warmed_monitor):
        result = warmed_monitor.observe(
            prompt="q",
            response="Đà Nẵng là thành phố lớn ở miền Trung.",
            latency_ms=720,
        )
        assert result.is_anomalous is False

    def test_extreme_length_flagged(self, warmed_monitor):
        result = warmed_monitor.observe(
            prompt="q",
            response="A" * 5500,  # > 5000 char threshold
            latency_ms=800,
        )
        assert result.is_anomalous is True
        assert result.severity == "high"

    def test_sensitive_keywords_flagged(self, warmed_monitor):
        result = warmed_monitor.observe(
            prompt="q",
            response="To make a bomb, you need explosive. Here's methamphetamine with credit card info.",
            latency_ms=800,
        )
        assert result.is_anomalous is True
        assert result.severity in ("high", "critical")

    def test_stats_returns_dict(self, warmed_monitor):
        stats = warmed_monitor.stats()
        assert "window_size" in stats
        assert "observations" in stats
        assert stats["observations"] == 50

    def test_reset_clears_baseline(self, warmed_monitor):
        warmed_monitor.reset()
        stats = warmed_monitor.stats()
        assert stats["observations"] == 0

    def test_window_size_limit(self, monitor):
        # Set small window
        m = ResponseMonitor(window_size=10)
        for i in range(20):
            m.observe(prompt="q", response=f"response {i}", latency_ms=100)
        stats = m.stats()
        assert stats["observations"] == 10  # capped at window_size

    def test_anomaly_reasons_populated(self, warmed_monitor):
        result = warmed_monitor.observe(
            prompt="q",
            response="A" * 5500,
            latency_ms=800,
        )
        assert len(result.reasons) > 0
        assert any("length" in r.lower() or "extreme" in r.lower() for r in result.reasons)
