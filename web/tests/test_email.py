"""Outbound mail. The failure this file exists for: the live app ran for weeks
with no Postmark token, so every confirmation link was logged instead of sent
and the signups were lost. Sending must be observable and must never raise."""

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


def test_without_token_logs_the_message(monkeypatch, caplog, no_post):
    monkeypatch.delenv("BUDGET_POSTMARK_TOKEN", raising=False)
    with caplog.at_level(logging.INFO, logger="email"):
        email.send_email("her@example.com", "Confirma", "https://x/verify?token=abc")
    assert "her@example.com" in caplog.text
    assert "https://x/verify?token=abc" in caplog.text


def test_with_token_posts_to_postmark(monkeypatch):
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def _post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return _Resp()

    monkeypatch.setenv("BUDGET_POSTMARK_TOKEN", "tok-123")
    monkeypatch.setenv("BUDGET_FROM_EMAIL", "no-reply@budget.example.do")
    monkeypatch.setattr(email.requests, "post", _post)

    email.send_email("her@example.com", "Confirma", "cuerpo")

    assert sent["url"] == "https://api.postmarkapp.com/email"
    assert sent["headers"]["X-Postmark-Server-Token"] == "tok-123"
    assert sent["json"] == {
        "From": "no-reply@budget.example.do",
        "To": "her@example.com",
        "Subject": "Confirma",
        "TextBody": "cuerpo",
        "MessageStream": "outbound",
    }


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("no route to host"),
    requests.HTTPError("422 Unprocessable Entity: sender signature not confirmed"),
])
def test_provider_failure_is_logged_not_raised(monkeypatch, caplog, failure):
    """The account row is already committed when we get here — raising would
    turn a successful signup into a 500 the caller cannot undo."""
    def _post(*args, **kwargs):
        raise failure

    monkeypatch.setenv("BUDGET_POSTMARK_TOKEN", "tok-123")
    monkeypatch.setattr(email.requests, "post", _post)

    with caplog.at_level(logging.ERROR, logger="email"):
        email.send_email("her@example.com", "Confirma", "https://x/verify?token=abc")

    assert "her@example.com" in caplog.text
    # The link stays recoverable from the log when delivery fails.
    assert "https://x/verify?token=abc" in caplog.text


def test_http_error_status_is_logged(monkeypatch, caplog):
    """raise_for_status is inside the guarded block, not only the request."""
    class _Resp:
        def raise_for_status(self):
            raise requests.HTTPError("401 Unauthorized")

    monkeypatch.setenv("BUDGET_POSTMARK_TOKEN", "bad-token")
    monkeypatch.setattr(email.requests, "post", lambda *a, **k: _Resp())

    with caplog.at_level(logging.ERROR, logger="email"):
        email.send_verification_email("her@example.com", "https://x/verify?token=abc")

    assert "401 Unauthorized" in caplog.text
