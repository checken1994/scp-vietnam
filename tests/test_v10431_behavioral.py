"""
[V104.31] Behavioral tests for the 10 data-source safety bug fixes.

This test file is part of the SCP test suite. Its purpose:
  1. Verify each V104.31 fix actually behaves correctly (NOT grep-based).
  2. Auto-detect FIX-LOSS in future rounds — if any of these tests fail,
     a previous fix was overwritten by a later edit. Re-apply the fix
     documented in /home/z/my-project/worklog.md (search "V104.31 #N").
"""
import os
import sys
import sqlite3
import tempfile

import pytest  # [AUTOFIX-T1] for skip decorator on stubbed-behavior tests

SCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCP_DIR)
def test_b1_geography_api_intent_mapping():
    """B1: Geography API fallback must return field matching intent, not always capital."""
    from scp.data_sources.geography import GeographyDataSource
    geo = GeographyDataSource()
    class MockResp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status
        def json(self):
            return self._payload
    import scp.data_sources.geography as geo_mod
    orig_get = geo_mod.requests.get
    def mock_get(url, *args, **kwargs):
        if 'restcountries.com' in url and '/name/' in url:
            return MockResp([{'capital': ['Washington D.C.'], 'population': 331000000, 'area': 9833520, 'region': 'Americas'}])
        return MockResp(None, 404)
    try:
        geo_mod.requests.get = mock_get
        assert geo.fetch('population', 'usa')['value'] == 331000000
        assert geo.fetch('area', 'usa')['value'] == 9833520
        assert geo.fetch('capital', 'usa')['value'] == 'Washington D.C.'
    finally:
        geo_mod.requests.get = orig_get
def test_b5_geography_unsupported_intent():
    """B5: Unsupported intent returns None, not silent fallback to capital."""
    from scp.data_sources.geography import GeographyDataSource
    geo = GeographyDataSource()
    assert geo.fetch('nonexistent_intent', 'việt nam') is None
def test_b2_medical_token_boundary():
    """B2: Short substring keys ('a', 'in', 'h') must NOT match longer entities."""
    from scp.data_sources.medical import _token_boundary_match, _MIN_FUZZY_KEY_LEN
    assert not _token_boundary_match('in', 'insulin')
    assert not _token_boundary_match('a', 'aspirin')
    assert not _token_boundary_match('h', 'huyết áp')
    assert _MIN_FUZZY_KEY_LEN == 4
    assert _token_boundary_match('paracetamol', 'paracetamol')
    assert _token_boundary_match('huyết áp', 'huyết áp cao')
    assert _token_boundary_match('paracetamol', 'thuốc paracetamol dùng cho gì')


def test_b3_consolidator_pass_only_median():
    """B3: FAIL values must NOT pollute median/mean — they become anomalies only."""
    import scp.core.db_manager as dbm
    from scp.consolidator.consolidator import KnowledgeConsolidator
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    orig_exec, orig_all, orig_one = dbm.db_exec, dbm.db_query_all, dbm.db_query_one
    dbm.db_exec = lambda sql, p=(): (conn.cursor().execute(sql, p), conn.commit())[-1]
    dbm.db_query_all = lambda sql, p=(): [dict(r) for r in conn.cursor().execute(sql, p).fetchall()]
    dbm.db_query_one = lambda sql, p=(): (lambda r: dict(r) if r else None)(conn.cursor().execute(sql, p).fetchone())
    try:
        consol = KnowledgeConsolidator()
        obs = [
            {'question': 'mw of CO2', 'real_value': 100.0, 'final_verdict': 'PASS', 'timestamp': '2024-01-01', 'source': 't'},
            {'question': 'mw of CO2', 'real_value': 101.0, 'final_verdict': 'PASS', 'timestamp': '2024-01-02', 'source': 't'},
            {'question': 'mw of CO2', 'real_value': 99.0, 'final_verdict': 'PASS', 'timestamp': '2024-01-03', 'source': 't'},
            {'question': 'mw of CO2', 'real_value': 100.5, 'final_verdict': 'PASS', 'timestamp': '2024-01-04', 'source': 't'},
            {'question': 'mw of CO2', 'real_value': 100.0, 'final_verdict': 'PASS', 'timestamp': '2024-01-05', 'source': 't'},
            {'question': 'mw of CO2', 'real_value': 99999.0, 'final_verdict': 'FAIL', 'timestamp': '2024-01-06', 'source': 'bad'},
        ]
        summary = consol._consolidate_group('co2', 'molecular_weight', obs)
        median = float(summary['median_value'])
        mean = float(summary['mean_value'])
        assert 99 <= median <= 101, f'median={median} (FAIL excluded)'
        assert 99 <= mean <= 101, f'mean={mean} (FAIL excluded, was ~16899 if FAIL included)'
        assert summary['pass_count'] == 5
        assert summary['fail_count'] == 1
    finally:
        dbm.db_exec, dbm.db_query_all, dbm.db_query_one = orig_exec, orig_all, orig_one
        try:
            import gc; gc.collect()  # [FIX] Windows: close DB connections before unlink
            os.unlink(tmp.name)
        except PermissionError:
            pass  # [FIX] Windows: file still locked — let OS clean temp
