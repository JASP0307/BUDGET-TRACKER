"""Gmail read-only access: OAuth + fetch candidate notification emails.

Scope is gmail.readonly, so we cannot mark messages with a label. Dedupe is
handled by the caller via message ids already recorded in the Sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Poller queries — see NOTES.md "Gmail queries for the poller".
QUERIES = [
    'from:notificaciones@popularenlinea.com subject:"Notificación de Consumo"',
    'from:notificaciones@popularenlinea.com subject:"Notificación de Retiro"',
    "from:notificaciones@qik.do subject:(Usaste OR reversó)",
]


@dataclass
class RawEmail:
    message_id: str
    sender: str
    subject: str
    html: str


def get_service(credentials_file: str, token_file: str):
    """Return an authorized Gmail service, running the OAuth consent flow the
    first time and caching the token afterwards."""
    creds = None
    from pathlib import Path

    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def fetch_candidates(service, fetch_window: str) -> list[RawEmail]:
    """Fetch recent notification emails across all poller queries."""
    emails: list[RawEmail] = []
    seen: set[str] = set()
    for query in QUERIES:
        full_query = f"{query} newer_than:{fetch_window}"
        resp = service.users().messages().list(userId="me", q=full_query).execute()
        for meta in resp.get("messages", []):
            mid = meta["id"]
            if mid in seen:
                continue
            seen.add(mid)
            emails.append(_load_email(service, mid))
    return emails


def _load_email(service, message_id: str) -> RawEmail:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return RawEmail(
        message_id=message_id,
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        html=_extract_html(msg["payload"]),
    )


def _extract_html(payload) -> str:
    import base64

    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        html = _extract_html(part)
        if html:
            return html
    return ""
