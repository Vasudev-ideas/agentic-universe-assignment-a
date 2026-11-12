from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_key() -> str:
    """Return a URL-safe 32-byte key suitable for either secret."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _key(name: str) -> bytes:
    raw = os.environ.get(name)
    if not raw:
        raise RuntimeError(f"Missing required environment variable: {name}")
    try:
        value = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise RuntimeError(f"{name} must be URL-safe base64") from exc
    if len(value) != 32:
        raise RuntimeError(f"{name} must decode to exactly 32 bytes")
    return value


@dataclass(frozen=True)
class Settings:
    database: Path
    pii_key: bytes
    lookup_key: bytes

    @classmethod
    def from_env(cls, database: str | Path = "solution/ledger.sqlite3") -> "Settings":
        return cls(Path(database), _key("AU_PII_KEY"), _key("AU_LOOKUP_KEY"))


class PiiProtector:
    def __init__(self, pii_key: bytes, lookup_key: bytes):
        self._aes = AESGCM(pii_key)
        self._lookup_key = lookup_key

    def encrypt(self, value: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, value.encode("utf-8"), None)

    def decrypt(self, value: bytes) -> str:
        return self._aes.decrypt(value[:12], value[12:], None).decode("utf-8")

    def token(self, value: str) -> str:
        return hmac.new(self._lookup_key, value.encode("utf-8"), hashlib.sha256).hexdigest()
