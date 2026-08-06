#!/usr/bin/env python3
"""
Test 17 Antibodies — Comprehensive Test Suite
==============================================
- 17 antibody tests (1 per antibody)
- 10 cross-domain conflict tests
- 9 domain isolation tests
- Edge cases + boundary tests

Usage:
    cd /path/to/scp-vietnam
    python3 tests/test_17_antibodies.py -v
"""

import sys
from pathlib import Path

import pytest

# Add scp/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scp.knowledge.antibody_system import ANTIBODIES, DomainAntibodySystem


@pytest.fixture
def ab():
    return DomainAntibodySystem()


# ============================================================
# PHẦN 1: 17 ANTIBODY TESTS — 1 per antibody (detect issues)
# ============================================================

class TestAntibodyDetection:
    """Test each antibody detects its target issue."""

    @pytest.mark.parametrize("ab_name,domain,question,answer", [
        ("dosage_validator", "medical", "Paracetamol 5000mg có an toàn?", "5000mg"),
        ("dosage_validator", "medical", "Thuốc 15g uống?", "15g thuốc"),
        ("pe_ratio_check", "finance", "P/E ratio Apple?", "P/E ratio là 600"),
        ("pe_ratio_check", "finance", "Apple P/E?", "P/E 600"),
        ("ratio_validator", "finance", "Tỷ lệ nợ/vốn?", "100:1"),
        ("ratio_validator", "finance", "Debt ratio?", "ratio 200:1"),
        ("interest_rate_check", "finance", "Lãi suất?", "60% năm"),
        ("interest_rate_check", "finance", "Interest rate?", "75% APR"),
        ("statute_check", "legal", "Thời hiệu?", "thời hiệu 50 năm"),
        ("contract_check", "legal", "Phạt vi phạm?", "penalty 25%"),
        ("citation_check", "general", "Nguồn?", "Theo research thì đúng"),
        ("url_hallucination", "general", "URL?", "https://example.com/fake"),
        ("date_verify", "general", "Khi nào?", "năm 2035"),
        ("fact_check", "general", "Tỷ lệ?", "100% người dùng"),
        ("general_check", "general", "Đúng không?", "Rõ ràng là đúng"),
        ("general_check", "general", "Is it true?", "Obviously true"),
        ("capital_check", "geography", "Thủ đô Pháp?", "thủ đô là London"),
        ("formula_check", "chemistry", "Công thức nước?", "công thức là H3O"),
        ("abbreviation_check", "biology", "DNA viết tắt gì?", "DNA là Dynamic Network App"),
        ("unit_check", "physics", "Vận tốc?", "vận tốc 5e8 m/s"),
        ("event_date_check", "history", "WW2 kết thúc?", "WW2 ended 1920"),
        ("http_status_check", "technology", "HTTP 404?", "404 means success"),
    ])
    def test_antibody_detects_issue(self, ab, ab_name, domain, question, answer):
        """Antibody should detect the issue (passed=False)."""
        results = ab.check(question, answer, domain)
        found = [r for r in results if r.antibody_name == ab_name]
        assert found, f"Antibody '{ab_name}' not triggered for question: {question}"
        assert not found[0].passed, f"Antibody '{ab_name}' should detect issue but passed=True. Details: {found[0].details}"


# ============================================================
# PHẦN 2: CROSS-DOMAIN CONFLICT TESTS — no false triggers
# ============================================================

