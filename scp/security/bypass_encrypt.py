"""
SCP Bypass Encryptor — at-rest encryption for bypass patterns.

TẠI SAO module này tồn tại?
  data/bypasses/*.jsonl chứa attack patterns SCP đã detect. Nếu attacker
  exfil được files này → biết SCP đã detect gì → adapt attack. Encrypt at-rest
  = thêm 1 lớp defense-in-depth.

TẠI SAO Fernet (symmetric)?
  - Đơn giản, không cần key management phức tạp
  - Key derive từ SCP_ENCRYPTION_KEY env var (human-controlled)
  - Nếu không có env var → generate + persist to data/.encryption_key
    (chỉ hoạt động local, không production-ready)

TẠI SAO không encrypt ngay khi write?
  - Performance: encrypt only khi SCP_ENCRYPT_BYPASSES=1
  - Default OFF — opt-in, không break existing flow
  - Khi ON: every append_bypass() encrypts before write

Safety:
  - Key rotation support (rotate_key())
  - Thread-safe (Lock)
  - Backup before encrypt file
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("scp.security.bypass_encrypt")

# Lazy import cryptography (optional dependency)
_Fernet = None
def _get_fernet():
    global _Fernet
    if _Fernet is None:
        try:
            from cryptography.fernet import Fernet
            _Fernet = Fernet
        except ImportError:
            logger.warning("[bypass_encrypt] cryptography not installed — encryption DISABLED")
            _Fernet = False
    return _Fernet


class BypassEncryptor:
    """At-rest encryption for bypass patterns."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bypasses_dir = self.data_dir / "bypasses"
        self.bypasses_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.data_dir / ".encryption_key"
        self._lock = threading.Lock()
        self._key = self._load_or_generate_key()

    def _load_or_generate_key(self) -> bytes:
        """Load key from env var or file. Generate if neither exists."""
        # Env var takes priority
        env_key = os.environ.get("SCP_ENCRYPTION_KEY", "")
        if env_key:
            # Derive Fernet-compatible key from env var (must be 32 url-safe base64 bytes)
            import base64
            derived = base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest())
            logger.info("[bypass_encrypt] Key loaded from SCP_ENCRYPTION_KEY env var")
            return derived

        # Load from file
        if self.key_file.exists():
            key = self.key_file.read_bytes().strip()
            logger.info("[bypass_encrypt] Key loaded from file")
            return key

        # Generate new key
        Fernet = _get_fernet()
        if not Fernet:
            return b""  # encryption disabled
        key = Fernet.generate_key()
        try:
            self.key_file.write_bytes(key)
            self.key_file.chmod(0o600)  # owner-only read/write
            logger.warning(
                "[bypass_encrypt] Generated new key — saved to data/.encryption_key. "
                "Set SCP_ENCRYPTION_KEY env var for production."
            )
        except Exception as e:
            logger.warning(f"[bypass_encrypt] Could not persist key: {e}")
        return key

    def encrypt_bypass(self, bypass_dict: dict) -> bytes:
        """Encrypt a bypass dict → bytes."""
        Fernet = _get_fernet()
        if not Fernet or not self._key:
            # Encryption disabled — return plaintext JSON
            return json.dumps(bypass_dict, ensure_ascii=False).encode("utf-8")
        with self._lock:
            f = Fernet(self._key)
            plaintext = json.dumps(bypass_dict, ensure_ascii=False).encode("utf-8")
            return f.encrypt(plaintext)

    def decrypt_bypass(self, encrypted: bytes) -> dict:
        """Decrypt bytes → bypass dict."""
        Fernet = _get_fernet()
        if not Fernet or not self._key:
            # Encryption was disabled — parse as plaintext JSON
            try:
                return json.loads(encrypted.decode("utf-8"))
            except Exception as e:
                logger.warning(f"[bypass_encrypt] Decrypt (plaintext) failed: {e}")
                return {}
        with self._lock:
            f = Fernet(self._key)
            try:
                plaintext = f.decrypt(encrypted)
                return json.loads(plaintext.decode("utf-8"))
            except Exception as e:
                logger.warning(f"[bypass_encrypt] Decrypt failed: {e}")
                return {}

    def encrypt_file(self, filepath: Path) -> Path:
        """Encrypt a bypass JSONL file in-place. Returns backup path."""
        Fernet = _get_fernet()
        if not Fernet or not self._key:
            logger.debug(f"[bypass_encrypt] Encryption disabled — skip {filepath}")
            return filepath

        if not filepath.exists():
            return filepath

        # Backup
        bak_path = filepath.with_suffix(filepath.suffix + ".bak")
        bak_path.write_bytes(filepath.read_bytes())

        # Encrypt each line
        with self._lock:
            f = Fernet(self._key)
            lines = filepath.read_text(encoding="utf-8").splitlines()
            encrypted_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    enc = f.encrypt(line.encode("utf-8"))
                    encrypted_lines.append(enc.decode("utf-8"))
                except Exception as e:
                    logger.warning(f"[bypass_encrypt] Line encrypt failed: {e}")
            filepath.write_text("\n".join(encrypted_lines) + "\n", encoding="utf-8")
            logger.info(f"[bypass_encrypt] Encrypted {len(encrypted_lines)} lines in {filepath.name}")
        return bak_path

    def decrypt_file(self, filepath: Path) -> dict:
        """Decrypt a bypass JSONL file → aggregated dict (for audit)."""
        Fernet = _get_fernet()
        if not filepath.exists():
            return {"error": "file not found"}

        with self._lock:
            if not Fernet or not self._key:
                # Plaintext
                lines = filepath.read_text(encoding="utf-8").splitlines()
                return {"count": len([line for line in lines if line.strip()]), "encrypted": False}

            f = Fernet(self._key)
            lines = filepath.read_text(encoding="utf-8").splitlines()
            decrypted = 0
            failed = 0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    f.decrypt(line.encode("utf-8"))
                    decrypted += 1
                except Exception:
                    failed += 1
            return {"count": decrypted, "failed": failed, "encrypted": True}

    def rotate_key(self) -> None:
        """Generate new key + re-encrypt all bypass files."""
        Fernet = _get_fernet()
        if not Fernet:
            return

        old_key = self._key
        # Decrypt all files with old key, then re-encrypt with new key
        new_key = Fernet.generate_key()
        bypass_files = list(self.bypasses_dir.glob("*.jsonl"))

        with self._lock:
            old_fernet = Fernet(old_key)
            new_fernet = Fernet(new_key)
            for bf in bypass_files:
                try:
                    lines = bf.read_text(encoding="utf-8").splitlines()
                    reencrypted = []
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            plaintext = old_fernet.decrypt(line.encode("utf-8"))
                            new_enc = new_fernet.encrypt(plaintext)
                            reencrypted.append(new_enc.decode("utf-8"))
                        except Exception:
                            reencrypted.append(line)  # keep as-is if decrypt fails
                    bf.write_text("\n".join(reencrypted) + "\n", encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[bypass_encrypt] Rotate failed for {bf.name}: {e}")

            # Save new key
            self._key = new_key
            self.key_file.write_bytes(new_key)
            self.key_file.chmod(0o600)
            logger.info(f"[bypass_encrypt] Key rotated — re-encrypted {len(bypass_files)} files")
