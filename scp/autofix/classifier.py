"""
SCP Bug Classifier — determines which tier a bug belongs to.

The classification is the SAFETY BOUNDARY of SCP autonomy.
Getting this wrong means either:
  - Too aggressive → SCP changes logic without permission (violates "con người quyết định")
  - Too conservative → SCP can't fix simple bugs fast enough (violates "xử lý nhanh hơn con người")

Classification rules (ordered by priority):
  1. If bug changes verdict thresholds → Tier 3 (Permission)
  2. If bug changes Evidence-First logic → Tier 3 (Permission)
  3. If bug changes Constitution/KILL logic → Tier 3 (Permission)
  4. If bug changes security policy (when to block/abstain) → Tier 3 (Permission)
  5. If bug changes API boundary (what to withhold) → Tier 3 (Permission)
  6. If bug changes learning verification (what goes to KB) → Tier 3 (Permission)
  7. If bug is in attack-response path AND fix is a RESTRAINT → Tier 4 (Attack Mode)
  8. If bug is a behavior fix with clear correct answer → Tier 2 (Auto-Fix + Log)
  9. Otherwise → Tier 1 (Auto-Fix, no report)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class BugTier(IntEnum):
    """Bug severity tier — determines SCP autonomy level."""
    TIER_1_AUTO_FIX = 1          # SCP fixes, no report (implementation bugs)
    TIER_2_AUTO_FIX_LOG = 2      # SCP fixes + logs (behavior bugs, clear fix)
    TIER_3_PERMISSION = 3        # SCP asks human (logic bugs — changes WHAT SCP decides)
    TIER_4_ATTACK_MODE = 4       # SCP auto-fixes restraints during attack (reversible + logged)


@dataclass
class BugReport:
    """A detected bug + its classification."""
    file: str
    line: int
    bug_type: str                # NameError, SchemaMismatch, ThresholdChange, etc.
    description: str
    suggested_fix: str
    tier: BugTier
    is_restraint: bool = False   # For Tier 4: is this a tightening (not loosening)?
    is_reversible: bool = True   # For Tier 4: can we undo after attack?
    affects_logic: bool = False  # Does this change WHAT SCP decides?
    # [TIER3-AUTO] NEW: marks relaxation -- HARD LIMIT, never auto-approve
    # even when SCP_AUTO_APPROVE_TIER3=1. Set when RELAXATION_PATTERNS match.
    is_relaxation: bool = False  # If True -> always Tier 3, always human-only
    # [BUGFIX] removed duplicate `is_relaxation` definition (was copy-pasted at L52)


class BugClassifier:
    """Classify a detected bug into the appropriate autonomy tier."""

    # Patterns that indicate LOGIC bugs (Tier 3 — must ask permission)
    LOGIC_PATTERNS = {
        # Verdict threshold changes
        "threshold_change": r"confidence_threshold|PASS_THRESHOLD|FAIL_THRESHOLD|0\.85|0\.5\b|threshold.*change|lower.*threshold|raise.*threshold",
        # Evidence-First principle (broadened — catches "retrieval as verification", "upgrade verdict", etc.)
        "evidence_first": r"evidence_first|evidence.?first|slm_self_answer|real_value.*slm|ground_truth.*slm|retrieval.*verification|upgrade.*verdict|upgrade.*UNKNOWN|upgrade.*PARTIAL|treats.*retrieval|wikipedia.*fallback.*upgrade",
        # Constitution / KILL logic
        "constitution_kill": r"default_action.*KILL|constitution.*kill|inviolable|abstain.*kill|governance.*KILL",
        # Security policy
        "security_policy": r"block_ip|poison_response|reverse_probe|counter_attack|when.*block|block.*policy|security.*policy",
        # API boundary
        "api_boundary": r"withhold|abstain.*answer|final_answer.*clear|answer.*withheld|api.*boundary",
        # Learning verification
        "learning_verify": r"store_kb.*without.*verify|kb_store.*bypass|knowledge.*without.*judge|learning.*verify|store.*without.*verif",
        # Closed-loop threshold
        "closed_loop": r"policy_applier.*threshold|calibration.*factor|verdict_predictor.*skip|closed.?loop",
    }

    # Patterns that indicate ATTACK-RESPONSE path (Tier 4 candidate)
    ATTACK_PATH_PATTERNS = {
        "attack_response": r"attack_memory|h8_redteam|threat_detector|counter_response|"
                           r"canary_monitor|unified_detector|gcg_attack|dos_protection|"
                           r"security/|attack_classifier|memory_guard|multi_turn",
    }

    # Patterns that indicate RESTRAINT (tightening security — safe to auto-apply)
    # Broadened to catch "add lock", "add validation", "add check", etc.
    RESTRAINT_PATTERNS = {
        "tighten_threshold": r"raise.*threshold|lower.*confidence|stricter|tighten",
        "add_block": r"add.*block|new.*rule|block.*pattern|detect.*pattern|add.*lock|add.*mutex|add.*synchronize",
        "add_canary": r"inject.*canary|add.*canary",
        "add_log": r"add.*log|log.*attack|audit.*log|add.*monitor|add.*detect",
        "add_validation": r"add.*check|add.*valid|add.*guard|add.*verify",
    }

    # Patterns that indicate RELAXATION (NEVER auto-apply — always ask permission)
    RELAXATION_PATTERNS = {
        "lower_threshold": r"lower.*threshold|raise.*confidence|looser|relax",
        "remove_block": r"remove.*block|delete.*rule|skip.*detect",
        "allow": r"allow.*attack|whitelist|bypass.*security",
    }

    def classify(
        self,
        file: str,
        line: int,
        bug_type: str,
        description: str,
        suggested_fix: str,
        in_attack_mode: bool = False,
        tier_hint: BugTier | None = None,
    ) -> BugReport:
        """Classify a bug into a tier. Returns BugReport with tier + metadata.

        [V4.1-FIX] If tier_hint is provided (e.g. from enterprise scanner),
        HONOR it — don't override. Enterprise scanner sets TIER_3_PERMISSION
        for S603/S310/S404/S607 (skip-LLM rules). Without this hint, classifier
        would re-classify based on LOGIC_PATTERNS and might set TIER_2_AUTO_FIX_LOG
        → LLM gets called → fix→fail→retry loop.
        DNA SCP #7 (AutoFix safe) — respect upstream tier decisions.
        """
        # [V4.1-FIX] Honor tier_hint from enterprise scanner (skip-LLM rules)
        if tier_hint is not None:
            return BugReport(
                file=file, line=line, bug_type=bug_type,
                description=description, suggested_fix=suggested_fix,
                tier=tier_hint,
                affects_logic=True,  # mark as logic-affecting (human review)
            )

        combined = f"{file} {bug_type} {description} {suggested_fix}"
        combined.lower()

        # [TIER3-AUTO] Check RELAXATION first -- if relaxation match, it's
        # always Tier 3 + is_relaxation=True (HARD LIMIT, never auto-approve).
        # Must check BEFORE LOGIC_PATTERNS because LOGIC matches "lower threshold"
        # and returns early, bypassing the relaxation flag.
        is_relaxation = any(
            re.search(p, combined, re.IGNORECASE)
            for p in self.RELAXATION_PATTERNS.values()
        )
        if is_relaxation:
            return BugReport(
                file=file, line=line, bug_type=bug_type,
                description=description, suggested_fix=suggested_fix,
                tier=BugTier.TIER_3_PERMISSION,
                affects_logic=True,
                is_relaxation=True,  # locked -- SCP_AUTO_APPROVE_TIER3 cannot bypass
            )

        # Check if this is a LOGIC bug (Tier 3, NOT relaxation)
        for _pattern_name, pattern in self.LOGIC_PATTERNS.items():
            if re.search(pattern, combined, re.IGNORECASE):
                return BugReport(
                    file=file, line=line, bug_type=bug_type,
                    description=description, suggested_fix=suggested_fix,
                    tier=BugTier.TIER_3_PERMISSION,
                    affects_logic=True,
                )

        # Check if this is in attack-response path
        is_attack_path = any(
            re.search(p, combined, re.IGNORECASE)
            for p in self.ATTACK_PATH_PATTERNS.values()
        )

        # Check if fix is a RESTRAINT or RELAXATION
        is_restraint = any(
            re.search(p, combined, re.IGNORECASE)
            for p in self.RESTRAINT_PATTERNS.values()
        )
        is_relaxation = any(
            re.search(p, combined, re.IGNORECASE)
            for p in self.RELAXATION_PATTERNS.values()
        )

        # If in attack mode AND attack path AND restraint AND reversible → Tier 4
        if in_attack_mode and is_attack_path and is_restraint and not is_relaxation:
            return BugReport(
                file=file, line=line, bug_type=bug_type,
                description=description, suggested_fix=suggested_fix,
                tier=BugTier.TIER_4_ATTACK_MODE,
                is_restraint=True,
                is_reversible=True,
            )

        # If it's a relaxation → ALWAYS Tier 3 (never auto-relax security)
        # [TIER3-AUTO] HARD LIMIT: is_relaxation=True -> human-only, no override
        if is_relaxation:
            return BugReport(
                file=file, line=line, bug_type=bug_type,
                description=description, suggested_fix=suggested_fix,
                tier=BugTier.TIER_3_PERMISSION,
                affects_logic=True,
                is_relaxation=True,  # locked -- SCP_AUTO_APPROVE_TIER3 cannot bypass
            )

        # Check if behavior-affecting (Tier 2) vs pure implementation (Tier 1)
        behavior_patterns = [
            r"cache|retry|fallback|timeout|connection|pool|queue|buffer",
            r"error.*message|log.*format|stat.*counter",
        ]
        is_behavior = any(
            re.search(p, combined, re.IGNORECASE)
            for p in behavior_patterns
        )

        if is_behavior:
            return BugReport(
                file=file, line=line, bug_type=bug_type,
                description=description, suggested_fix=suggested_fix,
                tier=BugTier.TIER_2_AUTO_FIX_LOG,
            )

        # Default: Tier 1 (pure implementation bug, no logic impact)
        return BugReport(
            file=file, line=line, bug_type=bug_type,
            description=description, suggested_fix=suggested_fix,
            tier=BugTier.TIER_1_AUTO_FIX,
        )
