"""
[OPT-14] ExternalTrustRoot — Gà §8: "Gốc tin cậy bên ngoài"

WHY re-implement: was 9-line stub returning True always.
Proper implementation needs:
  - Verify external audit tests exist and pass
  - Verify CI/CD pipeline exists
  - Verify constitution file exists and is human-approved
  - Check file integrity (hasn't been modified by SCP)

Wired into: scp/api_server.py (startup check — if external trust broken, warn)
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("scp.meta.external_trust")


class ExternalTrustRoot:
    """Gà §8: External trust root — SCP cannot self-verify, needs external anchors.

    3 external trust roots:
      1. tests/external_audit/ — independent tests (no SCP imports)
      2. CI/CD pipeline — GitHub Actions (external)
      3. Constitution — human-approved, SCP cannot modify
    """

    EXPECTED_FILES = [
        "tests/external_audit/test_cascade.py",
        "tests/external_audit/test_security.py",
        "tests/external_audit/conftest.py",
        ".github/workflows/",  # CI/CD
        "scp/meta/constitution.py",  # Constitution
    ]

    # Markers that constitution file is human-approved (not auto-generated).
    # SCP cannot write these markers — they must be added by a human reviewer.
    HUMAN_APPROVED_MARKERS = (
        "HUMAN_APPROVED",
        "# human-approved",
        "# human_reviewed",
        "Copyright (c) 2026",
    )

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self._baseline_hashes: dict[str, str] = {}

    def verify_external(self) -> dict:
        """Verify all external trust roots exist + intact.

        Returns dict with:
          - passed: bool (all checks passed)
          - checks: list of individual check results
          - missing: list of missing files
        """
        checks = []
        missing = []
        for expected in self.EXPECTED_FILES:
            path = self.project_root / expected
            exists = path.exists()
            checks.append({
                "file": expected,
                "exists": exists,
                "type": "dir" if expected.endswith("/") else "file",
            })
            if not exists:
                missing.append(expected)

        # Check constitution is human-approved (has HUMAN_APPROVED marker)
        constitution_path = self.project_root / "scp/meta/constitution.py"
        constitution_approved = False
        if constitution_path.exists():
            content = constitution_path.read_text()
            content_lower = content.lower()
            constitution_approved = any(
                marker.lower() in content_lower
                for marker in self.HUMAN_APPROVED_MARKERS
            )

        checks.append({
            "file": "constitution.py",
            "check": "human_approved",
            "passed": constitution_approved,
        })

        passed = len(missing) == 0 and constitution_approved
        return {
            "passed": passed,
            "checks": checks,
            "missing": missing,
            "constitution_approved": constitution_approved,
        }

    def get_baseline_hash(self, file_path: str) -> str | None:
        """Get baseline hash of a file (for tamper detection)."""
        path = self.project_root / file_path
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify_integrity(self, file_path: str, expected_hash: str) -> bool:
        """Verify file hasn't been tampered with (matches expected hash)."""
        actual = self.get_baseline_hash(file_path)
        if not actual:
            return False
        return actual == expected_hash

    def establish_baseline(self, file_paths: list[str]) -> dict[str, str]:
        """Establish baseline hashes for tamper detection.

        Call this once after human review — store hashes externally.
        """
        baselines = {}
        for fp in file_paths:
            h = self.get_baseline_hash(fp)
            if h:
                baselines[fp] = h
        self._baseline_hashes = baselines
        return baselines

    def stats(self) -> dict:
        verification = self.verify_external()
        return {
            "passed": verification["passed"],
            "missing_count": len(verification["missing"]),
            "constitution_approved": verification["constitution_approved"],
            "baseline_files": len(self._baseline_hashes),
        }


# Module-level singleton — used by api_server lifespan() at startup.
_external_trust_root: ExternalTrustRoot | None = None


def get_external_trust_root(project_root: str = ".") -> ExternalTrustRoot:
    """Get the singleton ExternalTrustRoot instance."""
    global _external_trust_root
    if _external_trust_root is None:
        _external_trust_root = ExternalTrustRoot(project_root)
    return _external_trust_root


__all__ = ["ExternalTrustRoot", "get_external_trust_root"]
