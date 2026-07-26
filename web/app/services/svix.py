"""Svix webhook signature verification — the scheme Resend signs with.

Implemented here rather than pulled in as a dependency: it is one HMAC, and the
alternative is another package in the boot path of a service that already
refuses to start when its configuration is wrong.

The signed string is ``<id>.<timestamp>.<raw body>``, keyed by the base64 secret
that follows the ``whsec_`` prefix, HMAC-SHA256, base64-encoded. The signature
header holds space-separated ``<version>,<signature>`` pairs — more than one
while a secret is being rotated, so any valid ``v1`` entry is accepted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

# Svix's own default. Bounds how long an intercepted delivery stays replayable.
TOLERANCE_SECONDS = 5 * 60


def _key(secret: str) -> bytes:
    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    return base64.b64decode(raw)


def verify(secret: str, msg_id: str, timestamp: str, signature_header: str,
           body: bytes, *, now: float | None = None) -> bool:
    """True when `body` carries a valid, in-date signature for `secret`.

    `body` must be the bytes as received. Re-serialising the parsed JSON changes
    the signed content and every verification fails.
    """
    if not (secret and msg_id and timestamp and signature_header):
        return False
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > TOLERANCE_SECONDS:
        return False

    try:
        key = _key(secret)
    except (ValueError, IndexError, base64.binascii.Error):
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()).decode()

    for part in signature_header.split():
        version, _, candidate = part.partition(",")
        if version == "v1" and hmac.compare_digest(candidate, expected):
            return True
    return False