class TestCrossDomainConflict:
    """Test antibodies don't trigger in wrong domains."""

    @pytest.mark.parametrize("test_name,domain,question,answer,forbidden_abs", [
        # Medical question với physics unit — unit_check should NOT trigger
        ("Medical vs Physics", "medical", "Liều thuốc Paracetamol?", "500mg mỗi lần",
         ["unit_check"]),
        # Finance question — dosage_validator should NOT trigger
        ("Finance vs Medical", "finance", "Lãi suất vay?", "25% lãi suất",
         ["dosage_validator", "interest_rate_check"]),  # 25% < 50%, should pass
        # Geography with history date — event_date_check should NOT trigger
        ("Geography vs History", "geography", "Thủ đô Nhật?", "Tokyo, năm 1457",
         ["event_date_check"]),
        # Chemistry with biology term — abbreviation_check should NOT trigger
        ("Chemistry vs Biology", "chemistry", "Công thức nước?", "H2O, phân tử sinh học",
         ["abbreviation_check"]),
        # History with geography — capital_check should NOT trigger
        ("History vs Geography", "history", "WW2 kết thúc?", "1945, Mỹ ném bom Nhật",
         ["capital_check"]),
        # Technology with finance — pe_ratio_check should NOT trigger
        ("Technology vs Finance", "technology", "HTTP 200?", "200 means OK",
         ["pe_ratio_check", "ratio_validator"]),
        # Physics with chemistry — formula_check should NOT trigger
        ("Physics vs Chemistry", "physics", "Năng lượng?", "E=mc²",
         ["formula_check"]),
        # Medical with legal — contract_check should NOT trigger
        ("Medical vs Legal", "medical", "Liều thuốc?", "500mg, hợp đồng cung cấp",
         ["contract_check"]),
        # Finance with technology — http_status_check should NOT trigger
        ("Finance vs Technology", "finance", "Giá Bitcoin?", "API trả 200, giá 50000",
         ["http_status_check"]),
    ])
    def test_no_cross_domain_conflict(self, ab, test_name, domain, question, answer, forbidden_abs):
        """Forbidden antibodies should NOT trigger in wrong domain."""
        results = ab.check(question, answer, domain)
        for r in results:
            if r.antibody_name in forbidden_abs:
                assert r.passed, (
                    f"[{test_name}] Antibody '{r.antibody_name}' triggered in wrong domain "
                    f"'{domain}' (passed={r.passed}). Details: {r.details}"
                )


# ============================================================
# PHẦN 3: DOMAIN ISOLATION TESTS — correct antibodies only
# ============================================================

class TestDomainIsolation:
    """Test each domain only triggers its own antibodies + general."""

    @pytest.mark.parametrize("domain,question,expected_abs", [
        ("medical", "Liều thuốc mg?", ["dosage_validator"]),
        ("finance", "P/E ratio lãi suất?", ["pe_ratio_check", "ratio_validator", "interest_rate_check"]),
        ("legal", "Hợp đồng phạt thời hiệu?", ["contract_check", "statute_check"]),
        ("geography", "Thủ đô capital?", ["capital_check"]),
        ("chemistry", "Công thức formula hợp chất?", ["formula_check"]),
        ("biology", "DNA gene protein?", ["abbreviation_check"]),
        ("physics", "Vận tốc lực nhiệt độ?", ["unit_check"]),
        ("history", "Chiến tranh war lịch sử?", ["event_date_check"]),
        ("technology", "HTTP API status code?", ["http_status_check"]),
    ])
    def test_domain_isolation(self, ab, domain, question, expected_abs):
        """Only domain-specific + general antibodies should trigger."""
        relevant = ab.get_relevant_antibodies(question, domain)
        general_abs = ab.DOMAIN_ANTIBODY_MAP.get("general", [])
        allowed = set(expected_abs + general_abs)
        wrong = [a for a in relevant if a not in allowed]
        assert not wrong, f"Domain '{domain}' triggered wrong antibodies: {wrong}. Expected: {expected_abs}"


