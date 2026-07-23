"""Password hashing and signed one-shot tokens (email verification, password
reset).

Passwords use Argon2id (argon2-cffi defaults). Tokens are signed, timestamped,
and URL-safe (itsdangerous) — no token rows in the DB; validity and expiry are
carried by the signature itself. Reset tokens additionally embed a fingerprint
of the current password hash, so a token stops working the moment the password
changes: that makes reset links effectively single-use and revokes every
outstanding link on any password change.
"""

from __future__ import annotations

import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..settings import get_settings

_hasher = PasswordHasher()

_VERIFY_SALT = "email-verify"
_RESET_SALT = "password-reset"
# Verification links stay valid for 24h; reset links for 1h.
VERIFY_MAX_AGE = 24 * 60 * 60
RESET_MAX_AGE = 60 * 60


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_fingerprint(hashed_password: str) -> str:
    """Short, stable digest of a password hash; changes when the password does."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:16]


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=salt)


def make_verify_token(user_id) -> str:
    return _serializer(_VERIFY_SALT).dumps(str(user_id))


def read_verify_token(token: str, max_age: int = VERIFY_MAX_AGE) -> str | None:
    """Return the user-id string a token was issued for, or None if the token
    is malformed, tampered with, or older than `max_age` seconds."""
    try:
        return _serializer(_VERIFY_SALT).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def make_reset_token(user_id, hashed_password: str) -> str:
    return _serializer(_RESET_SALT).dumps(
        [str(user_id), password_fingerprint(hashed_password)])


def read_reset_token(token: str, max_age: int = RESET_MAX_AGE) -> tuple[str, str] | None:
    """Return (user_id, password_fingerprint) or None. The caller must still
    check the fingerprint against the user's *current* hash."""
    try:
        data = _serializer(_RESET_SALT).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, list) or len(data) != 2:
        return None
    return data[0], data[1]
