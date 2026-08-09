"""
SCP - Viet Nam | Self-Correcting Pipeline
[V96] BiologyDataSource - Data source cho Sinh học
"""

import logging
import os
import re
from typing import Any, Optional

import requests
from defusedxml import ElementTree as ET  # nosec B314 — defusedxml hardens XXE

from scp.core.api_utils import fetch_with_retry  # [V5.8-API]
from scp.interfaces.data_source import IDataSource

logger = logging.getLogger(__name__)


def _wb_match(key: str, text_lower: str) -> bool:
    """[ROOT-FIX 4] Word-boundary match — prevents 'ATA' matching 'd**ata**',
    'Ala' matching 'b**ala**nce', 'Pro' matching '**pro**cess', etc.
    Uses Unicode-aware lookarounds so Vietnamese diacritics work too.
    """
    if not key or not text_lower:
        return False
    if key == text_lower:
        return True
    pattern = r'(?<![\wÀ-ỹ])' + re.escape(key) + r'(?![\wÀ-ỹ])'
    return bool(re.search(pattern, text_lower))


class BiologyDataSource(IDataSource):
    """
    Data source cho các câu hỏi Sinh học.
    Hỗ trợ: DNA, RNA, tế bào, enzyme, loài.
    """

    def __init__(self):
        self._cache: dict[str, Any] = {}
        # [V5.8-API] NCBI E-utilities API key (taxonomy db)
        self._ncbi_api_key = os.environ.get("NCBI_API_KEY", "").strip() or os.environ.get("PUBMED_API_KEY", "").strip()

        # DNA/RNA info
        self._genetic_code = {
            "TTT": "Phe", "TTC": "Phe", "TTA": "Leu", "TTG": "Leu",
            "CTT": "Leu", "CTC": "Leu", "CTA": "Leu", "CTG": "Leu",
            "ATT": "Ile", "ATC": "Ile", "ATA": "Ile", "ATG": "Met",
            "GTT": "Val", "GTC": "Val", "GTA": "Val", "GTG": "Val",
        }

        # Amino acids
        self._amino_acids = {
            "Ala": "Alanine", "Arg": "Arginine", "Asn": "Asparagine",
            "Asp": "Aspartic acid", "Cys": "Cysteine", "Gln": "Glutamine",
            "Glu": "Glutamic acid", "Gly": "Glycine", "His": "Histidine",
            "Ile": "Isoleucine", "Leu": "Leucine", "Lys": "Lysine",
            "Met": "Methionine", "Phe": "Phenylalanine", "Pro": "Proline",
            "Ser": "Serine", "Thr": "Threonine", "Trp": "Tryptophan",
            "Tyr": "Tyrosine", "Val": "Valine",
        }

        # Cell info
        self._cells = {
            "mitochondria": {"size": "1-10 μm", "function": "ATP production"},
            "ribosome": {"size": "20 nm", "function": "Protein synthesis"},
            "nucleus": {"size": "5-10 μm", "function": "DNA storage"},
            "lysosome": {"size": "0.1-1.2 μm", "function": "Cellular digestion"},
            "chloroplast": {"size": "5-10 μm", "function": "Photosynthesis"},
        }

        # Biological constants
        self._constants = {
            "số base DNA người": 3.2e9,
            "human genome size": 3.2e9,
            "số chromosome người": 46,
            "human chromosomes": 46,
            "kích thước gene trung bình": 2700,  # base pairs
            "average gene size": 2700,
            "tốc độ phiên mã": 30,  # nucleotides/second
            "transcription rate": 30,
            "tốc độ dịch mã": 15,  # amino acids/second
            "translation rate": 15,
        }


    @property
    def name(self) -> str:
        return "BiologyDataSource"

    @property
    def priority(self) -> int:
        return 5

    @property
    def ttl(self) -> int:
        return 86400

    def get_supported_intents(self) -> list[str]:
        return ["lookup", "query", "fact"]

    def can_handle(self, intent: str, entity: Optional[str] = None) -> bool:
        return True

    def fetch(self, intent: str, entity: str, **kwargs):
        result = self.query(entity or intent)
        if result.get("found"):
            return {"value": result.get("answer", ""), "source": "Biology", "metadata": result}
        return None

    def health_check(self) -> bool:
        return True

    def query(self, question: str) -> dict[str, Any]:
        """Query biology data."""
        q = question.lower().strip()

        if q in self._cache:
            return self._cache[q]

        result = {"found": False, "answer": None, "confidence": 0.0}

        # Check genetic code
        for codon, aa in self._genetic_code.items():
            # [ROOT-FIX 4] Was `codon.lower() in q or aa.lower() in q` — substring
            # match → 'ATA' matched 'data', 'Ala' matched 'balance', 'Pro' matched
            # 'process', 'Met' matched 'method', etc. Now uses word-boundary match.
            if _wb_match(codon.lower(), q) or _wb_match(aa.lower(), q):
                result = {
                    "found": True,
                    "answer": f"Codon {codon} → {aa}",
                    "confidence": 1.0,
                    "source": "Standard Genetic Code"
                }
                break

        # Check amino acids
        for code, name in self._amino_acids.items():
            # [ROOT-FIX 4] Same substring → word-boundary fix.
            if _wb_match(code.lower(), q) or _wb_match(name.lower(), q):
                result = {
                    "found": True,
                    "answer": f"{code} = {name}",
                    "confidence": 1.0,
                    "source": "Amino Acid Database"
                }
                break

        # Check constants
        for name, value in self._constants.items():
            # [ROOT-FIX 4] Same substring → word-boundary fix.
            if _wb_match(name.lower(), q):
                result = {
                    "found": True,
                    "answer": f"{name} = {value}",
                    "confidence": 1.0,
                    "source": "Biological Database"
                }
                break

        # [V5.8-API] Local DB miss → try NCBI taxonomy API for species/genus queries.
        # Skip very short queries (likely codons/amino acids — not species names).
        if not result.get("found") and len(q) >= 4:
            api_result = self._fetch_from_ncbi_taxonomy(question)
            if api_result:
                result = api_result

        self._cache[q] = result
        return result

    # [V5.8-API] NCBI Taxonomy integration
    def _fetch_from_ncbi_taxonomy(self, question: str) -> Optional[dict[str, Any]]:
        """
        [V5.8-API] Fetch taxonomic data from NCBI E-utilities (taxonomy db).
        Step 1: esearch.fcgi (JSON) → taxid list
        Step 2: efetch.fcgi (XML) → parsed TaxName / Rank / Lineage
        Returns dict or None.
        """
        if not question or not question.strip():
            return None
        # Extract a clean search term from the question (strip common stopwords)
        text = question.strip()
        cleaned = re.sub(
            r'\b(what|is|the|a|an|of|for|on|about|species|genus|taxon|taxonomy|là|gì|của|về|tìm|loài|giống)\b',
            ' ', text, flags=re.IGNORECASE,
        ).strip()
        term = re.sub(r'\s+', ' ', cleaned).strip()
        if len(term) < 4:
            return None

        api_key_param = f"&api_key={self._ncbi_api_key}" if self._ncbi_api_key else ""
        esearch_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=taxonomy&term={requests.utils.quote(term)}"
            f"&retmode=json&retmax=3{api_key_param}"
        )
        try:
            esearch_data = fetch_with_retry(esearch_url, headers={"User-Agent": "SCP/1.0"}, timeout=5)
            if not esearch_data:
                return None
            id_list = esearch_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return None
        except Exception as e:
            logger.warning(f"[V5.8-API] NCBI taxonomy esearch failed for '{term}': {e}")
            return None

        # efetch XML parse — use requests directly (fetch_with_retry expects JSON)
        taxid = id_list[0]
        efetch_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=taxonomy&id={taxid}&retmode=xml{api_key_param}"
        )
        try:
            resp = requests.get(efetch_url, timeout=8, headers={"User-Agent": "SCP/1.0"})
            if resp.status_code != 200 or not resp.text:
                return None
            root = ET.fromstring(resp.text)
            taxon = root.find('.//Taxon')
            if taxon is None:
                return None
            tax_name = (taxon.findtext('ScientificName') or '').strip()
            rank = (taxon.findtext('Rank') or '').strip()
            lineage = (taxon.findtext('Lineage') or '').strip()
            if not tax_name:
                return None
            answer_parts = [f"Species: {tax_name}"]
            if rank and rank != 'no rank':
                answer_parts.append(f"Rank: {rank}")
            if lineage:
                # Truncate lineage if very long
                lin_str = lineage if len(lineage) <= 400 else lineage[:400] + "..."
                answer_parts.append(f"Lineage: {lin_str}")
            return {
                "found": True,
                "answer": " | ".join(answer_parts),
                "confidence": 0.85,
                "source": "NCBI Taxonomy API",
                "taxid": taxid,
                "scientific_name": tax_name,
            }
        except Exception as e:
            logger.warning(f"[V5.8-API] NCBI taxonomy efetch failed for taxid {taxid}: {e}")
            return None
