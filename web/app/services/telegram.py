"""Low-level Telegram: send messages, discover the bot, build the account-link
deep link, and sign the link token that ties a `/start` back to a user.

Sending is best-effort and never raises — callers (transaction alerts, the
link-confirmation reply, the test button) treat delivery as advisory.
"""

from __future__ import annotations

import logging

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..settings import get_settings

log = logging.getLogger("telegram")

_API = "https://api.telegram.org/bot{token}/{method}"
_LINK_SALT = "telegram-link"
# A link deep-link is meant to be used immediately; keep the window short.
LINK_MAX_AGE = 15 * 60

_bot_username: str | None = None


def send_message(chat_id: str, text: str) -> bool:
    """Send `text` to `chat_id`. Returns False (and logs) on any failure."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        return False
    try:
        resp = requests.post(
            _API.format(token=settings.telegram_bot_token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        log.exception("telegram sendMessage failed")
        return False


def get_bot_username() -> str | None:
    """The bot's @username, from config or (once) the getMe API. None when no
    bot token is configured."""
    global _bot_username
    settings = get_settings()
    if settings.telegram_bot_username:
        return settings.telegram_bot_username
    if not settings.telegram_bot_token:
        return None
    if _bot_username is None:
        try:
            resp = requests.get(
                _API.format(token=settings.telegram_bot_token, method="getMe"),
                timeout=10)
            resp.raise_for_status()
            _bot_username = resp.json()["result"]["username"]
        except (requests.RequestException, KeyError, ValueError):
            log.exception("telegram getMe failed")
            return None
    return _bot_username


def deep_link(token: str) -> str | None:
    username = get_bot_username()
    if username is None:
        return None
    return f"https://t.me/{username}?start={token}"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_LINK_SALT)


def make_link_token(user_id) -> str:
    return _serializer().dumps(str(user_id))


def read_link_token(token: str, max_age: int = LINK_MAX_AGE) -> str | None:
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
