"""Resend inbound: the adapter, and the webhook that drives it.

Resend's webhook is metadata only — no body, no headers, no attachment content —
so a message has to be reassembled with a second call before it can be ingested.
Two properties matter more than the mapping itself:

* a failed fetch must leave **no row**, or `handle_inbound`'s idempotency would
  hand back the empty row on every retry and lose the email while reporting
  success (the 2026-07-25 routing bug in a new costume);
* the delivery headers must survive the translation, because on Gmail
  auto-forwarded mail they are the only place the destination appears.
"""

import base64
import hashlib
import hmac
import time

import pytest
import requests
from sqlalchemy import select

from web.app.services import inbound_resend

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _own_token(client):
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _detail(token, **over):
    """What GET /emails/receiving/<id> returns for a forwarded bank email."""
    detail = {
        "id": "re-abc-123",
        "from": "notificaciones@qik.do",
        "to": ["user@gmail.com"],
        "subject": "Usaste tu tarjeta de crédito Qik",
        "html": "<p>x</p>",
        "text": "",
        "headers": {
            "Delivered-To": "user@gmail.com",
            "X-Forwarded-To": f"u_{token}@in.example.do",
            "Return-Path": f"<user+caf_=u_{token}=in.example.do@gmail.com>",
        },
    }
    detail.update(over)
    return detail


def _post(client, event, *, secret="testsecret", body_secret=SECRET):
    """Deliver a signed webhook the way Resend does."""
    import json
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    key = base64.b64decode(body_secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(
        key, f"msg_1.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return client.post(
        f"/webhooks/resend-inbound/{secret}", content=body,
        headers={"content-type": "application/json", "svix-id": "msg_1",
                 "svix-timestamp": ts, "svix-signature": f"v1,{sig}"})


# ---- the adapter ----

def test_delivery_headers_survive_translation():
    """The whole point: `_route` reads these, and forwarded mail has nothing else."""
    payload = inbound_resend.to_postmark_payload(_detail("abc123def456"))
    names = {h["Name"]: h["Value"] for h in payload["Headers"]}
    assert names["X-Forwarded-To"] == "u_abc123def456@in.example.do"
    assert "Return-Path" in names and "Delivered-To" in names


def test_lowercase_delivery_headers_still_route(client, monkeypatch):
    """REGRESSION (2026-07-26, caught on the first real Resend delivery):
    Resend lower-cases header names where Postmark title-cases them, and the
    routing allowlist was matched literally. Auto-forwarded mail — whose `to`
    is the bank's addressee, so headers are the only clue — would have routed
    nowhere, silently, exactly like the Postmark bug a day earlier."""
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    token = _own_token(client)
    detail = _detail(token, to=["user@gmail.com"], headers={
        "delivered-to": "user@gmail.com",
        "x-forwarded-to": f"u_{token}@cualtoapp.com",
        "return-path": "notificaciones@qik.do",
    })
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(inbound_resend, "fetch_email", lambda _id: detail)

    assert _post(client, {"type": "email.received",
                          "data": {"email_id": "re-lower"}}).status_code == 200
    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "re-abc-123"))
        assert raw is not None and raw.user_id is not None


def test_repeated_header_becomes_one_entry_per_value():
    """`received` really does arrive as a list. A bare .strip() on it aborted
    routing for the whole message."""
    detail = _detail("abc123def456", headers={
        "received": ["from mail-vs1-f52.google.com ...", "by inbound-smtp ..."],
        "x-forwarded-to": "u_abc123def456@cualtoapp.com",
    })
    payload = inbound_resend.to_postmark_payload(detail)
    received = [h for h in payload["Headers"] if h["Name"] == "received"]
    assert len(received) == 2
    assert all(isinstance(h["Value"], str) for h in payload["Headers"])


def test_headers_accepted_as_a_list_of_pairs():
    """Resend has used both shapes; guessing wrong would silently break routing."""
    detail = _detail("abc123def456",
                     headers=[{"name": "X-Forwarded-To", "value": "u_tok@in.x"}])
    payload = inbound_resend.to_postmark_payload(detail)
    assert payload["Headers"] == [{"Name": "X-Forwarded-To", "Value": "u_tok@in.x"}]


