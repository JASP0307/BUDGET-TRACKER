"""Inbound reconciliation: catching up on mail no webhook ever delivered.

Resend stops retrying a dead endpoint after ~22.5 hours, so an outage longer
than that loses mail outright — and a missing transaction looks exactly like a
day without spending. The sweep exists to make that recoverable, so the
properties worth pinning are the ones that decide whether a gap gets closed:

* a fetch that fails leaves **no row**, or `handle_inbound`'s idempotency would
  hand the empty row back forever and the email would be lost while the sweep
  reported success (the same trap as the webhook's 503 branch);
* a provider we could not reach must never read as "nothing is missing";
* running twice ingests once, because the sweep runs hourly over mail it has
  already recovered.
"""

import pytest
from sqlalchemy import select

from web.app.services import inbound_resend, reconcile

pytestmark = pytest.mark.usefixtures("client")


def _sessionmaker():
    from web.app.db import get_sessionmaker
    return get_sessionmaker()


def _own_token():
    from web.app.models import InboundAddress
    with _sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _listed(email_id="re-1", created_at="2099-01-01T00:00:00.000Z"):
    """One entry as GET /emails/receiving returns it."""
    return {"id": email_id, "created_at": created_at,
            "from": "notificaciones@qik.do",
            "subject": "Usaste tu tarjeta de crédito Qik"}


def _detail(token, email_id="re-1"):
    """What GET /emails/receiving/<id> returns for a forwarded bank email.
    The Return-Path is Gmail's SRS form from the account's own mailbox, which
    is what ingest's forwarder trust gate requires."""
    return {
        "id": email_id,
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


def _stub(monkeypatch, *, listed, fetch=None):
    """Replace both provider calls. Records the ids actually fetched."""
    fetched = []

    def _fetch(email_id):
        fetched.append(email_id)
        if fetch is None:
            raise AssertionError(f"unexpected fetch of {email_id}")
        return fetch(email_id)

    monkeypatch.setattr(inbound_resend, "list_received", lambda **_: listed)
    monkeypatch.setattr(inbound_resend, "fetch_email", _fetch)
    return fetched


def _raw(email_id):
    from web.app.models import RawEmail
    with _sessionmaker()() as s:
        return s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == email_id))


# ---- the diff ----

def test_mail_we_already_hold_is_not_refetched(monkeypatch):
    """The cheap half of the sweep: an hourly run over a healthy inbox must cost
    one list call and nothing else."""
    token = _own_token()
    fetched = _stub(monkeypatch, listed=[_listed("re-1")],
                    fetch=lambda _id: _detail(token))
    with _sessionmaker()() as session:
        reconcile.sweep(session)          # first run recovers it
        fetched.clear()
        result = reconcile.sweep(session)  # second sees it as known

    assert result.missing == 0 and result.ingested == 0
    assert fetched == []


def test_missing_mail_is_ingested_and_routed(monkeypatch):
    token = _own_token()
    _stub(monkeypatch, listed=[_listed("re-gap")],
          fetch=lambda _id: _detail(token, "re-gap"))

    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert (result.listed, result.missing, result.ingested) == (1, 1, 1)
    raw = _raw("re-gap")
    assert raw is not None and raw.user_id is not None


def test_sweep_is_idempotent(monkeypatch):
    """It runs hourly over mail it already recovered; a second pass must not
    duplicate the transaction."""
    from web.app.models import RawEmail

    token = _own_token()
    _stub(monkeypatch, listed=[_listed("re-dup")],
          fetch=lambda _id: _detail(token, "re-dup"))

    with _sessionmaker()() as session:
        reconcile.sweep(session)
        reconcile.sweep(session)

    with _sessionmaker()() as s:
        rows = s.scalars(select(RawEmail).where(
            RawEmail.provider_message_id == "re-dup")).all()
    assert len(rows) == 1


def test_oldest_is_ingested_first(monkeypatch):
    """Recovered transactions should land in the order they really happened,
    not newest-first the way the provider lists them."""
    token = _own_token()
    order = []

    def _fetch(email_id):
        order.append(email_id)
        return _detail(token, email_id)

    monkeypatch.setattr(inbound_resend, "list_received", lambda **_: [
        _listed("re-new", "2026-08-06T22:00:00.000Z"),
        _listed("re-old", "2026-08-05T10:00:00.000Z"),
    ])
    monkeypatch.setattr(inbound_resend, "fetch_email", _fetch)

    with _sessionmaker()() as session:
        reconcile.sweep(session)

    assert order == ["re-old", "re-new"]


# ---- failure must stay recoverable ----

