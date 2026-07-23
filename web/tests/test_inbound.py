"""Inbound forward-to address formatting (custom domain vs Postmark inbound)."""


def test_default_custom_domain(monkeypatch):
    monkeypatch.setenv("BUDGET_INBOUND_DOMAIN", "in.example.do")
    monkeypatch.delenv("BUDGET_POSTMARK_INBOUND_ADDRESS", raising=False)
    from web.app.services.inbound import format_inbound_address
    assert format_inbound_address("abc123") == "u_abc123@in.example.do"


def test_postmark_mailbox_hash_form(monkeypatch):
    monkeypatch.setenv("BUDGET_POSTMARK_INBOUND_ADDRESS",
                       "srv9hash@inbound.postmarkapp.com")
    from web.app.services.inbound import format_inbound_address
    assert format_inbound_address("abc123") == "srv9hash+u_abc123@inbound.postmarkapp.com"
