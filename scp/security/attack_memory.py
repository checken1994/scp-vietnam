"""
SCP V98 — AttackPatternMemory
Copyright (c) 2026 Minh. MIT License.

Port từ WHY H3 — học pattern attack từ bypass log, tự sinh rule mới.

Naming convention: <Purpose>Memory (world standard, e.g. PatternMemory, AttackMemory).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import re as _re  # [V104.32 #23] needed for word-boundary check
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.security.attack_memory")


PROMOTE_AFTER_HITS = 2  # [V104.20 #2 ROLLBACK] 1 was too aggressive — 2 hits required
AUTO_DISABLE_FP_RATE = 0.3
MAX_DYNAMIC_RULES = 200


@dataclass
class DynamicRule:
    """Rule tự sinh từ bypass learning."""
    rule_id: str
    rule_type: str  # keyword | pattern | strategy
    pattern: str
    description: str = ""
    created_at: float = 0.0
    hits: int = 0
    false_positives: int = 0
    promoted: bool = False
    promoted_at: float = 0.0
    disabled: bool = False
    source_signatures: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "pattern": self.pattern,
            "description": self.description,
            "created_at": self.created_at,
            "hits": self.hits,
            "false_positives": self.false_positives,
            "promoted": self.promoted,
            "disabled": self.disabled,
            "source_signatures": self.source_signatures,
            "examples": self.examples[:3],
        }


# Attack keywords for keyword learning
ATTACK_KEYWORDS = [
    "ignore", "previous", "instructions", "forget", "system",
    "prompt", "reveal", "dan", "jailbreak", "override",
    "bỏ", "qua", "lệnh", "quên", "hệ", "thống",
]


class AttackPatternMemory:
    """Learn from bypass + auto-fix + generate new rules.

    Naming convention: <Purpose>Memory (world standard).

    3 tiers learning:
      Tầng 1: Keyword learning — học keyword mới
      Tầng 2: Pattern learning — học cấu trúc câu attack
      Tầng 3: Strategy learning — học multi-turn manipulation

    Auto-rule generation:
      Khi 1 pattern xuất hiện 3+ lần trong bypass_log → promote thành rule
      Test rule mới trên lịch sử bypass → confirm không false positive → activate
      Rule mới được inject vào runtime memory — H1/H8 query để block
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bypasses: list[dict[str, Any]] = []
        self.dynamic_rules: dict[str, DynamicRule] = {}
        self._compiled_rules: dict[str, re.Pattern] = {}
        self._keyword_index: dict[str, str] = {}  # keyword → rule_id
        self._lock_hashes: set = set()
        # [V5.8-DEDUP] Dedup hash set for record_bypass — keyed by
        # md5(f"{attack_type}:{question[:200]}"). Populated at load time
        # from existing bypasses.jsonl so we never re-store a duplicate.
        # TẠI SAO: bypasses.jsonl was 47686 lines but 8 cross_lang_* variants
        # of the same ~6000 questions = ~48000 duplicates. Dedup at write-time
        # prevents further bloat; one-time cleanup script removes existing dupes.
        self._bypass_hashes: set = set()
        # [V104.50 #P1-9] Re-entrant lock guarding ALL read-modify-write
        # cycles on dynamic_rules.jsonl + bypasses.jsonl + in-memory dicts.
        # Without this, concurrent record_bypass calls corrupt the JSONL
        # (truncate-rewrite races) and lose rule.hits increments.
        self._lock: threading.RLock = threading.RLock()

        # Initial load runs unlocked — __init__ is single-threaded by contract.
        self._load_bypasses()
        self._load_rules()

    def _load_bypasses(self):
        """Load bypass history."""
        bypass_file = self.data_dir / "bypasses.jsonl"
        if not bypass_file.is_file():
            return
        try:
            for line in bypass_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.bypasses.append(rec)
                    # [V5.8-DEDUP] Pre-populate dedup hash set from existing
                    # records so we don't re-add duplicates that are already
                    # on disk. TẠI SAO: bypasses.jsonl was 6.8 MB / 47686 lines
                    # but runtime analysis showed 8 cross_lang_* variants of
                    # the same ~6000 questions = ~48000 duplicates. Without
                    # seeding the hash set at load, every duplicate already on
                    # disk would be re-storable on next record_bypass call.
                    try:
                        _at = rec.get("attack_type", "") or ""
                        _q = rec.get("question", "") or ""
                        _h = hashlib.sha256(f"{_at}:{_q[:200]}".encode()).hexdigest()
                        self._bypass_hashes.add(_h)
                        # [V5.8-DEDUP] Also seed canonical (cross_lang_en)
                        # hashes for cross_lang_<other> records that already
                        # exist on disk — so a new cross_lang_zh variant of
                        # an already-stored cross_lang_zh is still deduped by
                        # its own attack_type hash (above), but a NEW en-variant
                        # of an existing zh-variant is also deduped (below).
                        if _at.startswith("cross_lang_") and _at != "cross_lang_en":
                            _canon_h = hashlib.sha256(
                                f"cross_lang_en:{_q[:200]}".encode()
                            ).hexdigest()
                            self._bypass_hashes.add(_canon_h)
                    except Exception as e:
                        logger.warning(f"Silent except: {e}")  # defensive — bad record shouldn't break load
            logger.info(
                f"[AttackPatternMemory] Loaded {len(self.bypasses)} bypasses "
                f"({len(self._bypass_hashes)} dedup hashes)"
            )
        except Exception as e:
            logger.warning(f"[AttackPatternMemory] Load bypasses error: {e}")

    def _load_rules(self):
        """Load existing dynamic rules."""
        rules_file = self.data_dir / "dynamic_rules.jsonl"
        if not rules_file.is_file():
            return
        try:
            for line in rules_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    data = json.loads(line)
                    rule = DynamicRule(**{k: data.get(k) for k in DynamicRule.__dataclass_fields__})
                    self.dynamic_rules[rule.rule_id] = rule
                    if rule.promoted and not rule.disabled:
                        self._compile_rule(rule)
            logger.info(f"[AttackPatternMemory] Loaded {len(self.dynamic_rules)} rules")
        except Exception as e:
            logger.warning(f"[AttackPatternMemory] Load rules error: {e}")

    def _compile_rule(self, rule: DynamicRule):
        """Compile regex pattern for rule."""
        try:
            if rule.rule_type == "pattern":
                self._compiled_rules[rule.rule_id] = re.compile(rule.pattern, re.IGNORECASE)
            elif rule.rule_type == "keyword":
                self._keyword_index[rule.pattern.lower()] = rule.rule_id
        except Exception as e:
            logger.debug(f"[AttackPatternMemory] Compile error for {rule.rule_id}: {e}")

    def record_bypass(
        self,
        question: str,
        answer: str,
        attack_type: str,
        signatures: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record 1 bypass event.

        Args:
            question: Question that bypassed
            answer: Answer that was returned
            attack_type: Type of attack
            signatures: Attack signatures detected

        Returns:
            Dict with bypass_id + rule info

        [V104.50 #P1-9] Whole body guarded by self._lock (RLock) so the
        full read-modify-write cycle (append bypass → generate/update rule
        → check promotion → save rule via truncate-rewrite) is atomic.
        Concurrent record_bypass calls from H8 background thread, judge main
        thread, and ThreatSimulator thread can no longer interleave and
        corrupt dynamic_rules.jsonl or lose rule.hits increments.
        The lock is NOT held across any network call (this method does none).

        [V5.8-DEDUP] Duplicate check at top — TẠI SAO: attack_crawler +
        cross_language_learner generate 8 language variants (en/zh/ja/ko/fr/
        de/es/ar) of every attack pattern. Without dedup, the same logical
        bypass is stored 8 times → bypasses.jsonl ballooned to 6.8 MB /
        47686 lines with ~48000 duplicates. Dedup key = md5 of
        "{attack_type}:{question[:200]}". For cross_lang_<non-en> variants
        we ALSO check against the cross_lang_en canonical hash — if the
        English version was already stored, the translated variant is
        skipped (canonical-only storage). The hash set is seeded at _load
        time so existing on-disk duplicates are not re-storable.
        """
        with self._lock:
            # [V5.8-DEDUP] cross_lang_<non-en> → check canonical cross_lang_en hash
            _is_cross_lang_variant = (
                isinstance(attack_type, str)
                and attack_type.startswith("cross_lang_")
                and attack_type != "cross_lang_en"
            )
            if _is_cross_lang_variant:
                _canon_hash = hashlib.sha256(
                    f"cross_lang_en:{(question or '')[:200]}".encode()
                ).hexdigest()
                if _canon_hash in self._bypass_hashes:
                    logger.debug(
                        f"[V5.8-DEDUP] Skipping translated_variant "
                        f"(attack_type={attack_type}, canonical cross_lang_en exists)"
                    )
                    return {
                        "bypass_id": None,
                        "rule_id": None,
                        "rule_promoted": False,
                        "dedup_skipped": "translated_variant",
                    }

            # [V5.8-DEDUP] Same attack_type + same question → duplicate
            _bypass_hash = hashlib.sha256(
                f"{attack_type}:{(question or '')[:200]}".encode()
            ).hexdigest()
            if _bypass_hash in self._bypass_hashes:
                logger.debug(
                    f"[V5.8-DEDUP] Skipping duplicate bypass "
                    f"(hash={_bypass_hash[:8]}, attack_type={attack_type})"
                )
                return {
                    "bypass_id": None,
                    "rule_id": None,
                    "rule_promoted": False,
                    "dedup_skipped": "duplicate",
                }
            self._bypass_hashes.add(_bypass_hash)
            bypass_id = hashlib.sha256(f"{question}{time.time()}".encode()).hexdigest()[:16]
            bypass = {
                "id": bypass_id,
                "timestamp": time.time(),
                "question": question[:500],
                "answer": answer[:500],
                "attack_type": attack_type,
                "signatures": signatures or [],
            }
            self.bypasses.append(bypass)
            self._save_bypass(bypass)

            # Generate or update rule (also locked — RLock allows re-entry).
            rule = self._generate_or_update_rule(question, attack_type, signatures)
            if rule:
                self._check_promotion(rule)

            return {
                "bypass_id": bypass_id,
                "rule_id": rule.rule_id if rule else None,
                "rule_promoted": rule.promoted if rule else False,
            }

    def _generate_or_update_rule(
        self,
        question: str,
        attack_type: str,
        signatures: list[str] | None = None,
    ) -> DynamicRule | None:
        """Generate new rule or update existing.

        [V104.50 #P1-9] Guarded by self._lock — the `rule.hits += 1`
        read-modify-write and the truncate-rewrite `_save_rule` must be
        atomic together to avoid lost increments and rule corruption.
        """
        with self._lock:
            keywords = self._extract_keywords(question)
            if not keywords:
                return None

            # Generate rule_id from keywords + attack_type
            # [FIX #7] TẠI SAO: MD5 has collision risk (CWE-327) — attacker could
            # craft 2 attacks with same rule_id → bypass detection. SHA256 same
            # API, just longer hash. Reality > Model: verified sha256 works.
            rule_id_input = f"{attack_type}:{':'.join(sorted(keywords))}"
            rule_id = f"auto_{hashlib.sha256(rule_id_input.encode()).hexdigest()[:8]}"

            if rule_id in self.dynamic_rules:
                # Update existing
                rule = self.dynamic_rules[rule_id]
                rule.hits += 1
                if question[:100] not in rule.examples:
                    rule.examples.append(question[:100])
                if signatures:
                    for sig in signatures:
                        if sig not in rule.source_signatures:
                            rule.source_signatures.append(sig)
            else:
                # Create new
                rule = DynamicRule(
                    rule_id=rule_id,
                    rule_type="keyword",
                    pattern=keywords[0],  # primary keyword
                    description=f"Auto-generated for {attack_type}",
                    created_at=time.time(),
                    hits=1,
                    source_signatures=signatures or [],
                    examples=[question[:100]],
                )
                self.dynamic_rules[rule_id] = rule

            self._save_rule(rule)
            return rule

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract attack keywords from text.

        [BUG-3 FIX] TÁI SAO: trước đây chỉ extract từ ATTACK_KEYWORDS list (hardcoded).
        25k bypasses không generate rule vì _extract_keywords không tìm thấy keyword
        trong attack text → return [] → _generate_or_update_rule return None → 0 rules.
        Fix: ngoài ATTACK_KEYWORDS, cũng extract từ signatures passed vào + n-grams.
        """
        text_lower = text.lower()
        keywords = [kw for kw in ATTACK_KEYWORDS if len(kw) >= 4 and _re.search(r'\b' + _re.escape(kw) + r'\b', text_lower)]

        # [BUG-3 FIX] Nếu không tìm thấy keyword từ list → extract từ text directly
        # Lấy các từ dài ≥4 ký tự xuất hiện ≥2 lần trong text
        if not keywords:
            import re as _re2
            words = _re2.findall(r'\b[a-z]{4,}\b', text_lower)
            from collections import Counter
            word_counts = Counter(words)
            # Lấy top 3 từ xuất hiện nhiều nhất (có thể là attack pattern)
            keywords = [w for w, c in word_counts.most_common(3) if c >= 1 and w not in {
                "that", "this", "with", "from", "have", "they", "will", "what",
                "your", "were", "been", "more", "when", "some", "them", "then",
                "than", "also", "just", "like", "such", "into", "only", "very",
                "does", "done", "each", "make", "made", "most", "over", "take",
                "want", "well", "here", "there", "where", "which", "would", "could",
                "should", "about", "after", "before", "every", "never", "always",
                "please", "ignore", "forget", "system", "prompt", "instructions",
                "previous", "above", "below", "these", "those", "their", "other",
            }]
            if keywords:
                logger.debug(f"[BUG-3] Extracted keywords from text: {keywords}")

        return keywords[:5]  # Limit to 5 keywords

    def _check_promotion(self, rule: DynamicRule):
        """Check if rule should be promoted.

        [V104.50 #P1-9] Guarded by self._lock — promotion mutates rule state
        and rebuilds _compiled_rules/_keyword_index; must be atomic with the
        increment that triggered it (caller already holds the lock via
        record_bypass, but RLock makes it safe to call standalone too).

        [BUG-3 FIX] TÁI SAO: PROMOTE_AFTER_HITS=2 nhưng mỗi attack chỉ hit 1 lần
        (different attack text → different rule_id → hits=1 → never promoted).
        Fix: auto-promote sau 1 hit nếu rule có source_signatures (came from
        ThreatSimulator/H8 — đã verified là attack).
        """
        with self._lock:
            if rule.promoted or rule.disabled:
                return

            # [BUG-3 FIX] Auto-promote sau 1 hit nếu có source_signatures
            # (came from verified attack source — ThreatSimulator/H8)
            should_promote = False
            if rule.hits >= PROMOTE_AFTER_HITS:
                should_promote = True
            elif rule.hits >= 1 and rule.source_signatures:
                # [BUG-3] Rule came from verified attack → promote immediately
                should_promote = True
                logger.info(f"[BUG-3] Auto-promoting rule {rule.rule_id} (1 hit + signatures={rule.source_signatures})")

            if should_promote:
                # Check false positive rate
                if rule.false_positives > 0:
                    fp_rate = rule.false_positives / rule.hits
                    if fp_rate > AUTO_DISABLE_FP_RATE:
                        rule.disabled = True
                        logger.info(f"[AttackPatternMemory] Auto-disabled rule {rule.rule_id} (FP rate {fp_rate:.1%})")
                        return

                # Promote
                rule.promoted = True
                rule.promoted_at = time.time()
                self._compile_rule(rule)
                logger.info(f"[AttackPatternMemory] Promoted rule {rule.rule_id} (hits={rule.hits})")

    def check_against_rules(self, question: str) -> dict[str, Any]:
        """Check if question matches any active rule.

        [V104.50 #P1-9] Read-only but takes the lock to get a consistent
        snapshot of _keyword_index / _compiled_rules / dynamic_rules (a
        concurrent record_bypass could otherwise mutate them mid-iteration).
        """
        with self._lock:
            q_lower = question.lower()

            # Check keyword rules
            for keyword, rule_id in list(self._keyword_index.items()):
                # [V104.18 #7 FIX] Word boundary match (was: substring → "dan" in "danger")
                # [FALSE-POS-FIX] F402: use module-level _re (imported at L15), not loop-local import
                if _re.search(r'\b' + _re.escape(keyword) + r'\b', q_lower):
                    rule = self.dynamic_rules.get(rule_id)
                    if rule and rule.promoted and not rule.disabled:
                        return {
                            "matched": True,
                            "rule_id": rule_id,
                            "rule_type": "keyword",
                            "attack_type": rule.description,
                        }

            # Check pattern rules
            for rule_id, pattern in list(self._compiled_rules.items()):
                if pattern.search(question):
                    rule = self.dynamic_rules.get(rule_id)
                    if rule and rule.promoted and not rule.disabled:
                        return {
                            "matched": True,
                            "rule_id": rule_id,
                            "rule_type": "pattern",
                            "attack_type": rule.description,
                        }

            return {"matched": False}

    def _save_bypass(self, bypass: dict[str, Any]):
        """Save bypass to file.

        [V104.50 #P1-9] Append-mode write is inherently atomic at the OS
        level for small lines, but we still take the lock for the in-memory
        `self.bypasses.append` ordering (the caller record_bypass already
        holds it; RLock makes standalone calls safe too).
        """
        with self._lock:
            try:
                bypass_file = self.data_dir / "bypasses.jsonl"
                with open(bypass_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(bypass, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug(f"[AttackPatternMemory] Save bypass error: {e}")

    def _save_rule(self, rule: DynamicRule):
        """Save rule to file (rewrite all).

        [V104.50 #P1-9] CRITICAL: truncate-rewrite ("w" mode) is NOT
        concurrency-safe. Two concurrent calls both truncate → last writer
        wins → earlier rules silently vanish. Guard with self._lock so the
        full read-iterate-write cycle is atomic with the in-memory mutation
        that preceded it (rule.hits += 1, promote, etc.). The caller
        _generate_or_update_rule already holds the lock; RLock allows
        re-entry and makes standalone calls (e.g. from tests) safe too.
        Additionally use atomic write (temp file + os.replace) so a crashed
        write cannot corrupt the existing file.
        """
        with self._lock:
            try:
                import os as _os
                rules_file = self.data_dir / "dynamic_rules.jsonl"
                tmp_file = rules_file.with_suffix(".jsonl.tmp")
                with open(tmp_file, "w", encoding="utf-8") as f:
                    for r in self.dynamic_rules.values():
                        f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                _os.replace(tmp_file, rules_file)
            except Exception as e:
                logger.debug(f"[AttackPatternMemory] Save rule error: {e}")

    def get_active_rules(self) -> list[dict[str, Any]]:
        """Get all promoted + active rules.

        [V104.50 #P1-9] Snapshot under lock to avoid concurrent mutation
        during iteration.
        """
        with self._lock:
            return [r.to_dict() for r in self.dynamic_rules.values() if r.promoted and not r.disabled]

    def stats(self) -> dict[str, Any]:
        # [V104.50 #P1-9] Snapshot under lock for consistent counts.
        with self._lock:
            return {
                "total_bypasses": len(self.bypasses),
                "total_rules": len(self.dynamic_rules),
                "active_rules": len(self.get_active_rules()),
                "promoted_rules": sum(1 for r in self.dynamic_rules.values() if r.promoted),
                "disabled_rules": sum(1 for r in self.dynamic_rules.values() if r.disabled),
                "by_type": {
                    t: sum(1 for r in self.dynamic_rules.values() if r.rule_type == t)
                    for t in ("keyword", "pattern", "strategy")
                },
            }


__all__ = ["DynamicRule", "AttackPatternMemory", "ATTACK_KEYWORDS"]