def test_b4_finance_health_check_real():
    """B4: health_check actually pings API, caches result, returns bool."""
    from scp.data_sources.finance import FinanceDataSource
    fin = FinanceDataSource()
    hc = fin.health_check()
    assert isinstance(hc, bool)
    assert '_health_cache' in fin._cache
def test_b6_crypto_unknown_coin_value_none():
    """B6: Unknown coin → value=None (was: 0.0 → caller treats as real price)."""
    from scp.core.crypto_verifier import fetch_crypto_price
    result = fetch_crypto_price('nonexistent_coin_xyz')
    assert result.value is None
def test_b6_crypto_all_failed_value_none():
    """B6: All crypto sources failed → value=None."""
    from scp.core.crypto_verifier import fetch_crypto_price
    import scp.core.crypto_verifier as cv
    # Patch SOURCE_FETCHERS list (not the module functions — they're already bound at import)
    orig_fetchers = cv.SOURCE_FETCHERS
    cv.SOURCE_FETCHERS = [
        ("Binance", lambda s: None),
        ("Coinbase", lambda s: None),
        ("Kraken", lambda s: None),
        ("Bitstamp", lambda s: None),
        ("KuCoin", lambda s: None),
        ("CoinGecko", lambda s: None),
    ]
    try:
        result = fetch_crypto_price('bitcoin')
        assert result.value is None
    finally:
        cv.SOURCE_FETCHERS = orig_fetchers
def test_b6_chemistry_all_failed_value_none():
    """B6: Chemistry multi-source all-fail → value=None (was: 0.0)."""
    import scp.core.multi_source_verifier as msv
    orig_p, orig_w = msv._fetch_pubchem, msv._fetch_wikidata
    msv._fetch_pubchem = msv._fetch_wikidata = lambda c: None
    try:
        result = msv.fetch_chemistry_multi('nonexistent_compound_xyz')
        assert result['value'] is None
        assert result.get('ok') is False
    finally:
        msv._fetch_pubchem, msv._fetch_wikidata = orig_p, orig_w
def test_b7_crypto_result_has_sources_incomplete():
    """B7: CryptoResult dataclass has sources_incomplete field."""
    from scp.core.crypto_verifier import CryptoResult
    r = CryptoResult(value=100.0, source='t', confidence=0.5,
                     sources_queried=[], sources_succeeded=[], sources_failed=[],
                     all_values=[], strategy='t', conflict_detected=False, reason='t')
    assert hasattr(r, 'sources_incomplete')
def test_b8_conversion_unknown_scale_returns_none():
    """B8: Unknown temperature scale returns None, not ValueError."""
    from scp.data_sources.conversion import ConversionDataSource
    conv = ConversionDataSource()
    assert conv.convert_temperature(100, 'celsius', 'romer') is None
    assert conv.convert_temperature(100, 'romer', 'celsius') is None
def test_b8_conversion_known_scales_still_work():
    """B8: Known scales still convert correctly (no regression)."""
    from scp.data_sources.conversion import ConversionDataSource
    conv = ConversionDataSource()
    assert conv.convert_temperature(0, 'celsius', 'fahrenheit') == 32.0
    assert conv.convert_temperature(100, 'celsius', 'kelvin') == 373.15
    assert round(conv.convert_temperature(32, 'fahrenheit', 'celsius'), 2) == 0.0
def test_b9_medical_static_db_has_disclaimer_and_low_conf():
    """B9: Medical static DB has source_date, disclaimer, low static_confidence."""
    from scp.data_sources.medical import MedicalDataSource
    med = MedicalDataSource()
    result = med.fetch('medical_disease', 'cảm lạnh')
    assert 'source_date' in result['metadata']
    assert 'disclaimer' in result['metadata']
    assert result['metadata']['static_confidence'] == 0.55
    assert result['confidence'] == 0.55


def test_b10_consolidator_en_regex():
    """B10: English questions extract entity/attribute correctly (was: VN only)."""
    import scp.core.db_manager as dbm
    from scp.consolidator.consolidator import KnowledgeConsolidator
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    orig_exec, orig_all, orig_one = dbm.db_exec, dbm.db_query_all, dbm.db_query_one
    dbm.db_exec = lambda sql, p=(): (conn.cursor().execute(sql, p), conn.commit())[-1]
    dbm.db_query_all = lambda sql, p=(): [dict(r) for r in conn.cursor().execute(sql, p).fetchall()]
    dbm.db_query_one = lambda sql, p=(): (lambda r: dict(r) if r else None)(conn.cursor().execute(sql, p).fetchone())
    try:
        consol = KnowledgeConsolidator()
        assert consol._extract_entity_attribute({'question': 'molecular weight of water'}) == ('water', 'molecular_weight')
        assert consol._extract_entity_attribute({'question': 'temperature in Tokyo'}) == ('tokyo', 'temperature')
        assert consol._extract_entity_attribute({'question': 'what is the capital of France'}) == ('france', 'capital')
        assert consol._extract_entity_attribute({'question': 'population of Japan'}) == ('japan', 'population')
        # VN still works (no regression)
        assert consol._extract_entity_attribute({'question': 'khối lượng phân tử của nước'}) == ('nước', 'molecular_weight')
    finally:
        dbm.db_exec, dbm.db_query_all, dbm.db_query_one = orig_exec, orig_all, orig_one
        try:
            import gc; gc.collect()
            os.unlink(tmp.name)
        except PermissionError:
            pass
