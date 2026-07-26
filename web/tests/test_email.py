"""Outbound mail (Resend). The failure this file exists for: the live app ran
for weeks with no outbound key at all, so every confirmation link was logged
instead of sent and the signups were lost — and when a key finally arrived, the
provider refused the send for a reason the log did not record. Sending must be
observable and must never raise."""

import logging

import pytest
import requests

from web.app.services import email


@pytest.fixture()
def no_post(monkeypatch):
    """Fails the test if anything reaches the network."""
    def _boom(*args, **kwargs):
        raise AssertionError("requests.post called")
    monkeypatch.setattr(email.requests, "post", _boom)


def test_without_key_logs_the_message(monkeypatch, caplog, no_post):
    monkeypatch.delenv("BUDGET_RESEND_TOKEN", raising=False)
    with caplog.at_level(logging.INFO, logger="email"):
        email.send_email("her@example.com", "Confirma", "https://x/verify?token=abc")
    assert "her@example.com" in caplog.text
    assert "https://x/verify?token=abc" in caplog.text


def test_with_key_posts_to_resend(monkeypatch):
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def _post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return _Resp()

    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_tok-123")
    monkeypatch.setenv("BUDGET_FROM_EMAIL", "no-reply@budget.example.do")
    monkeypatch.setattr(email.requests, "post", _post)

    email.send_email("her@example.com", "Confirma", "cuerpo")

    assert sent["url"] == "https://api.resend.com/emails"
    assert sent["headers"]["Authorization"] == "Bearer re_tok-123"
    assert sent["json"] == {
        "from": "no-reply@budget.example.do",
        "to": ["her@example.com"],
        "subject": "Confirma",
        "text": "cuerpo",
    }


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("no route to host"),
    requests.HTTPError("403 Forbidden: domain is not verified"),
])
def test_provider_failure_is_logged_not_raised(monkeypatch, caplog, failure):
    """The account row is already committed when we get here — raising would
    turn a successful signup into a 500 the caller cannot undo."""
    def _post(*args, **kwargs):
        raise failure

    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_tok-123")
    monkeypatch.setattr(email.requests, "post", _post)

    with caplog.at_level(logging.ERROR, logger="email"):
        email.send_email("her@example.com", "Confirma", "https://x/verify?token=abc")

    assert "her@example.com" in caplog.text
    # The link stays recoverable from the log when delivery fails.
    assert "https://x/verify?token=abc" in caplog.text


def test_provider_error_body_is_logged(monkeypatch, caplog):
    """The status alone is useless: an unverified domain, a rejected From and a
    rate limit all arrive as a 4xx, and they are fixed in different places."""
    class _Resp:
        text = ('{"statusCode":403,"name":"validation_error","message":'
                '"The cualtoapp.com domain is not verified."}')

        def raise_for_status(self):
            raise requests.HTTPError("403 Client Error", response=self)

    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_tok-123")
    monkeypatch.setattr(email.requests, "post", lambda *a, **k: _Resp())

    with caplog.at_level(logging.ERROR, logger="email"):
        email.send_email("her@example.com", "Confirma", "cuerpo")

    assert "validation_error" in caplog.text and "not verified" in caplog.text


def test_http_error_status_is_logged(monkeypatch, caplog):
    """raise_for_status is inside the guarded block, not only the request."""
    class _Resp:
        def raise_for_status(self):
            raise requests.HTTPError("401 Unauthorized")

    monkeypatch.setenv("BUDGET_RESEND_TOKEN", "re_bad-key")
    monkeypatch.setattr(email.requests, "post", lambda *a, **k: _Resp())

    with caplog.at_level(logging.ERROR, logger="email"):
        email.send_verification_email("her@example.com", "https://x/verify?token=abc")

    assert "401 Unauthorized" in caplog.text