def test_body_and_envelope_are_mapped():
    payload = inbound_resend.to_postmark_payload(
        _detail("abc123def456", envelope_to="u_abc123def456@in.example.do"))
    assert payload["MessageID"] == "re-abc-123"
    assert payload["From"] == "notificaciones@qik.do"
    assert payload["HtmlBody"] == "<p>x</p>"
    assert payload["OriginalRecipient"] == "u_abc123def456@in.example.do"
    assert payload["ToFull"] == [{"Email": "user@gmail.com", "MailboxHash": ""}]


def test_envelope_is_not_invented_from_the_header():
    """Claiming the To header is the envelope would re-introduce the bug where
    forwarded mail routes to whoever the bank addressed."""
    payload = inbound_resend.to_postmark_payload(_detail("abc123def456"))
    assert payload["OriginalRecipient"] == ""


def test_only_rfc822_attachments_are_downloaded(monkeypatch):
    downloaded = []

    def _get(url, timeout=None):
        downloaded.append(url)
        class _R:
            content = b"From: bank\r\nSubject: s\r\n\r\nbody"
            def raise_for_status(self): pass
        return _R()

    monkeypatch.setattr(inbound_resend.requests, "get", _get)
    detail = _detail("abc123def456", attachments=[
        {"filename": "orig.eml", "content_type": "message/rfc822",
         "download_url": "https://x/eml"},
        {"filename": "estado.pdf", "content_type": "application/pdf",
         "download_url": "https://x/pdf"},
    ])
    payload = inbound_resend.to_postmark_payload(detail)

    assert downloaded == ["https://x/eml"]  # the PDF costs no bandwidth
    assert base64.b64decode(payload["Attachments"][0]["Content"]).startswith(b"From:")
    assert payload["Attachments"][1]["Content"] == ""


class _Resp:
    """Enough of a requests.Response for the adapter's two uses of it."""

    def __init__(self, payload=None, content=b""):
        self._payload, self.content = payload, content

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _fake_transport(monkeypatch, *, listed, single=None, eml=b"From: bank\r\n\r\nx"):
    """Stand in for the attachment endpoints. Records the URLs asked for."""
    seen = []

    def _get(url, headers=None, timeout=None):
        seen.append(url)
        if url.endswith("/attachments"):
            return _Resp({"object": "list", "has_more": bool(single), "data": listed})
        if "/attachments/" in url:
            return _Resp(single)
        return _Resp(content=eml)

    monkeypatch.setattr(inbound_resend.requests, "get", _get)
    return seen


def test_attachment_urls_are_resolved_when_the_email_omits_them(monkeypatch):
    """REGRESSION (2026-07-26, first real backfill through Resend): a fetched
    email lists its attachments with an id and a size but **no URL** — unlike
    Postmark, which inlines the content. Reading `download_url` off the entry
    yielded empty .eml files, and an empty forwarded email is indistinguishable
    from one sent by a stranger, so a 12-email backfill reported "0 transactions
    (12 skipped)" and looked like an ordinary rejection."""
    seen = _fake_transport(monkeypatch, listed=[
        {"id": "att-1", "download_url": "https://cdn/eml-1"}])
    detail = _detail("abc123def456", attachments=[
        {"id": "att-1", "filename": "Consumo.eml", "content_type": "message/rfc822",
         "size": 10936}])

    payload = inbound_resend.to_postmark_payload(detail)

    assert seen[0] == "https://api.resend.com/emails/receiving/re-abc-123/attachments"
    assert "https://cdn/eml-1" in seen
    assert base64.b64decode(payload["Attachments"][0]["Content"]).startswith(b"From:")


def test_attachment_missing_from_the_listing_is_fetched_by_id(monkeypatch):
    """The listing is paginated (`has_more`), so it is not proof of absence."""
    seen = _fake_transport(
        monkeypatch, listed=[{"id": "att-1", "download_url": "https://cdn/eml-1"}],
        single={"id": "att-2", "download_url": "https://cdn/eml-2"})
    detail = _detail("abc123def456", attachments=[
        {"id": "att-1", "filename": "a.eml", "content_type": "message/rfc822"},
        {"id": "att-2", "filename": "b.eml", "content_type": "message/rfc822"}])

    payload = inbound_resend.to_postmark_payload(detail)

    assert ("https://api.resend.com/emails/receiving/re-abc-123/attachments/att-2"
            in seen)
    assert all(a["Content"] for a in payload["Attachments"])


def test_pdf_costs_no_attachment_lookup(monkeypatch):
    """A statement attached to a forward must not trigger the URL dance."""
    seen = _fake_transport(monkeypatch, listed=[])
    payload = inbound_resend.to_postmark_payload(_detail("abc123def456", attachments=[
        {"id": "att-1", "filename": "estado.pdf", "content_type": "application/pdf"}]))
    assert seen == [] and payload["Attachments"][0]["Content"] == ""


