"""Resend inbound → the Postmark-shaped payload ``ingest.handle_inbound`` reads.

Postmark's inbound webhook is self-contained: one POST carries the body, the
headers and the attachments. Resend's carries **metadata only** — the body and
headers need a second authenticated call, and attachment *content* a third,
since not even the fetched email names a URL for it. So this
module reassembles a message before ingestion, and translates it into the shape
the existing pipeline already understands. Nothing in ``ingest`` changes; the
routing rules, the bank registry, dedupe and the retention behaviour are shared
by both providers, which is the point.

Two failures are worth naming, because both have precedent in this project:

* **A fetch that fails must not produce a row.** ``handle_inbound`` is idempotent
  on the message id, so a half-built row would be returned unchanged on every
  retry and the email would be lost while looking processed. We raise instead,
  and the caller answers 5xx so Resend redelivers.
* **Routing must keep reading the delivery headers.** On Gmail auto-forwarded
  mail the ``To`` header names the bank, not us; the real destination only
  appears in ``X-Forwarded-To`` / ``Return-Path``. Those live in the fetched
  headers, so they are mapped into the payload verbatim — see ``ingest._route``.
"""

from __future__ import annotations

import base64
import logging

import requests

from ..settings import get_settings

log = logging.getLogger("inbound.resend")

_API = "https://api.resend.com"
_TIMEOUT = 20
_MAX_PAGE = 100  # the received-emails endpoint's documented ceiling

# Where Resend records the address a message was actually delivered to. The
# webhook's `to` is the header, which forwarded mail fills with the bank's
# addressee — see the module docstring.
_ENVELOPE_KEYS = ("envelope_to", "recipient", "original_recipient")


class ReceivedEmailUnavailable(RuntimeError):
    """Resend accepted the message but we could not retrieve it. Always
    transient from our side: the caller must ask for a redelivery rather than
    ingest a message with no body."""


def _auth() -> dict:
    return {"Authorization": f"Bearer {get_settings().resend_token}"}


