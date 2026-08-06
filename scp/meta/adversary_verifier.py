"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""
from __future__ import annotations

# [G3-CONSOLIDATE P1-06] Verifier consolidation status:
# - This verifier: LIVE-UNIQUE
# - Canonical verifier: scp/core/multi_source_verifier.py::AsyncMultiSourceVerifier
# - Unique role: Per-verdict SECOND-OPINION adversary check — given an SLM's
#   primary answer, calls a DIFFERENT source per domain (wttr.in for weather,
#   Bitstamp/KuCoin for crypto, open.er-api for currency, Wikipedia for
#   chemistry/geography/history/biology/reality/medical/etc.) to cross-check.
#   Returns AdversaryResult with agreement_score + conflict_detected.
#   Distinct from canonical AsyncMultiSourceVerifier which verifies a claim
#   against registered sources in parallel; AdversaryVerifier verifies an
#   already-produced SLM answer against a deliberately different source.
# - Wired to /ask: YES — judgecore_mixin.py:938 `self.adversary.verify()`
#   (Step 5.5, runs per-verdict when SLM has primary answer; judge.py:268-269
#   instantiates AdversaryVerifier).
# - Delegation already in place:
#   * `_fetch_adversary_crypto` (line ~127) imports SYMBOL_MAP from
#     crypto_verifier (LIVE-UNIQUE crypto module).
#   * `_fetch_adversary_history/biology/reality` import
#     `fetch_wikipedia_summary` from canonical multi_source_verifier.
# - [V4 fix applied] `_fetch_adversary_crypto` previously re-implemented the
#   Bitstamp + KuCoin fetch URLs inline (duplicating
#   crypto_verifier._fetch_bitstamp / _fetch_kucoin). It now delegates the
#   actual HTTP call to those canonical helpers — only the result wrapping
#   ({"value": price, "source": "Bitstamp(adversary)"}) stays here.

#!/usr/bin/env python3
"""
SCP V29 — Adversary Verifier.

Cross-validation giữa các source — verify 1 SLM answer bằng cách gọi source khác.

Vấn đề V28:
    MathSLM = deterministic (PythonAST) → luôn đúng
    ChemistrySLM = PubChem → có thể sai nếu PubChem sai
    WeatherSLM = Open-Meteo → có thể sai nếu API fail
    ConversionSLM = CoinGecko → có thể sai nếu giá chênh

Giải pháp V29:
    Khi SLM primary trả answer, gọi thêm 1-2 SLM/source khác để verify.
    Nếu conflict → dùng ConflictResolver để pick winner.
    Nếu agree → boost confidence.
    Nếu disagree nhiều → flag for review.

Ví dụ:
    Q: "giá bitcoin hiện tại"
    SLM primary (ConversionSLM/CoinGecko): BTC = $97,000
    Adversary (Binance API):              BTC = $97,150
    → ConflictResolver.weighted_avg → $97,075 (confidence boost)

    Q: "khối lượng phân tử caffeine"
    SLM primary (ChemistrySLM/PubChem): 194.19
    Adversary (Wikipedia):              194.19
    → Agree → confidence 0.95 → 0.99
"""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("scp.adversary")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from scp.core.conflict_resolver import get_source_weight, resolve_value


@dataclass
class AdversaryResult:
    """Result của adversary verification."""
    primary_value: Any
    primary_source: str
    adversary_values: list[dict]      # [{"value":..., "source":...}]
    final_value: Any
    final_confidence: float
    conflict_detected: bool
    agreement_score: float            # 0.0-1.0, higher = more agreement
    strategy: str
    reason: str


