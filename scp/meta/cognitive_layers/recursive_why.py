"""
LAYER 5: RECURSIVE WHY — Neo đệ quy truy tầng nhận thức.

Neo không chỉ hỏi "Tại sao?" một lần
Neo hỏi "Tại sao?" → nhận answer → "Tại sao tin answer?" → ... → axiom
Mỗi tầng có thể FAIL → dừng chain → UNKNOWN_WITH_REASON

VD:
    Level 0: "Tại sao 2+3=5?"
    Answer: "PythonAST evaluator returns 5"
    Level 1: "Tại sao tin PythonAST?"
    Answer: "Python's ast module is deterministic"
    Level 2: "Tại sao tin Python's ast module?"
    Answer: "Python is a well-tested programming language" (AXIOM)
    → Terminated at axiom, trust = axiomatic

Extracted from `meta/cognitive_engine.py` in Task 10-B (Modularity Refactor B).
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass


@dataclass
class WhyLevel:
    """1 level trong Recursive Why chain."""
    level: int
    question: str
    answer: str
    trust_source: str        # Tại sao tin answer?
    is_axiom: bool           = False  # Có phải tiên đề không?
    is_unprovable: bool      = False  # Không thể chứng minh thêm?
    next_question: str | None = None


@dataclass
class RecursiveWhyResult:
    """Result từ Recursive Why."""
    chain: list[WhyLevel]
    depth_reached: int
    terminated_at: str       # "axiom" / "unprovable" / "max_depth" / "circular"
    final_trust: str         # "axiomatic" / "empirical" / "unprovable" / "circular"


class RecursiveWhyEngine:
    """
    Recursive Why — Neo không chỉ hỏi 1 lần.

    Neo asks "Tại sao?" → answer → "Tại sao tin answer?" → ... → axiom

    VD:
        Level 0: "Tại sao 2+3=5?"
        Answer: "PythonAST evaluator returns 5"
        Level 1: "Tại sao tin PythonAST?"
        Answer: "Python's ast module is deterministic"
        Level 2: "Tại sao tin Python's ast module?"
        Answer: "Python is a well-tested programming language" (AXIOM)
        → Terminated at axiom, trust = axiomatic

    VD:
        Level 0: "Tại sao giá BTC = $62000?"
        Answer: "5 exchanges agree: Binance, Coinbase, Kraken..."
        Level 1: "Tại sao tin 5 exchanges?"
        Answer: "They are independent, high-volume crypto exchanges"
        Level 2: "Tại sao tin chúng independent?"
        Answer: "Different companies, different jurisdictions"
        Level 3: "Tại sao tin different jurisdictions = independent?"
        Answer: "Regulatory frameworks differ by country" (EMPIRICAL)
        → Terminated at empirical claim, trust = empirical
    """

    MAX_DEPTH = 5

    # Axiom sources — không cần chứng minh thêm
    AXIOM_SOURCES = {
        "PythonAST": "Python AST is deterministic by definition",
        "PythonMath": "Python math is deterministic by definition",
        "CODATA": "CODATA constants are internationally agreed standards",
    }

    # [V50] Authoritative empirical sources — high trust, terminate as "trusted_source"
    # These are institutional/scientific authorities whose data is the de-facto ground truth
    # for their domain. Not axioms (math), but trusted enough to stop the why-chain.
    AUTHORITATIVE_SOURCES = {
        "PubChem": "PubChem is NIH's authoritative chemical database",
        "NIST": "NIST is US National Institute of Standards and Technology",
        "CODATA": "CODATA constants are internationally agreed standards",
        "IUPAC": "IUPAC is the international chemistry authority",
        "NASA": "NASA is the US space agency (planetary data)",
        "WHO": "WHO is the UN health authority",
        "Wikidata": "Wikidata is a structured knowledge base (curated)",
        "REST Countries": "REST Countries API provides official country data",
        "Frankfurter": "Frankfurter uses European Central Bank data",
        "LocalDB": "Local database stores curated facts (manually verified)",
        "Local Chemistry Database": "Local chemistry DB (manually curated)",
        "Local Physics Database": "Local physics DB (manually curated)",
        "Local Math Database": "Local math DB (manually curated)",
        "Local Biology Database": "Local biology DB (manually curated)",
        "Local Geography Database": "Local geography DB (manually curated)",
        "Local History Database": "Local history DB (manually curated)",
        "Local Medical Database": "Local medical DB (manually curated)",
        "Local Sports Database": "Local sports DB (manually curated)",
        "Local Legal Database": "Local legal DB (manually curated)",
        "Local Arts Database": "Local arts DB (manually curated)",
        "Local Technology Database": "Local technology DB (manually curated)",
        "Local Statistics Database": "Local statistics DB (manually curated)",
        "Local Logic Database": "Local logic DB (manually curated)",
        "Local Conversion Database": "Local conversion DB (manually curated)",
        "Local Reality Database": "Local reality DB (manually curated)",
        "IUPAC Periodic Table (Local)": "IUPAC periodic table (local copy)",
        "NASA Planetary Fact Sheet (Local)": "NASA planetary data (local copy)",
        "NIST CODATA 2018 (Local)": "NIST CODATA 2018 (local copy)",
        "Yale Bright Star Catalog (Local)": "Yale bright star catalog (local copy)",
        "IAU Constellation Catalog (Local)": "IAU constellation catalog (local copy)",
        "Climate Normals (Local)": "Climate normals (local copy)",
        "Beaufort Scale (Local)": "Beaufort scale (local copy)",
        "Standard Statistical Tables (Local)": "Standard statistical tables (local copy)",
        "Mathematical Constants (Local)": "Mathematical constants (local copy)",
        # [V51] SLM internal knowledge bases
        "internal_kb": "Internal knowledge base (curated biology/medical facts)",
        "internal knowledge base": "Internal knowledge base (curated facts)",
        "CODATA constant": "CODATA internationally agreed standard",
    }

    # Empirical sources — trust based on observation (lower trust than AUTHORITATIVE)
    EMPIRICAL_SOURCES = {
        "Binance": "Binance is a high-volume crypto exchange",
        "Coinbase": "Coinbase is a regulated US crypto exchange",
        "Kraken": "Kraken is a established crypto exchange",
        "Bitstamp": "Bitstamp is a long-running crypto exchange",
        "KuCoin": "KuCoin is a global crypto exchange",
        "CoinGecko": "CoinGecko is a crypto price aggregator",
        "PubChem": "PubChem is NIH's chemical database",
        "Wikidata": "Wikidata is a structured knowledge base",
        "Open-Meteo": "Open-Meteo is a reliable weather API",
        "Open-Meteo-Archive": "Open-Meteo Archive is historical weather data",
        "wttr.in": "wttr.in is a weather service",
        "Frankfurter": "Frankfurter uses European Central Bank data",
        "open.er-api.com": "open.er-api.com provides free currency rates",
        "REST Countries": "REST Countries API provides official country data",
        "REST Countries API": "REST Countries API provides official country data",
        "Wikipedia": "Wikipedia is community-edited (moderate trust)",
        "KnowledgeCache": "Knowledge cache stores previously verified facts",
        "LocalDB": "Local database stores curated facts",
    }

    def recursive_why(self, question: str, primary_source: str,
                      evidence_type: str) -> RecursiveWhyResult:
        """
        Recursive Why chain.

        [V37 FIX] Handle multi-source consensus patterns (median/weighted).
        [V50 FIX] AUTHORITATIVE_SOURCES terminate as "trusted_source" (not "unprovable").
                  This is the proper fix for the CognitiveGate issue where PubChem/CODATA
                  data was being downgraded because RecursiveWhy returned "unprovable".
                  Now: PubChem → "trusted_source" → CognitiveGate sees this and
                  does NOT downgrade. No more need for the is_verified_source hack.
        """
        chain: list[WhyLevel] = []
        current_source = primary_source
        current_question = f"Tại sao {question}?"

        # [V50] Check authoritative sources FIRST — before multi-source pattern check
        # because "weighted(PubChem)" should match PubChem in AUTHORITATIVE_SOURCES
        if current_source:
            src_lower = current_source.lower()
            # Check if any authoritative source name appears in current_source
            for auth_src, auth_desc in self.AUTHORITATIVE_SOURCES.items():
                if (len(auth_src) >= 4 and _re.search(r'\b' + _re.escape(auth_src.lower()) + r'\b', src_lower)) or auth_src.lower() == src_lower:  # [V104.32 #21] was: substring matched "who" in "whoever"
                    why_level = WhyLevel(
                        level=0,
                        question=current_question,
                        answer=auth_desc,
                        trust_source=auth_src,
                    )
                    chain.append(why_level)
                    return RecursiveWhyResult(
                        chain=chain,
                        depth_reached=0,
                        terminated_at="trusted_source",
                        final_trust="empirical_authoritative",
                    )

        # [V37 FIX] If source is a multi-source consensus (median/weighted),
        # extract individual sources and trace trust to empirical
        if current_source and (current_source.startswith("median(") or
                                current_source.startswith("weighted(")):
            # Multi-source consensus → empirical (based on individual sources)
            # Extract source names from pattern like "median(Binance,Coinbase,Kraken)"
            import re
            sources_in_pattern = re.findall(r'[A-Za-z][A-Za-z\.\-]+', current_source)
            # Check if all sources are empirical
            all_empirical = all(s in self.EMPIRICAL_SOURCES for s in sources_in_pattern if s not in ('median', 'weighted'))

            if all_empirical:
                # Level 0: answer is multi-source consensus
                why_level = WhyLevel(
                    level=0,
                    question=current_question,
                    answer=f"Multi-source consensus from {current_source}",
                    trust_source=current_source,
                )
                why_level.next_question = f"Tại sao tin các sources trong {current_source}?"
                chain.append(why_level)

                # Level 1: why trust these sources?
                source_list = ", ".join(sources_in_pattern[:3])
                why_level_1 = WhyLevel(
                    level=1,
                    question=f"Tại sao tin {source_list}?",
                    answer="These are independent, established data sources",
                    trust_source="multi_source_consensus",
                )
                chain.append(why_level_1)

                return RecursiveWhyResult(
                    chain=chain,
                    depth_reached=1,
                    terminated_at="trusted_source",  # [V50] was "unprovable"
                    final_trust="empirical_authoritative",  # [V50] was "empirical"
                )

        for level in range(self.MAX_DEPTH):
            # Check if current source is axiom
            if current_source in self.AXIOM_SOURCES:
                why_level = WhyLevel(
                    level=level,
                    question=current_question,
                    answer=self.AXIOM_SOURCES[current_source],
                    trust_source=current_source,
                    is_axiom=True,
                )
                chain.append(why_level)
                return RecursiveWhyResult(
                    chain=chain,
                    depth_reached=level,
                    terminated_at="axiom",
                    final_trust="axiomatic",
                )

            # Check if empirical
            if current_source in self.EMPIRICAL_SOURCES:
                empirical_answer = self.EMPIRICAL_SOURCES[current_source]
                why_level = WhyLevel(
                    level=level,
                    question=current_question,
                    answer=empirical_answer,
                    trust_source=current_source,
                )

                # Next question: why trust this empirical source?
                next_q = f"Tại sao tin {current_source}?"
                why_level.next_question = next_q
                chain.append(why_level)

                # Level 1+: why trust?
                # The trust itself is based on "reputation" — which is an axiom of sorts
                if level >= 1:
                    # We've gone deep enough — terminate as empirical
                    return RecursiveWhyResult(
                        chain=chain,
                        depth_reached=level,
                        terminated_at="trusted_source",  # [V50] was "unprovable"
                        final_trust="empirical",  # [V50] keep empirical (lower than authoritative)
                    )

                current_question = next_q
                current_source = "reputation"  # Meta-source
                continue

            # Unknown source — unprovable
            why_level = WhyLevel(
                level=level,
                question=current_question,
                answer=f"Source '{current_source}' trust is unprovable",
                trust_source=current_source,
                is_unprovable=True,
            )
            chain.append(why_level)
            return RecursiveWhyResult(
                chain=chain,
                depth_reached=level,
                terminated_at="unprovable",
                final_trust="unprovable",
            )

        # Max depth reached
        return RecursiveWhyResult(
            chain=chain,
            depth_reached=self.MAX_DEPTH,
            terminated_at="max_depth",
            final_trust="unprovable",
        )