# ============================================================
# PHẦN 4: EDGE CASES + BOUNDARY TESTS
# ============================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_answer(self, ab):
        """Empty answer should trigger general_check (too short)."""
        results = ab.check("Any question?", "", "general")
        # general_check should flag empty answer
        general = [r for r in results if r.antibody_name == "general_check"]
        assert general, "general_check should run for any question"
        assert not general[0].passed, "Empty answer should be flagged"

    def test_correct_answer_no_issues(self, ab):
        """Correct factual answer should PASS all antibodies."""
        results = ab.check("Thủ đô Việt Nam?", "Hà Nội", "geography")
        flagged = [r for r in results if not r.passed]
        assert not flagged, f"Correct answer flagged: {[r.antibody_name for r in flagged]}"

    def test_correct_chemistry_formula(self, ab):
        """Correct H2O formula should PASS."""
        results = ab.check("Công thức nước?", "H2O", "chemistry")
        formula = [r for r in results if r.antibody_name == "formula_check"]
        assert formula, "formula_check should run"
        assert formula[0].passed, "H2O is correct formula, should pass"

    def test_correct_capital(self, ab):
        """Correct capital Paris should PASS."""
        results = ab.check("Thủ đô Pháp?", "Paris", "geography")
        capital = [r for r in results if r.antibody_name == "capital_check"]
        assert capital, "capital_check should run"
        assert capital[0].passed, "Paris is correct capital for France"

    def test_correct_http_status(self, ab):
        """Correct 404 = Not Found should PASS."""
        results = ab.check("HTTP 404 nghĩa gì?", "404 means not found", "technology")
        http = [r for r in results if r.antibody_name == "http_status_check"]
        assert http, "http_status_check should run"
        assert http[0].passed, "404 = not found is correct"

    def test_antibody_count(self, ab):
        """Should have exactly 17 antibodies."""
        assert len(ANTIBODIES) == 17, f"Expected 17 antibodies, got {len(ANTIBODIES)}"

    def test_all_antibodies_have_logic(self, ab):
        """All 17 antibodies should have real check logic (not stub)."""
        for antibody in ANTIBODIES:
            ab_name = antibody["name"]
            # Run with dummy question — should NOT return passed=None (stub)
            results = ab.check("test question", "test answer", antibody["domain"])
            found = [r for r in results if r.antibody_name == ab_name]
            if found:
                assert found[0].passed is not None, (
                    f"Antibody '{ab_name}' returned passed=None — stub detected"
                )

    def test_stats_tracking(self, ab):
        """Stats should track questions and antibodies run."""
        initial = ab.stats()
        ab.check("test", "test", "general")
        after = ab.stats()
        assert after["total_questions"] > initial["total_questions"], "Stats not tracking questions"

    def test_domain_coverage(self, ab):
        """DOMAIN_ANTIBODY_MAP should cover 9 non-math domains + general."""
        domains = set(ab.DOMAIN_ANTIBODY_MAP.keys())
        expected = {"medical", "finance", "legal", "geography", "chemistry",
                    "biology", "physics", "history", "technology", "general"}
        assert domains == expected, f"Missing domains: {expected - domains}"

    def test_weasel_words_vietnamese(self, ab):
        """Vietnamese weasel words should be detected."""
        weasel_words = ["hiển nhiên", "rõ ràng", "chắc chắn", "đương nhiên", "tất nhiên"]
        for ww in weasel_words:
            results = ab.check("Đúng không?", ww, "general")
            general = [r for r in results if r.antibody_name == "general_check"]
            assert general and not general[0].passed, f"Weasel word '{ww}' not detected"

    def test_weasel_words_english(self, ab):
        """English weasel words should be detected."""
        weasel_words = ["obviously", "clearly", "well known", "everyone knows", "plainly"]
        for ww in weasel_words:
            results = ab.check("Is it true?", ww, "general")
            general = [r for r in results if r.antibody_name == "general_check"]
            assert general and not general[0].passed, f"Weasel word '{ww}' not detected"


# ============================================================
# PHẦN 5: STATISTICS + HEALTH CHECK
# ============================================================

class TestHealthCheck:
    """Test antibody system health."""

    def test_all_antibodies_in_real_check_set(self, ab):
        """All antibodies in ANTIBODIES should be in REAL_CHECK_ANTIBODIES."""
        for antibody in ANTIBODIES:
            assert antibody["name"] in ab.REAL_CHECK_ANTIBODIES, (
                f"Antibody '{antibody['name']}' not in REAL_CHECK_ANTIBODIES"
            )

    def test_no_stub_antibodies(self, ab):
        """No antibody should return passed=None (stub)."""
        for antibody in ANTIBODIES:
            ab_name = antibody["name"]
            results = ab.check(f"test for {ab_name}", "test answer with some content", antibody["domain"])
            found = [r for r in results if r.antibody_name == ab_name]
            if found:
                assert found[0].passed is not None, (
                    f"Antibody '{ab_name}' is a stub (passed=None)"
                )

    def test_antibody_result_has_fields(self, ab):
        """AntibodyResult should have all required fields."""
        results = ab.check("test", "test", "general")
        for r in results:
            assert hasattr(r, "antibody_name")
            assert hasattr(r, "domain")
            assert hasattr(r, "passed")
            assert hasattr(r, "severity")
            assert hasattr(r, "confidence")
            assert hasattr(r, "details")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