class AdversaryVerifier:
    """
    Adversary Verifier — gọi source khác để verify SLM primary.
    """

    def __init__(self):
        # Cache cho adversary sources
        self._cache: dict[str, tuple[Any, float]] = {}
        self._CACHE_TTL = 300  # 5 min

    # ============================================================
    # ADVERSARY SOURCES — mỗi domain có 1-2 adversary
    # ============================================================
    def _fetch_adversary_weather(self, city: str) -> dict | None:
        """Adversary cho WeatherSLM — dùng wttr.in (different from Open-Meteo)."""
        try:
            import urllib.parse

            from scp.core.api_utils import fetch_with_retry
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=%t&u"
            text = fetch_with_retry(url, {"User-Agent": "SCP-V29/1.0"}, timeout=10)
            if text and isinstance(text, str):
                # wttr.in returns "  +25°C\n" hoặc "25°C"
                import re
                m = re.search(r'(-?\d+\.?\d*)', text)
                if m:
                    val = float(m.group(1))
                    return {"value": val, "source": "wttr.in"}
        except Exception as e:
            logger.debug(f"wttr.in error: {e}")
        return None

    def _fetch_adversary_crypto(self, coin_id: str) -> dict | None:
        """
        [V29.1] Adversary cho crypto — dùng Bitstamp (khác với Binance/Coinbase/Kraken
        mà crypto_verifier đã dùng).

        [G3-CONSOLIDATE P1-06 / Task V4] Previously re-implemented the Bitstamp +
        KuCoin HTTP fetch inline (duplicating crypto_verifier._fetch_bitstamp /
        _fetch_kucoin — same URL, same parsing). Now DELEGATES the actual HTTP
        call to those canonical helpers; only the result wrapping
        ({"value": price, "source": "Bitstamp(adversary)"}) remains here so the
        adversary can label itself distinctly from the primary crypto_verifier
        consensus path.
        """
        try:
            from scp.core.crypto_verifier import (
                SYMBOL_MAP,
                _fetch_bitstamp,
                _fetch_kucoin,
            )
            coin_lower = coin_id.lower()
            if coin_lower not in SYMBOL_MAP:
                return None
            symbols = SYMBOL_MAP[coin_lower]
            # Try Bitstamp first (different from crypto_verifier's Binance/Coinbase priority)
            bitstamp_sym = symbols.get('bitstamp')
            if bitstamp_sym:
                price = _fetch_bitstamp(bitstamp_sym)
                if price is not None and price > 0:
                    return {"value": price, "source": "Bitstamp(adversary)"}
            # Fallback: KuCoin
            kucoin_sym = symbols.get('kucoin')
            if kucoin_sym:
                price = _fetch_kucoin(kucoin_sym)
                if price is not None and price > 0:
                    return {"value": price, "source": "KuCoin(adversary)"}
        except Exception as e:
            logger.debug(f"Crypto adversary error: {e}")
        return None

    def _fetch_adversary_currency(self, from_curr: str, to_curr: str) -> dict | None:
        """Adversary cho currency — dùng exchangerate-api (free)."""
        try:
            from scp.core.api_utils import fetch_with_retry
            url = f"https://open.er-api.com/v6/latest/{from_curr.upper()}"
            data = fetch_with_retry(url, {"User-Agent": "SCP-V29/1.0"}, timeout=10)
            if data and "rates" in data:
                rate = data["rates"].get(to_curr.upper())
                if rate:
                    return {"value": float(rate), "source": "open.er-api.com"}
        except Exception as e:
            logger.debug(f"exchangerate-api error: {e}")
        return None

    def _fetch_adversary_chemistry(self, compound: str) -> dict | None:
        """Adversary cho ChemistrySLM — dùng ChemSpider Web (fallback Wikipedia)."""
        try:
            # Try Wikipedia — often has MW in summary
            from scp.core.reality_engine import WikipediaDataSource
            wiki = WikipediaDataSource()
            data = wiki.fetch(compound)
            if data and data.get("extract"):
                # Find molecular weight in extract
                import re
                # Look for "molar mass X g/mol" or "molecular weight X"
                patterns = [
                    r'molar\s+mass[:\s]+([\d\.]+)',
                    r'molecular\s+weight[:\s]+([\d\.]+)',
                    r'(\d+\.?\d*)\s*g/?mol',
                    r'MW[:\s]+([\d\.]+)',
                ]
                for pat in patterns:
                    m = re.search(pat, data["extract"], re.IGNORECASE)
                    if m:
                        val = float(m.group(1))
                        return {"value": val, "source": "Wikipedia"}
        except Exception as e:
            logger.debug(f"Wikipedia chemistry adversary error: {e}")
        return None

    def _fetch_adversary_geography(self, entity: str) -> dict | None:
        """Adversary cho GeographySLM — dùng Wikipedia."""
        try:
            from scp.core.reality_engine import WikipediaDataSource
            wiki = WikipediaDataSource()
            data = wiki.fetch(entity)
            if data and data.get("extract"):
                # Return first sentence (often contains capital)
                extract = data["extract"]
                return {"value": extract[:200], "source": "Wikipedia"}
        except Exception as e:
            logger.debug(f"Wikipedia geography adversary error: {e}")
        return None

    def _fetch_adversary_history(self, entity: str) -> dict | None:
        """
        [V29.2] Adversary cho HistorySLM — dùng Wikipedia.
        Trả về extract containing event/year/person info.
        """
        try:
            from scp.core.multi_source_verifier import fetch_wikipedia_summary
            data = fetch_wikipedia_summary(entity)
            if data and data.get("value"):
                return {"value": data["value"], "source": "Wikipedia", "title": data.get("title", "")}
        except Exception as e:
            logger.debug(f"Wikipedia history adversary error: {e}")
        return None

    def _fetch_adversary_biology(self, entity: str) -> dict | None:
        """
        [V29.2] Adversary cho BiologySLM — dùng Wikipedia.
        """
        try:
            from scp.core.multi_source_verifier import fetch_wikipedia_summary
            data = fetch_wikipedia_summary(entity)
            if data and data.get("value"):
                return {"value": data["value"], "source": "Wikipedia", "title": data.get("title", "")}
        except Exception as e:
            logger.debug(f"Wikipedia biology adversary error: {e}")
        return None

    def _fetch_adversary_reality(self, entity: str) -> dict | None:
        """
        [V29.2] Adversary cho RealitySLM — dùng Wikipedia cho physical constants.
        """
        try:
            from scp.core.multi_source_verifier import fetch_wikipedia_summary
            # Map entity → Wikipedia article name
            wiki_names = {
                'tốc độ ánh sáng': 'Speed of light',
                'speed of light': 'Speed of light',
                'hằng số planck': 'Planck constant',
                'planck constant': 'Planck constant',
                'số avogadro': 'Avogadro constant',
                'avogadro': 'Avogadro constant',
                'gia tốc trọng trường': 'Gravity of Earth',
                'gravity': 'Gravity of Earth',
                'nhiệt độ sôi của nước': 'Boiling point',
                'boiling point of water': 'Boiling point',
                'khối lượng trái đất': 'Earth mass',
                'mass of earth': 'Earth mass',
                'hằng số hấp dẫn': 'Gravitational constant',
                'gravitational constant': 'Gravitational constant',
            }
            wiki_name = wiki_names.get(entity.lower(), entity)
            data = fetch_wikipedia_summary(wiki_name)
            if data and data.get("value"):
                return {"value": data["value"], "source": "Wikipedia", "title": data.get("title", "")}
        except Exception as e:
            logger.debug(f"Wikipedia reality adversary error: {e}")
        return None

    # ============================================================
    # MAIN — verify
    # ============================================================
    def _fetch_adversary_wikipedia(self, entity: str) -> dict | None:
        """[V91] Generic Wikipedia adversary for ALL domains."""
        try:
            import urllib.parse

            from scp.core.api_utils import fetch_with_retry
            search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(entity)}&format=json&srlimit=1"
            search_data = fetch_with_retry(search_url, {"User-Agent": "SCP-V91/1.0"}, timeout=8)
            if search_data and search_data.get("query", {}).get("search"):
                title = search_data["query"]["search"][0]["title"]
                summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
                summary_data = fetch_with_retry(summary_url, {"User-Agent": "SCP-V91/1.0"}, timeout=8)
                if summary_data and summary_data.get("extract"):
                    return {
                        "value": summary_data["extract"][:200],
                        "source": "Wikipedia",
                        "entity": entity,
                    }
        except Exception as e:
            logger.debug(f"Wikipedia adversary error: {e}")
        return None


    def verify(self, primary_value: Any, primary_source: str,
               domain: str, question: str, entity: str = "") -> AdversaryResult:
        """
        Verify SLM primary answer bằng adversary source.

        Args:
            primary_value: Value từ SLM primary
            primary_source: Source name (vd "PubChem", "CoinGecko")
            domain: domain của question
            question: original question
            entity: extracted entity (vd "caffeine", "bitcoin")

        Returns:
            AdversaryResult với final value + confidence
        """
        adversary_values: list[dict] = []
        strategy = "no_adversary"

        # [P0-5 FIX] TẠI SAO: `base_conf` was previously only defined at line ~412
        # (AFTER the `if not adversary_values: return` early-return below), so the
        # early-return referenced an undefined name → NameError → swallowed by
        # upstream except → for EVERY domain without a registered adversary
        # (general, chuck_norris, advice, etc. — the most common paths), verify()
        # crashed silently. Fix: compute base_conf up here, before the early return.
        base_conf = get_source_weight(primary_source)

        # Pick adversary based on domain
        if domain == "weather" and entity:
            adv = self._fetch_adversary_weather(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "wttr.in"
        elif domain == "conversion":
            # Check if crypto or currency
            import re
            m = re.search(r'chuyển\s+đổi\s+\d+\s+([A-Z]{3})\s+sang\s+([A-Z]{3})', question, re.IGNORECASE)
            if m:
                adv = self._fetch_adversary_currency(m.group(1), m.group(2))
                if adv:
                    adversary_values.append(adv)
                    strategy = "open.er-api.com"
            else:
                m = re.search(r'giá\s+(\w+)\s+hiện\s+tại|price\s+of\s+(\w+)', question, re.IGNORECASE)
                if m:
                    coin = (m.group(1) or m.group(2)).lower()
                    adv = self._fetch_adversary_crypto(coin)
                    if adv:
                        adversary_values.append(adv)
                        strategy = "Bitstamp/KuCoin"
        elif domain == "chemistry" and entity:
            adv = self._fetch_adversary_chemistry(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"
        elif domain == "geography" and entity:
            adv = self._fetch_adversary_geography(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"
        elif domain == "history" and entity:
            # [V29.2] History adversary via Wikipedia
            adv = self._fetch_adversary_history(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"
        elif domain == "biology" and entity:
            # [V29.2] Biology adversary via Wikipedia
            adv = self._fetch_adversary_biology(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"
        elif domain == "reality" and entity:
            # [V29.2] Reality adversary via Wikipedia (cross-check constants)
            adv = self._fetch_adversary_reality(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"
        elif domain in ("medical", "technology", "sports", "legal", "arts",
                        "general", "entertainment", "religion", "food", "universal",
                        "astronomy", "city", "holiday", "animal_facts", "advice",
                        "chuck_norris") and entity:
            # [V91 FIX] Wikipedia adversary for ALL remaining domains
            adv = self._fetch_adversary_wikipedia(entity)
            if adv:
                adversary_values.append(adv)
                strategy = "Wikipedia"

        # Nếu không có adversary → return primary
        # [V48 FIX] Don't override SLM confidence with source weight.
        # Trước V48: final_confidence = get_source_weight(primary_source) = 0.5 cho "weighted(PubChem)"
        #           → giảm confidence từ 0.85 xuống 0.5 → PARTIAL → 100% FAIL trong calibration
        # V48: giữ nguyên SLM confidence — đã verified bởi SLM, không cần adversary để confirm
        if not adversary_values:
            return AdversaryResult(
                primary_value=primary_value,
                primary_source=primary_source,
                adversary_values=[],
                final_value=primary_value,
                final_confidence=base_conf,  # [V104.30 #1] was: max(0.85, weight) → PASS ảo. Keep SLM base_conf.
                # OLD FLOOR: max(0.85, get_source_weight(primary_source)),  # [V48] floor at 0.85
                conflict_detected=False,
                agreement_score=1.0,
                strategy="no_adversary",
                reason=f"No adversary available for domain '{domain}' — preserving SLM confidence",
            )

        # Combine primary + adversary
        all_values = [{"value": primary_value, "source": primary_source}] + adversary_values

        # Check numeric vs string
        is_numeric = all(isinstance(v.get("value"), (int, float))
                         or (isinstance(v.get("value"), str) and
                             v.get("value", "").replace('.', '', 1).replace('-', '').isdigit())
                         for v in all_values)

        if is_numeric:
            # Numeric → weighted_avg
            # Convert all to float
            for v in all_values:
                v["value"] = float(v["value"])
            result = resolve_value(all_values, strategy="weighted_avg")
        else:
            # String → majority_vote
            result = resolve_value(all_values, strategy="majority_vote")

        # Calculate agreement score
        if is_numeric:
            # Agreement = 1 - (std/mean)
            nums = [v["value"] for v in all_values]
            mean = sum(nums) / len(nums)
            if mean != 0:
                import math
                std = math.sqrt(sum((x - mean) ** 2 for x in nums) / len(nums))
                agreement = max(0.0, 1.0 - std / abs(mean))
            else:
                agreement = 1.0 if all(n == 0 for n in nums) else 0.5
        else:
            # String agreement
            unique = set(str(v["value"])[:50].lower() for v in all_values)
            agreement = 1.0 - (len(unique) - 1) / max(1, len(all_values))

        # Boost confidence if agreement high
        # [P0-5 FIX] base_conf already computed at top of verify() (before early-return).
        if agreement > 0.95:
            final_conf = min(0.99, base_conf + 0.05)
        elif agreement > 0.85:
            final_conf = base_conf  # unchanged
        else:
            final_conf = max(0.3, base_conf - 0.2)  # penalize

        return AdversaryResult(
            primary_value=primary_value,
            primary_source=primary_source,
            adversary_values=adversary_values,
            final_value=result.value,
            final_confidence=final_conf,
            conflict_detected=result.conflict_detected,
            agreement_score=agreement,
            strategy=result.strategy,
            reason=f"Adversary {strategy}: agreement={agreement:.2f}, conflict={result.conflict_detected}",
        )

    # ============================================================
    # STATS
    # ============================================================
    def get_stats(self) -> dict[str, Any]:
        """Stats về adversary verifier."""
        return {
            "cache_size": len(self._cache),
            "supported_domains": ["weather", "conversion", "chemistry", "geography"],
        }


# ============================================================
# MAIN
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="SCP V29 Adversary Verifier")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    av = AdversaryVerifier()

    if args.stats:
        print(f"\n  AdversaryVerifier stats: {av.get_stats()}")
        return

    if args.test:
        print(f"\n{'='*60}")
        print("  ADVERSARY VERIFIER TESTS")
        print(f"{'='*60}")

        test_cases = [
            # (primary_value, primary_source, domain, question, entity)
            (97000, "CoinGecko", "conversion", "giá bitcoin hiện tại", "bitcoin"),
            (0.92, "Frankfurter", "conversion", "chuyển đổi 1 USD sang EUR", "USD_EUR"),
            (25.0, "Open-Meteo", "weather", "nhiệt độ tại Hà Nội", "Hà Nội"),
            (194.19, "PubChem", "chemistry", "khối lượng phân tử caffeine", "caffeine"),
        ]

        for primary_val, primary_src, domain, question, entity in test_cases:
            print(f"\n  Q: {question}")
            print(f"    Primary: {primary_src} = {primary_val}")
            result = av.verify(primary_val, primary_src, domain, question, entity)
            print(f"    Adversary values: {result.adversary_values}")
            print(f"    Final value: {result.final_value}")
            print(f"    Final confidence: {result.final_confidence:.3f}")
            print(f"    Agreement: {result.agreement_score:.3f}")
            print(f"    Conflict: {result.conflict_detected}")
            print(f"    Reason: {result.reason}")

    else:
        print("Use --test or --stats")


if __name__ == "__main__":
    main()
