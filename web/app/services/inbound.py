"""Build the per-user inbound (forward-to) address.

Two shapes, chosen by config:
- Custom domain (MX → Postmark): ``u_<token>@<inbound_domain>``
- Postmark default inbound: ``<pmhash>+u_<token>@inbound.postmarkapp.com`` — the
  token rides in the mailbox hash, which ``ingest._route`` reads back out.
"""

from __future__ import annotations

from ..settings import get_settings


def format_inbound_address(token: str) -> str:
    settings = get_settings()
    pm = settings.postmark_inbound_address
    if pm and "@" in pm:
        local, domain = pm.split("@", 1)
        return f"{local}+u_{token}@{domain}"
    return f"u_{token}@{settings.inbound_domain}"