def test_unfetchable_email_leaves_no_row_and_is_retried(monkeypatch):
    """A half-built row would be returned unchanged by handle_inbound forever,
    so the email would be lost while looking processed."""
    token = _own_token()

    def _boom(_id):
        raise inbound_resend.ReceivedEmailUnavailable("resend down")

    _stub(monkeypatch, listed=[_listed("re-flaky")], fetch=_boom)
    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert (result.failed, result.ingested) == (1, 0)
    assert _raw("re-flaky") is None

    # The next sweep finds it still missing and recovers it.
    _stub(monkeypatch, listed=[_listed("re-flaky")],
          fetch=lambda _id: _detail(token, "re-flaky"))
    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert (result.failed, result.ingested) == (0, 1)
    assert _raw("re-flaky") is not None


def test_one_bad_email_does_not_strand_the_rest(monkeypatch):
    token = _own_token()

    def _fetch(email_id):
        if email_id == "re-bad":
            raise inbound_resend.ReceivedEmailUnavailable("gone")
        return _detail(token, email_id)

    _stub(monkeypatch, listed=[_listed("re-bad"), _listed("re-good")],
          fetch=_fetch)
    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert (result.ingested, result.failed) == (1, 1)
    assert _raw("re-good") is not None


def test_an_unreachable_provider_is_not_a_clean_run(monkeypatch):
    """Swallowing this would report an empty gap for every email on the pages
    we never saw — silence in exactly the case the sweep exists to catch."""
    def _boom(**_):
        raise inbound_resend.ReceivedEmailUnavailable("503")

    monkeypatch.setattr(inbound_resend, "list_received", _boom)
    with _sessionmaker()() as session:
        with pytest.raises(inbound_resend.ReceivedEmailUnavailable):
            reconcile.sweep(session)


def test_dry_run_writes_nothing(monkeypatch):
    _stub(monkeypatch, listed=[_listed("re-dry")])  # fetch would assert
    with _sessionmaker()() as session:
        result = reconcile.sweep(session, dry_run=True)

    assert result.missing == 1 and result.ingested == 0
    assert result.recovered  # still names what it would have taken
    assert _raw("re-dry") is None


# ---- the quiet-inbox alarm ----

def test_a_quiet_inbox_is_reported(monkeypatch):
    """The failure that hid the outage: the tunnel is up, the sweep finds
    nothing missing, and mail simply stopped arriving."""
    _stub(monkeypatch, listed=[_listed("re-old", "2026-01-01T00:00:00.000Z")],
          fetch=lambda _id: _detail(_own_token(), "re-old"))
    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert result.stale_for is not None
    assert result.worth_reporting


def test_recent_mail_is_not_stale(monkeypatch):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token = _own_token()
    _stub(monkeypatch, listed=[_listed("re-now", recent)],
          fetch=lambda _id: _detail(token, "re-now"))
    with _sessionmaker()() as session:
        result = reconcile.sweep(session)

    assert result.stale_for is None
    assert result.worth_reporting  # because it recovered one


def test_a_healthy_run_says_nothing(monkeypatch):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token = _own_token()
    _stub(monkeypatch, listed=[_listed("re-quiet", recent)],
          fetch=lambda _id: _detail(token, "re-quiet"))
    with _sessionmaker()() as session:
        reconcile.sweep(session)
        result = reconcile.sweep(session)

    assert not result.worth_reporting


# ---- pagination ----

def test_list_received_walks_the_cursor(monkeypatch):
    """No date filter exists on this endpoint, only an id cursor, so a gap older
    than one page is only reachable by paging."""
    pages = {
        None: [{"id": f"re-{i}"} for i in range(100)],
        "re-99": [{"id": "re-100"}],
    }
    asked = []

    class _R:
        def __init__(self, data): self._data = data
        def json(self): return {"data": self._data}
        def raise_for_status(self): pass

    def _get(url, params=None, headers=None, timeout=None):
        asked.append((params or {}).get("after"))
        return _R(pages[(params or {}).get("after")])

    monkeypatch.setattr(inbound_resend.requests, "get", _get)
    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_test")

    out = inbound_resend.list_received()

    assert asked == [None, "re-99"]
    assert len(out) == 101 and out[-1]["id"] == "re-100"


def test_list_received_raises_rather_than_truncating(monkeypatch):
    import requests as _requests

    def _get(url, params=None, headers=None, timeout=None):
        raise _requests.RequestException("connection reset")

    monkeypatch.setattr(inbound_resend.requests, "get", _get)
    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_test")

    with pytest.raises(inbound_resend.ReceivedEmailUnavailable):
        inbound_resend.list_received()