def fetch_email(email_id: str) -> dict:
    """Retrieve one received email (body, headers, attachment metadata)."""
    try:
        resp = requests.get(f"{_API}/emails/receiving/{email_id}",
                            headers=_auth(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        detail = getattr(exc.response, "text", "") or str(exc)
        raise ReceivedEmailUnavailable(
            f"could not fetch received email {email_id}: {detail}") from exc


def list_received(*, page_size: int = _MAX_PAGE, max_pages: int = 20) -> list[dict]:
    """Received emails Resend still holds, newest first.

    Feeds ``services.reconcile``, which subtracts what we already stored to find
    mail no webhook ever delivered — after an outage longer than Resend's retry
    window there is no other trace of it. The endpoint has no date filter, only
    an id cursor, so pages are walked until one comes back short.

    A transport failure raises rather than returning the pages it did get: a
    half-read list would make every email we never saw look like one we already
    have, i.e. report a clean inbox precisely when something is wrong.
    ``max_pages`` is a different thing — a deliberate bound on how far back to
    look, and reaching it only leaves mail older than the reconcilable window.
    """
    out: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        params: dict = {"limit": page_size}
        if cursor:
            params["after"] = cursor
        try:
            resp = requests.get(f"{_API}/emails/receiving", params=params,
                                headers=_auth(), timeout=_TIMEOUT)
            resp.raise_for_status()
            page = resp.json().get("data") or []
        except (requests.RequestException, ValueError) as exc:
            detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            raise ReceivedEmailUnavailable(
                f"could not list received emails: {detail}") from exc

        out.extend(item for item in page
                   if isinstance(item, dict) and item.get("id"))
        if len(page) < page_size or not out:
            return out
        cursor = str(out[-1]["id"])
    log.warning("received-email list stopped at the %d-page cap", max_pages)
    return out


def _download(url: str) -> bytes:
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def _headers_list(detail: dict) -> list[dict]:
    """Normalise headers to Postmark's ``[{"Name", "Value"}]``.

    Two shapes have to survive this, both seen from Resend on real mail:
    a mapping (what it returns today) or a list of pairs. And a *repeated*
    header — several ``Received``, and on some forwards several
    ``Delivered-To`` — comes back as a list of values under one name, which is
    expanded into one entry per value here so that routing sees every one of
    them. Names are left exactly as sent; ``ingest._route`` matches them
    case-insensitively, because Resend lower-cases what Postmark title-cases.
    """
    headers = detail.get("headers") or []
    out: list[dict] = []

    def emit(name: object, value: object) -> None:
        if not name:
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            out.append({"Name": str(name), "Value": "" if item is None else str(item)})

    if isinstance(headers, dict):
        for name, value in headers.items():
            emit(name, value)
        return out
    for item in headers:
        if isinstance(item, dict):
            emit(item.get("Name") or item.get("name"),
                 item.get("Value") if item.get("Value") is not None else item.get("value"))
    return out


def _is_eml(att: dict) -> bool:
    """A forwarded bank email inside a backfill batch, rather than a statement."""
    name = str(att.get("filename") or att.get("name") or "")
    ctype = str(att.get("content_type") or att.get("contentType") or "").lower()
    return ctype.startswith("message/rfc822") or name.lower().endswith(".eml")


def _url_of(att: dict) -> str:
    return str(att.get("download_url") or att.get("downloadUrl") or "")


def _download_urls(email_id: str, wanted: list[dict]) -> dict[str, str]:
    """Attachment id → pre-signed download URL, for the parts we mean to read.

    Needed because the attachment entries *inside* a fetched email carry
    identity only — id, filename, content type, size — and no URL. The URL lives
    on the attachment endpoints, so the content of a forwarded email costs a
    third call on top of the webhook and the fetch. One list call covers a whole
    batch; ``has_more`` says that list is paginated, so rather than trust the
    first page to hold everything, whatever is still missing is asked for by id.

    A failure here is logged and returned as a gap, not raised: the caller
    already treats a contentless attachment as a skip.
    """
    urls: dict[str, str] = {}
    try:
        resp = requests.get(f"{_API}/emails/receiving/{email_id}/attachments",
                            headers=_auth(), timeout=_TIMEOUT)
        resp.raise_for_status()
        for item in resp.json().get("data") or []:
            if isinstance(item, dict) and item.get("id") and _url_of(item):
                urls[str(item["id"])] = _url_of(item)
    except (requests.RequestException, ValueError):
        log.exception("could not list attachments of received email %s", email_id)

    for att in wanted:
        att_id = str(att.get("id") or "")
        if not att_id or att_id in urls:
            continue
        try:
            resp = requests.get(
                f"{_API}/emails/receiving/{email_id}/attachments/{att_id}",
                headers=_auth(), timeout=_TIMEOUT)
            resp.raise_for_status()
            if url := _url_of(resp.json()):
                urls[att_id] = url
        except (requests.RequestException, ValueError):
            log.exception("could not resolve attachment %s of received email %s",
                          att_id, email_id)
    return urls


def _attachments(detail: dict) -> list[dict]:
    """Postmark-shaped attachments, content downloaded and base64-encoded.

    Only the ``message/rfc822`` parts matter — they are the forwarded bank
    emails in a "forward as attachment" backfill (``ingest._eml_attachments``).
    Anything else is left as metadata with no content, so a PDF statement costs
    no bandwidth. A download that fails is logged and dropped: the batch it
    belongs to already tolerates a bad attachment, and failing the whole
    delivery over one is worse than backfilling the rest.

    REGRESSION (2026-07-26): this used to read ``download_url`` straight off the
    attachment entry, the way Postmark hands over content inline. Resend never
    sends one there, so every .eml arrived empty and a real 12-email backfill
    parsed as "0 transactions (12 skipped)" — a silent failure, because an empty
    body simply looks like mail from a sender that isn't a bank. URLs now come
    from ``_download_urls``, and a part we cannot resolve is logged as an error.
    """
    items = [a for a in (detail.get("attachments") or []) if isinstance(a, dict)]
    wanted = [a for a in items if _is_eml(a)]
    urls: dict[str, str] = {}
    if any(not _url_of(att) for att in wanted):
        urls = _download_urls(
            str(detail.get("id") or detail.get("email_id") or ""), wanted)

    out = []
    for att in items:
        name = str(att.get("filename") or att.get("name") or "")
        ctype = str(att.get("content_type") or att.get("contentType") or "").lower()
        item = {"Name": name, "ContentType": ctype, "Content": ""}
        if _is_eml(att):
            url = _url_of(att) or urls.get(str(att.get("id") or ""), "")
            if not url:
                log.error("no download url for forwarded email %r", name)
            else:
                try:
                    item["Content"] = base64.b64encode(_download(url)).decode()
                except requests.RequestException:
                    log.exception("attachment download failed: %s", name)
        out.append(item)
    return out


def to_postmark_payload(detail: dict) -> dict:
    """Translate a fetched Resend email into the dict ``handle_inbound`` reads."""
    recipients = detail.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]

    envelope = ""
    for key in _ENVELOPE_KEYS:
        value = detail.get(key)
        if isinstance(value, str) and value:
            envelope = value
            break
        if isinstance(value, list) and value:
            envelope = value[0]
            break

    return {
        "MessageID": detail.get("id") or detail.get("email_id") or "",
        "From": detail.get("from") or "",
        "Subject": detail.get("subject") or "",
        "HtmlBody": detail.get("html") or "",
        "TextBody": detail.get("text") or "",
        # `_route` reads OriginalRecipient first and falls back to ToFull; both
        # feed one token list, so supplying whichever Resend actually gives us
        # is enough. Never invent an envelope from the header.
        "OriginalRecipient": envelope,
        "To": ", ".join(recipients),
        "ToFull": [{"Email": addr, "MailboxHash": ""} for addr in recipients],
        "Headers": _headers_list(detail),
        "Attachments": _attachments(detail),
    }


def payload_from_event(event: dict) -> dict:
    """Webhook event → ingestible payload. Raises ReceivedEmailUnavailable."""
    data = event.get("data") or {}
    email_id = data.get("email_id") or data.get("id") or ""
    if not email_id:
        raise ReceivedEmailUnavailable("event carried no email id")
    return to_postmark_payload(fetch_email(email_id))
