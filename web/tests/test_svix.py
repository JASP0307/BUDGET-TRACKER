"""Svix signature verification — what stands between the inbound endpoint and
anyone who can guess a forward address."""

import base64
import hashlib
import hmac
import time

from web.app.services import svix

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _sign(msg_id: str, timestamp: str, body: bytes, secret: str = SECRET) -> str:
    key = base64.b64decode(secret.split("_", 1)[1])
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def test_valid_signature_passes():
    body = b'{"type":"email.received"}'
    ts = str(int(time.time()))
    assert svix.verify(SECRET, "msg_1", ts, _sign("msg_1", ts, body), body)


def test_tampered_body_fails():
    ts = str(int(time.time()))
    header = _sign("msg_1", ts, b'{"type":"email.received"}')
    assert not svix.verify(SECRET, "msg_1", ts, header, b'{"type":"evil"}')


def test_wrong_secret_fails():
    body = b"{}"
    ts = str(int(time.time()))
    other = "whsec_" + base64.b64encode(b"f" * 32).decode()
    assert not svix.verify(SECRET, "msg_1", ts, _sign("msg_1", ts, body, other), body)


def test_replay_outside_tolerance_fails():
    """A captured delivery must stop working once it is old."""
    body = b"{}"
    old = str(int(time.time()) - svix.TOLERANCE_SECONDS - 60)
    assert not svix.verify(SECRET, "msg_1", old, _sign("msg_1", old, body), body)


def test_id_is_part_of_the_signature():
    """Otherwise a signature could be lifted onto another message."""
    body = b"{}"
    ts = str(int(time.time()))
    assert not svix.verify(SECRET, "msg_2", ts, _sign("msg_1", ts, body), body)


def test_multiple_versions_accepted_during_rotation():
    body = b"{}"
    ts = str(int(time.time()))
    header = f"v1,AAAAstale {_sign('msg_1', ts, body)}"
    assert svix.verify(SECRET, "msg_1", ts, header, body)


def test_missing_headers_fail_closed():
    body = b"{}"
    ts = str(int(time.time()))
    good = _sign("msg_1", ts, body)
    assert not svix.verify(SECRET, "", ts, good, body)
    assert not svix.verify(SECRET, "msg_1", "", good, body)
    assert not svix.verify(SECRET, "msg_1", ts, "", body)
    assert not svix.verify("", "msg_1", ts, good, body)


def test_garbage_timestamp_and_secret_do_not_raise():
    body = b"{}"
    assert not svix.verify(SECRET, "msg_1", "not-a-number", "v1,x", body)
    assert not svix.verify("whsec_!!!notbase64!!!", "msg_1",
                           str(int(time.time())), "v1,x", body)