def test_unresolvable_attachment_leaves_content_empty(monkeypatch):
    """Resend answering nothing useful must not raise: the rest of the batch
    still deserves to be backfilled."""
    _fake_transport(monkeypatch, listed=[], single={})
    payload = inbound_resend.to_postmark_payload(_detail("abc123def456", attachments=[
        {"id": "att-1", "filename": "a.eml", "content_type": "message/rfc822"}]))
    assert payload["Attachments"][0]["Content"] == ""


def test_failed_attachment_download_does_not_fail_the_message(monkeypatch):
    """One bad attachment must not cost us the whole delivery."""
    def _boom(url, timeout=None):
        raise requests.ConnectionError("nope")
    monkeypatch.setattr(inbound_resend.requests, "get", _boom)
    payload = inbound_resend.to_postmark_payload(_detail("abc123def456", attachments=[
        {"filename": "o.eml", "content_type": "message/rfc822",
         "download_url": "https://x/eml"}]))
    assert payload["Attachments"][0]["Content"] == ""


def test_fetch_failure_raises_rather_than_returning_a_shell(monkeypatch):
    def _boom(url, headers=None, timeout=None):
        raise requests.ConnectionError("resend down")
    monkeypatch.setattr(inbound_resend.requests, "get", _boom)
    with pytest.raises(inbound_resend.ReceivedEmailUnavailable):
        inbound_resend.payload_from_event({"data": {"email_id": "re-1"}})


def test_event_without_an_id_raises():
    with pytest.raises(inbound_resend.ReceivedEmailUnavailable):
        inbound_resend.payload_from_event({"data": {}})


# ---- the webhook ----

def test_route_is_off_without_a_signing_secret(client, monkeypatch):
    monkeypatch.delenv("BUDGET_RESEND_WEBHOOK_SECRET", raising=False)
    assert _post(client, {"type": "email.received"}).status_code == 404


def test_bad_signature_is_rejected(client, monkeypatch):
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    other = "whsec_" + base64.b64encode(b"f" * 32).decode()
    r = _post(client, {"type": "email.received", "data": {"email_id": "x"}},
              body_secret=other)
    assert r.status_code == 401


def test_wrong_path_secret_is_rejected(client, monkeypatch):
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    r = _post(client, {"type": "email.received"}, secret="nope")
    assert r.status_code == 404


def test_non_received_events_are_ignored(client, monkeypatch):
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    r = _post(client, {"type": "email.delivered", "data": {"email_id": "x"}})
    assert r.status_code == 200 and r.json()["status"] == "ignored"


def test_fetch_failure_asks_for_redelivery_and_stores_nothing(client, monkeypatch):
    """The important one. A row written now would be handed back unchanged on
    every retry, and the email would be lost while the endpoint reports success."""
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(inbound_resend, "fetch_email",
                        lambda _id: (_ for _ in ()).throw(
                            inbound_resend.ReceivedEmailUnavailable("down")))

    r = _post(client, {"type": "email.received", "data": {"email_id": "re-1"}})
    assert r.status_code == 503

    with get_sessionmaker()() as s:
        assert s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "re-1")) is None


def test_forwarded_bank_email_routes_and_ingests(client, monkeypatch):
    """End to end through the real pipeline: signed webhook → fetch → adapter →
    handle_inbound, routed only by the delivery headers."""
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    token = _own_token(client)
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(inbound_resend, "fetch_email", lambda _id: _detail(token))

    r = _post(client, {"type": "email.received", "data": {"email_id": "re-abc-123"}})
    assert r.status_code == 200

    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "re-abc-123"))
        assert raw is not None
        assert raw.user_id is not None  # it routed
        assert raw.processing_status != "unrecognized"


def test_redelivery_of_the_same_email_is_idempotent(client, monkeypatch):
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    token = _own_token(client)
    monkeypatch.setenv("BUDGET_RESEND_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(inbound_resend, "fetch_email", lambda _id: _detail(token))
    event = {"type": "email.received", "data": {"email_id": "re-abc-123"}}

    assert _post(client, event).status_code == 200
    assert _post(client, event).status_code == 200

    with get_sessionmaker()() as s:
        rows = s.scalars(select(RawEmail).where(
            RawEmail.provider_message_id == "re-abc-123")).all()
        assert len(rows) == 1
