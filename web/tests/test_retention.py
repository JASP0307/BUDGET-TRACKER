"""Retention of raw email bodies: dropped as soon as they're not needed, and
swept on a timer for the ones that are.

The body of a bank notification is the most sensitive thing the system touches,
so "we delete it once we've read it" has to be enforced, not just documented.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"


def _payload(message_id, token, sender="notificaciones@qik.do",
             subject="Usaste tu tarjeta de crédito Qik",
             fixture="qik_purchase.html", html=None):
    return {
        "MessageID": message_id,
        "From": sender,
        "Subject": subject,
        "ToFull": [{"Email": f"u_{token}@in.example.do"}],
        "HtmlBody": html if html is not None
                    else (FIXTURES / fixture).read_text(encoding="utf-8"),
        "Headers": [],
    }


def _token():
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _post(client, payload):
    return client.post("/webhooks/postmark-inbound/testsecret", json=payload)


def _raw(message_id):
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        return s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == message_id))


def test_processed_email_body_is_discarded(client):
    resp = _post(client, _payload("r-ok", _token()))
    assert resp.json() == {"status": "processed"}
    raw = _raw("r-ok")
    assert raw.html_body == ""
    # The transaction it produced survives — that's the point of dropping it.
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is not None


def test_skipped_email_body_is_discarded(client):
    """Mail from an unknown sender is skipped — and definitely not archived."""
    resp = _post(client, _payload("r-skip", _token(), sender="spam@evil.example",
                                 html="<p>nada</p>"))
    assert resp.json() == {"status": "skipped"}
    assert _raw("r-skip").html_body == ""


def test_unparseable_email_keeps_its_body_for_debugging(client):
    """The one case we do retain: a bank email whose format we can't read yet."""
    resp = _post(client, _payload("r-unrec", "nosuchtoken", html="<p>hola</p>"))
    assert resp.json() == {"status": "unrecognized"}
    assert _raw("r-unrec").html_body == "<p>hola</p>"


def test_gmail_confirmation_keeps_its_link(client):
    """The user still needs this link to finish onboarding."""
    body = ('<a href="https://mail.google.com/mail/vf-ABC123">confirmar</a>')
    resp = _post(client, _payload("r-conf", _token(),
                                  sender="forwarding-noreply@google.com",
                                  subject="Confirmación de reenvío", html=body))
    assert resp.json() == {"status": "confirmation"}
    assert _raw("r-conf").html_body == body


# ---- the daily sweep ----

def _age(message_id, days):
    """Backdate a row so the purge script's cutoffs apply to it."""
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == message_id))
        raw.received_at = datetime.now(timezone.utc) - timedelta(days=days)
        s.commit()


def _purge(**kwargs):
    from scripts.purge_raw_emails import purge
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        return purge(s, **kwargs)


def test_purge_blanks_old_confirmation_bodies(client):
    body = '<a href="https://mail.google.com/mail/vf-XYZ">confirmar</a>'
    _post(client, _payload("p-conf", _token(),
                           sender="forwarding-noreply@google.com",
                           subject="Confirmación", html=body))
    _age("p-conf", days=8)

    counts = _purge()
    assert counts["confirmation_bodies"] == 1
    assert _raw("p-conf").html_body == ""


def test_purge_leaves_recent_confirmation_bodies_alone(client):
    body = '<a href="https://mail.google.com/mail/vf-XYZ">confirmar</a>'
    _post(client, _payload("p-fresh", _token(),
                           sender="forwarding-noreply@google.com",
                           subject="Confirmación", html=body))
    _age("p-fresh", days=2)

    assert _purge()["confirmation_bodies"] == 0
    assert _raw("p-fresh").html_body == body


def test_purge_blanks_problem_bodies_after_thirty_days(client):
    _post(client, _payload("p-unrec", "nosuchtoken", html="<p>formato nuevo</p>"))
    _age("p-unrec", days=31)

    counts = _purge()
    # 30 days is also the row-deletion cutoff, so an unreferenced row goes
    # entirely — body and all.
    assert counts["problem_bodies"] == 1
    assert counts["rows_deleted"] == 1
    assert _raw("p-unrec") is None


def test_purge_keeps_rows_a_transaction_still_points_at(client):
    """Deleting these would strip a user's transaction of its provenance."""
    _post(client, _payload("p-keep", _token()))
    _age("p-keep", days=40)

    assert _purge()["rows_deleted"] == 0
    assert _raw("p-keep") is not None


def test_purge_dry_run_writes_nothing(client):
    body = '<a href="https://mail.google.com/mail/vf-XYZ">confirmar</a>'
    _post(client, _payload("p-dry", _token(),
                           sender="forwarding-noreply@google.com",
                           subject="Confirmación", html=body))
    _age("p-dry", days=8)

    assert _purge(dry_run=True)["confirmation_bodies"] == 1
    assert _raw("p-dry").html_body == body  # unchanged on disk
