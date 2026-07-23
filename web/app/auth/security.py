"""Password hashing and email-verification tokens.

Passwords use Argon2id (argon2-cffi defaults). Verification tokens are signed,
timestamped, and URL-safe (itsdangerous) — no token rows in the DB; validity
and expiry are carried by the signature itself.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..settings import get_settings

_hasher = PasswordHasher()

_VERIFY_SALT = "email-verify"
# Verification links stay valid for 24h; after that the user requests a new one.
VERIFY_MAX_AGE = 24 * 60 * 60


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_VERIFY_SALT)


def make_verify_token(user_id) -> str:
    return _serializer().dumps(str(user_id))


def read_verify_token(token: str, max_age: int = VERIFY_MAX_AGE) -> str | None:
    """Return the user-id string a token was issued for, or None if the token
    is malformed, tampered with, or older than `max_age` seconds."""
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
