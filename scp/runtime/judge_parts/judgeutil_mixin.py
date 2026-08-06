"""
RealityJudge utility mixin — extracted from judge.py (Task 19-A).
 kept verbatim; only the method location changed.
"""
import logging
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger("scp.runtime.judge")


class JudgeUtilMixin:
    """Mixin for RealityJudge — provides _extract_value + _check_consistency."""

    def _extract_value(self, question: str, answer: str) -> Optional[str]:
        """
        [V73] Extract comparable value from an answer based on question type.

        Strategy:
          - "When was X born/founded/established?" → extract year (4-digit number)
          - "What is the population of X?" → extract number (with optional million/thousand)
          - "What type of thing is X?" / "X là loại gì?" → extract first noun phrase
          - "What does X say in Y:Z?" → extract verse text (return as-is)
          - "What is the recipe/nutritional value?" → extract key numbers
          - Otherwise: return answer as-is (truncated)

        Returns: extracted value or None if cannot extract
        """
        if not answer:
            return None
        import re
        q = question.lower().strip()
        ans = answer.strip()

        # [V73.1] Skip value extraction for capital/country questions — they use string match
        # "Thủ đô của Việt Nam là gì?" → "Hà Nội" should match by string, not extract
        if any(kw in q for kw in ['thủ đô', 'capital of', 'capital is', 'quốc gia',
                                    'country is', 'country of']):
            return ans[:100]

        # Year extraction — "born", "founded", "established", "when"
        if any(kw in q for kw in ['when was', 'born', 'founded', 'established',
                                    'created', 'opened', 'năm nào', 'khi nào']):
            # Find first 4-digit year
            m = re.search(r'\b(1[0-9]{3}|20[0-9]{2}|21[0-9]{2})\b', ans)
            if m:
                return m.group(1)

        # Population/number extraction
        if any(kw in q for kw in ['population', 'how many', 'dân số', 'bao nhiêu']):
            # Find number with optional million/thousand/billion suffix
            m = re.search(r'([\d,.]+)\s*(million|billion|thousand|triệu|tỷ|nghìn)?',
                          ans, re.IGNORECASE)
            if m:
                num = m.group(1).replace(',', '')
                suffix = (m.group(2) or '').lower()
                try:
                    val = float(num)
                    if 'million' in suffix or 'triệu' in suffix:
                        val *= 1_000_000
                    elif 'billion' in suffix or 'tỷ' in suffix:
                        val *= 1_000_000_000
                    elif 'thousand' in suffix or 'nghìn' in suffix:
                        val *= 1_000
                    return str(int(val)) if val == int(val) else str(val)
                except Exception:
                    return m.group(1)

        # Nutritional value — extract "calories=X"
        if 'nutritional value' in q or 'nutrition' in q:
            m = re.search(r'calories[=\s:]+(\d+)', ans, re.IGNORECASE)
            if m:
                return f"calories={m.group(1)}"

        # [V77 FIX] Type classification — extract TYPE noun, not first 5 words
        # Was: "Saturn is a gas giant" → first 5 words = "Saturn is a gas giant" (wrong!)
        # Now: extract noun phrase AFTER "is a/an" pattern
        if 'what type of thing is' in q or 'là loại gì' in q:
            # Pattern: "X is a/an/the Y" → Y
            import re as _re2
            type_patterns = [
                r'\bis\s+(?:a|an|the)\s+([^.,;]+?)(?:[.,;]|\s+(?:that|which|who|where|when)\s|$)',
                r'\blà\s+(?:một\s+)?([^.,;]+?)(?:[.,;]|\s+(?:được|tại|trong|thuộc)\s|$)',
                r'\bare\s+(?:a|an|the)\s+([^.,;]+?)(?:[.,;]|$)',
                r'\bwas\s+(?:a|an|the)\s+([^.,;]+?)(?:[.,;]|$)',
            ]
            for pat in type_patterns:
                m = _re2.search(pat, ans, _re2.IGNORECASE)
                if m:
                    type_phrase = m.group(1).strip().rstrip(',.')
                    # Normalize common types
                    type_lower = type_phrase.lower()
                    type_aliases = {
                        'gas giant': 'planet',
                        'ice giant': 'planet',
                        'terrestrial planet': 'planet',
                        'rocky planet': 'planet',
                        'dwarf planet': 'planet',
                        'gas giant planet': 'planet',
                    }
                    return type_aliases.get(type_lower, type_phrase)
            # Fallback: first 5 words
            words = ans.split()[:5]
            return ' '.join(words).rstrip(',.')

        # Quote/verse — return as-is (already short)
        if 'what does the bible say' in q or 'verse' in q or 'quote' in q:
            return ans[:200]

        # Recipe — return dish name (first 5 words)
        if 'recipe for' in q or 'how do you make' in q:
            words = ans.split()[:5]
            return ' '.join(words).rstrip(',.')

        # Default: return first 100 chars (truncated)
        return ans[:100] if ans else None


    def _check_consistency(self, responses: list[dict]) -> bool:
        """Kiểm tra đồng thuận giữa các SLM.
        [V91 FIX] Tightened consistency check — extract key facts, not just string similarity.
        """
        if len(responses) < 2:
            return True
        answers = [r.get("answer", "") for r in responses if r.get("answer")]
        if len(answers) < 2:
            return True

        import re

        # [V91] Step 1: Extract numeric values from each answer
        all_numbers = []
        for ans in answers:
            # Extract all numbers (including decimals)
            nums = set(re.findall(r'-?\d+\.?\d*', ans))
            # Filter out trivial numbers (0, 1, 2)
            nums = {n for n in nums if float(n) not in (0, 1, 2)}
            all_numbers.append(nums)

        # [V91] Step 2: If answers have numbers, check if they share KEY numbers
        has_numbers = any(len(nums) > 0 for nums in all_numbers)
        if has_numbers:
            # All answers must share at least 1 significant number
            common = all_numbers[0]
            for nums in all_numbers[1:]:
                if nums:  # Only intersect with answers that have numbers
                    common = common & nums
            if len(common) >= 1:
                return True  # Share key number → consistent
            # Numbers exist but NO common number → CONFLICT
            return False

        # [V91] Step 3: No numbers — use string similarity (stricter)
        first = answers[0]
        similarities = [SequenceMatcher(None, first, a).ratio() for a in answers[1:]]
        avg = sum(similarities) / len(similarities)
        if avg > 0.6:
            return True

        # [V91] Step 4: Check for shared key words (nouns, names)
        # Extract capitalized words (proper nouns) from each answer
        all_names = []
        for ans in answers:
            names = set(re.findall(r'\b[A-Z][a-z]+\b', ans))
            # Filter common words
            names = {n for n in names if n.lower() not in ('the', 'this', 'that', 'what', 'who', 'when', 'where')}
            all_names.append(names)
        if all_names:
            common = all_names[0]
            for names in all_names[1:]:
                if names:
                    common = common & names
            if len(common) >= 1:
                return True  # Share proper noun → consistent

        return False  # [V91] No shared numbers, names, or similarity → NOT consistent


