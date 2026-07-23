"""Optional at-rest encryption for raw email bodies.

With BUDGET_FERNET_KEY set (production), bodies are Fernet-encrypted; without
it (local dev/tests) they pass through unchanged. Ciphertext is prefixed so
decrypt can tell which case it is reading.
"""

from __future__ import annotations

from .settings import get_settings

_PREFIX = "enc:"


def _fernet():
    key = get_settings().fernet_key
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key)


def encrypt(text: str) -> str:
    f = _fernet()
    if f is None:
        return text
    return _PREFIX + f.encrypt(text.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        raise RuntimeError("Encrypted body but BUDGET_FERNET_KEY is not set")
    return f.decrypt(stored[len(_PREFIX):].encode()).decode()
