"""Budget-threshold alert emails.

Email is deliberately *not* the per-transaction channel Telegram is: it fires
only on the transaction that pushes a category across its threshold. The cases
worth pinning are the ones that would each produce a stream of unwanted mail —
an unbudgeted category, a category already over, an uncategorized charge — and
the one that would produce none at all when it should.
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from budgetcore.models import Bank, Transaction as CoreTxn, TxType
from web.app.services import notify


@pytest.fixture()
def sent(monkeypatch):
    """Capture budget-alert mail instead of sending it."""
    out = []
    monkeypatch.setattr(notify.email, "send_budget_alert_email",
                        lambda to, cat, spent, budget: out.append(
                            {"to": to, "category": cat, "spent": spent,
                             "budget": budget}))
    return out


def _signup_login(client, email="alerts@example.com", pw="supersecret"):
    from web.app.auth.security import make_verify_token
    from web.app.db import get_sessionmaker
    from web.app.models import User
    client.post("/register", data={"email": email, "password": pw},
                follow_redirects=True)
    with get_sessionmaker()() as s:
        uid = s.scalar(select(User).where(
            func.lower(User.email) == email.lower())).id
    client.get(f"/verify?token={make_verify_token(uid)}", follow_redirects=True)
    client.post("/login", data={"email": email, "password": pw})
    return uid


def _category(uid, name="Supermercado"):
    from web.app.db import get_sessionmaker
    from web.app.models import Category
    with get_sessionmaker()() as s:
        cat = s.scalar(select(Category).where(Category.user_id == uid,
                                              Category.name == name))
        if cat is None:
            cat = Category(user_id=uid, name=name)
            s.add(cat)
            s.commit()
        return cat.id


def _set_budget(uid, category_id, amount):
    from web.app.db import get_sessionmaker
    from web.app.models import Budget
    with get_sessionmaker()() as s:
        s.add(Budget(user_id=uid, category_id=category_id,
                     month=date.today().replace(day=1), amount_dop=amount))
        s.commit()


def _txn(amount, tx_type=TxType.CONSUMO):
    return CoreTxn(message_id="m1", bank=Bank.POPULAR, last4="1234",
                   tx_type=tx_type, txn_date=date.today(), merchant="JUMBO",
                   amount_dop=amount, original_amount=amount, currency="RD$",
                   category="Supermercado")


def _alert(uid, spent, budget, amount, category_id, name="Supermercado"):
    """Run the dispatcher the way both ingest and the manual path do."""
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        notify.transaction_alert(s, uid, _txn(amount), spent, budget,
                                 category_id=category_id, category_name=name)


def _enable_email(uid):
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        notify.set_enabled(s, uid, notify.EMAIL, True)


def test_no_email_when_category_has_no_budget(client, sent):
    """THE regression this guards: an unbudgeted category has budget 0, and
    every charge on it is trivially "over" — without the guard each one mails
    the user that a limit they never set was reached."""
    uid = _signup_login(client)
    cat = _category(uid)
    _enable_email(uid)
    _alert(uid, spent=500.0, budget=0.0, amount=500.0, category_id=cat)
    assert sent == []


def test_email_when_crossing_the_default_threshold(client, sent):
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _enable_email(uid)
    # 800 -> 1100: crosses 100%.
    _alert(uid, spent=1100.0, budget=1000.0, amount=300.0, category_id=cat)
    assert len(sent) == 1
    assert sent[0]["category"] == "Supermercado"
    assert sent[0]["spent"] == 1100.0


def test_no_second_email_while_already_over(client, sent):
    """Only the crossing transaction mails. Otherwise every further purchase in
    an exceeded category is another email."""
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _enable_email(uid)
    # already at 1100 before this one -> no new crossing.
    _alert(uid, spent=1300.0, budget=1000.0, amount=200.0, category_id=cat)
    assert sent == []


def test_custom_threshold_fires_below_100(client, sent):
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _enable_email(uid)
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        notify.set_category_alert(s, uid, cat, 80.0, True)
        s.commit()
    # 700 -> 850: crosses 80% but not 100%.
    _alert(uid, spent=850.0, budget=1000.0, amount=150.0, category_id=cat)
    assert len(sent) == 1


def test_category_can_be_muted(client, sent):
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _enable_email(uid)
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        notify.set_category_alert(s, uid, cat, 100.0, False)
        s.commit()
    _alert(uid, spent=1100.0, budget=1000.0, amount=300.0, category_id=cat)
    assert sent == []


def test_nothing_sent_when_email_channel_is_off(client, sent):
    """Email is opt-in; the channel defaults to absent, not enabled."""
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _alert(uid, spent=1100.0, budget=1000.0, amount=300.0, category_id=cat)
    assert sent == []


def test_uncategorized_transaction_never_mails(client, sent):
    """category_id None means "Otros / sin categoría" has no budget of its own
    to breach; mailing about it would be noise on exactly the transactions the
    user has not sorted yet."""
    uid = _signup_login(client)
    _enable_email(uid)
    _alert(uid, spent=5000.0, budget=1000.0, amount=300.0, category_id=None)
    assert sent == []


def test_reversal_that_drops_below_does_not_mail(client, sent):
    """A refund moves spend down through the threshold. Only upward crossings
    are alerts."""
    uid = _signup_login(client)
    cat = _category(uid)
    _set_budget(uid, cat, 1000.0)
    _enable_email(uid)
    # 1100 -> 900 via a 200 reversal.
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        notify.transaction_alert(s, uid, _txn(200.0, TxType.REVERSAL),
                                 900.0, 1000.0, category_id=cat,
                                 category_name="Supermercado")
    assert sent == []


def test_threshold_page_and_save_round_trip(client, sent):
    uid = _signup_login(client)
    cat = _category(uid)
    assert client.get("/notifications").status_code == 200
    client.post("/notifications/email/toggle", data={"enabled": "on"})
    r = client.post("/notifications/email/thresholds",
                    data={f"threshold_{cat}": "75", f"enabled_{cat}": "on"},
                    follow_redirects=True)
    assert r.status_code == 200
    from web.app.db import get_sessionmaker
    with get_sessionmaker()() as s:
        row = notify.get_category_alert(s, uid, cat)
    assert row is not None and row.threshold_pct == 75.0 and row.enabled


def test_thresholds_reject_another_users_category(client, sent):
    """The form posts category ids; one belonging to someone else must not be
    writable."""
    from web.app.db import get_sessionmaker
    other = _signup_login(client, "other@example.com")
    other_cat = _category(other, "Supermercado")
    client.get("/logout", follow_redirects=True)
    uid = _signup_login(client)
    client.post("/notifications/email/thresholds",
                data={f"threshold_{other_cat}": "10",
                      f"enabled_{other_cat}": "on"}, follow_redirects=True)
    with get_sessionmaker()() as s:
        assert notify.get_category_alert(s, uid, other_cat) is None
        assert notify.get_category_alert(s, other, other_cat) is None
