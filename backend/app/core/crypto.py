"""Symmetric encryption for secrets at rest (Delta API keys/secrets).

Ciphertext is tagged with a version prefix ("enc:v1:") so decrypt() can tell
an encrypted value from a legacy plaintext one and migrate transparently:
  - encrypt(x)  -> "enc:v1:<fernet-token>"
  - decrypt(x)  -> plaintext if x is tagged; returns x unchanged otherwise
This lets existing plaintext rows keep working until they're re-saved/backfilled.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    """Build the Fernet cipher from ENCRYPTION_KEY, or derive one deterministically
    from SUPABASE_SERVICE_KEY so the app works without extra configuration."""
    raw = settings.ENCRYPTION_KEY or settings.SUPABASE_SERVICE_KEY or "mirror-engine-dev-key"
    # Derive a 32-byte urlsafe-base64 key (Fernet requirement) from any string.
    digest = hashlib.sha256(raw.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret. Idempotent — already-encrypted values pass through."""
    if plaintext is None or plaintext == "":
        return plaintext
    if is_encrypted(plaintext):
        return plaintext
    token = _fernet().encrypt(plaintext.encode()).decode()
    return f"{_PREFIX}{token}"


def decrypt(value: str) -> str:
    """Decrypt a secret. Legacy plaintext (no prefix) is returned unchanged."""
    if value is None or value == "" or not is_encrypted(value):
        return value
    token = value[len(_PREFIX):]
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt a secret (wrong ENCRYPTION_KEY?). Returning as-is.")
        return value
