"""scripts/unroute_raw_email.py — the repair tool for misrouted mail.

It deletes real user data, so the two things that matter are that a dry run
changes nothing and that a real run deletes only the transactions belonging to
the named email.
"""

import base64
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select

from conftest import OWNER_EMAIL

ROOT = Path(__file__).parent.parent.parent
FIXTURES = ROOT / "core" / "tests" / "fixtures"
SCRIPT = ROOT / "scripts" / "unroute_raw_email.py"

QIK_SENDER = "notificaciones@qik.do"
POPULAR_SENDER = "notificaciones@popularenlinea.com"


def _eml(fixture, sender, subject):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["To"] = OWNER_EMAIL
    msg.set_content("(sin texto)")
    msg.add_alternative((FIXTURES / fixture).read_text(encoding="utf-8"),
                        subtype="html")
    return {"Name": f"{fixture}.eml", "ContentType": "message/rfc822",
            "Content": base64.b64encode(msg.as_bytes()).decode()}


def _ingest_batch(client):
    """A backfill batch from the owner: two transactions on one raw email."""
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        token = s.scalar(select(InboundAddress)).token
    client.post("/webhooks/postmark-inbound/testsecret", json={
        "MessageID": "batch-1",
        "From": OWNER_EMAIL,
        "Subject": "Fwd: mis consumos",
        "ToFull": [{"Email": f"u_{token}@in.example.do"}],
        "HtmlBody": "<p>aquí están</p>",
        "Attachments": [
            _eml("qik_purchase.html", QIK_SENDER,
                 "Usaste tu tarjeta de crédito Qik"),
            _eml("popular_consumo.html", POPULAR_SENDER,
                 "Notificación de Consumo")],
        "Headers": [],
    })
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        return s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "batch-1"))


def _run(db_url, raw_id, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(raw_id), *extra],
        capture_output=True, text=True, cwd=str(ROOT),
        env={"BUDGET_DATABASE_URL": db_url, "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(ROOT)})


def _counts():
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        return len(s.scalars(select(Transaction)).all())


def test_dry_run_lists_but_deletes_nothing(client, tmp_path):
    raw = _ingest_batch(client)
    assert _counts() == 2

    r = _run(f"sqlite:///{tmp_path}/test.db", raw.id)
    assert r.returncode == 0, r.stderr
    assert "2 transaction(s) would be deleted" in r.stdout
    assert "Dry run" in r.stdout
    assert _counts() == 2, "a dry run must not touch the data"


def test_yes_deletes_the_transactions_and_marks_the_row(client, tmp_path):
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    raw = _ingest_batch(client)
    r = _run(f"sqlite:///{tmp_path}/test.db", raw.id, "--yes")
    assert r.returncode == 0, r.stderr
    assert _counts() == 0

    with get_sessionmaker()() as s:
        row = s.get(RawEmail, raw.id)
        assert row.processing_status == "rejected"
        assert "wrong account" in row.note


def test_an_id_prefix_is_enough(client, tmp_path):
    raw = _ingest_batch(client)
    r = _run(f"sqlite:///{tmp_path}/test.db", str(raw.id)[:8], "--yes")
    assert r.returncode == 0, r.stderr
    assert _counts() == 0


def test_an_unknown_id_changes_nothing(client, tmp_path):
    _ingest_batch(client)
    r = _run(f"sqlite:///{tmp_path}/test.db", "ffffffff", "--yes")
    assert r.returncode != 0
    assert "no raw_email matches" in (r.stderr + r.stdout)
    assert _counts() == 2
