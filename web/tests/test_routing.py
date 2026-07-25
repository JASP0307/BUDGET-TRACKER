"""Inbound routing: which token a payload resolves to.

REGRESSION (2026-07-25): every **Gmail auto-forwarded** bank email was filed as
`unrecognized` and silently dropped. On forwarded mail the `To` header is the
bank addressing the *user* — the inbound address never appears in it — and the
router only looked at To/ToFull/MailboxHash. Live ingestion had therefore never
worked: all "processed" bank emails in production turned out to be replayed
fixtures, and the only two genuine Postmark deliveries both failed to route.

The header values below are the real shapes captured from those two messages,
with the mailbox local-part replaced.
"""

from sqlalchemy import select

TOKEN = "43d24f4a9ef2"
PMHASH = "32c2ad98da061df4750eb3c0bbf65e71"
INBOUND = f"{PMHASH}+u_{TOKEN}@inbound.postmarkapp.com"
# Gmail rewrites the envelope sender when forwarding; the destination (and so
# the token) survives inside it.
GMAIL_RETURN_PATH = f"<user+caf_={PMHASH}+u_{TOKEN}=inbound.postmarkapp.com@gmail.com>"


def _route(payload):
    from web.app.db import get_sessionmaker
    from web.app.services.ingest import _route as route
    with get_sessionmaker()() as s:
        return route(s, payload)


def _own_token(client):
    """The bootstrap user's real token, so tests route to a row that exists."""
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _forwarded_payload(token, **over):
    """A Gmail auto-forwarded bank email: To is the user's own address, and the
    inbound address appears only in the delivery headers."""
    payload = {
        "MessageID": "route-1",
        "From": "notificaciones@qik.do",
        "Subject": "Usaste tu tarjeta de crédito Qik",
        "To": "user@gmail.com",
        "ToFull": [{"Email": "user@gmail.com", "MailboxHash": ""}],
        "MailboxHash": "",
        "HtmlBody": "<p>x</p>",
        "Headers": [
            {"Name": "Delivered-To", "Value": "user@gmail.com"},
            {"Name": "X-Forwarded-To",
             "Value": f"{PMHASH}+u_{token}@inbound.postmarkapp.com"},
            {"Name": "Return-Path",
             "Value": GMAIL_RETURN_PATH.replace(TOKEN, token)},
        ],
    }
    payload.update(over)
    return payload


# ---- the regression ----

def test_gmail_forwarded_mail_routes_via_delivery_headers(client):
    """The exact failure: To is the user's own address, nothing else set."""
    token = _own_token(client)
    assert _route(_forwarded_payload(token)) is not None


def test_gmail_forwarded_mail_routes_from_return_path_alone(client):
    """Even with X-Forwarded-To stripped, the SRS Return-Path still carries it."""
    token = _own_token(client)
    payload = _forwarded_payload(token)
    payload["Headers"] = [h for h in payload["Headers"]
                          if h["Name"] != "X-Forwarded-To"]
    assert _route(payload) is not None


def test_original_recipient_is_preferred(client):
    """Postmark's envelope field is the cleanest source when present."""
    token = _own_token(client)
    payload = _forwarded_payload(token, Headers=[])
    payload["OriginalRecipient"] = f"{PMHASH}+u_{token}@inbound.postmarkapp.com"
    assert _route(payload) is not None


# ---- previously-working paths must keep working ----

def test_mailbox_hash_still_routes(client):
    token = _own_token(client)
    assert _route({"MailboxHash": f"u_{token}", "ToFull": []}) is not None


def test_custom_domain_to_still_routes(client):
    token = _own_token(client)
    assert _route({"ToFull": [{"Email": f"u_{token}@in.example.do"}]}) is not None


def test_postmark_plus_address_in_to_still_routes(client):
    token = _own_token(client)
    addr = f"{PMHASH}+u_{token}@inbound.postmarkapp.com"
    assert _route({"ToFull": [{"Email": addr, "MailboxHash": ""}]}) is not None


# ---- it must not become a free-for-all ----

def test_unknown_token_does_not_route(client):
    assert _route(_forwarded_payload("deadbeef99")) is None


def test_token_in_an_arbitrary_header_is_ignored(client):
    """Only delivery headers are honoured. A token in Subject or some random
    X- header must not steer mail into someone's account."""
    token = _own_token(client)
    payload = _forwarded_payload(token)
    payload["Headers"] = [
        {"Name": "X-Whatever", "Value": f"{PMHASH}+u_{token}@inbound.postmarkapp.com"},
        {"Name": "Subject", "Value": f"u_{token}@inbound.postmarkapp.com"},
    ]
    assert _route(payload) is None


def test_token_embedded_in_a_longer_word_is_not_matched(client):
    token = _own_token(client)
    payload = _forwarded_payload(token)
    payload["Headers"] = [
        {"Name": "X-Forwarded-To", "Value": f"xxxu_{token}yyy@example.com"},
    ]
    assert _route(payload) is None


# ---- end to end through the webhook ----

def test_forwarded_bank_email_becomes_a_transaction(client):
    """The whole point: a real forwarded purchase must land as a transaction."""
    from pathlib import Path

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail, Transaction

    fixtures = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"
    payload = _forwarded_payload(_own_token(client))
    payload["HtmlBody"] = (fixtures / "qik_purchase.html").read_text(encoding="utf-8")

    resp = client.post("/webhooks/postmark-inbound/testsecret", json=payload)
    assert resp.json() == {"status": "processed"}

    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is not None
        raw = s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "route-1"))
        assert raw.user_id is not None, "routed but not attributed to a user"
        assert raw.html_body == "", "body should be dropped once processed"
