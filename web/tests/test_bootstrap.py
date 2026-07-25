"""The bootstrap account's password.

BUDGET_BOOTSTRAP_PASSWORD seeds the owner's password on a fresh install, but must
never re-apply itself afterwards: it used to run on every startup, so a password
changed through the UI was reverted on the next restart or deploy, handing the
account back to anyone who knew the env value.
"""

import pytest
from sqlalchemy import select

BOOT_PW = "bootstrap-secret-1"
OWNER = "owner@example.com"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A throwaway database and a sessionmaker, without the web app."""
    monkeypatch.setenv("BUDGET_DATABASE_URL", f"sqlite:///{tmp_path}/boot.db")
    monkeypatch.setenv("BUDGET_USER_EMAIL", OWNER)
    monkeypatch.delenv("BUDGET_BOOTSTRAP_PASSWORD", raising=False)

    from web.app import db as dbmod
    dbmod.reset_engine()
    from web.app.db import get_engine, get_sessionmaker
    from web.app.models import Base
    Base.metadata.create_all(get_engine())
    yield get_sessionmaker()
    dbmod.reset_engine()


def _owner(sessionmaker):
    from web.app.models import User
    with sessionmaker() as s:
        return s.scalar(select(User).where(User.email == OWNER))


def _run_bootstrap(sessionmaker):
    from web.app.services.seed import bootstrap
    with sessionmaker() as s:
        bootstrap(s)


def test_bootstrap_seeds_the_password_on_a_fresh_install(db, monkeypatch):
    from web.app.auth.security import verify_password
    monkeypatch.setenv("BUDGET_BOOTSTRAP_PASSWORD", BOOT_PW)
    _run_bootstrap(db)

    user = _owner(db)
    assert user is not None and user.is_verified
    assert verify_password(BOOT_PW, user.hashed_password)


def test_bootstrap_seeds_a_password_onto_an_account_that_has_none(db, monkeypatch):
    """The case the variable was introduced for: an account predating password
    auth still needs a way to get one."""
    from web.app.auth.security import verify_password
    _run_bootstrap(db)                      # creates the account, no password
    assert _owner(db).hashed_password is None

    monkeypatch.setenv("BUDGET_BOOTSTRAP_PASSWORD", BOOT_PW)
    _run_bootstrap(db)
    assert verify_password(BOOT_PW, _owner(db).hashed_password)


def test_restart_does_not_revert_a_password_changed_in_the_app(db, monkeypatch):
    """The regression this guards: bootstrap used to re-hash the env password on
    every startup, so a UI password change was undone by the next deploy."""
    from web.app.auth.security import verify_password
    from web.app.auth.service import set_password
    from web.app.models import User

    monkeypatch.setenv("BUDGET_BOOTSTRAP_PASSWORD", BOOT_PW)
    _run_bootstrap(db)

    with db() as s:
        set_password(s, s.scalar(select(User).where(User.email == OWNER)),
                     "chosen-by-the-user")

    _run_bootstrap(db)                      # simulates a restart / deploy

    user = _owner(db)
    assert verify_password("chosen-by-the-user", user.hashed_password)
    assert not verify_password(BOOT_PW, user.hashed_password), \
        "the env password came back — a UI change would be silently reverted"


def test_session_version_survives_a_restart(db, monkeypatch):
    """A password change bumps session_version to evict other devices; bootstrap
    must leave that alone. (The old re-applying code did too — what it broke was
    the credential itself, covered by the test above. This pins the invariant so
    a future change to bootstrap cannot quietly un-evict anyone.)"""
    from web.app.auth.service import set_password
    from web.app.models import User

    monkeypatch.setenv("BUDGET_BOOTSTRAP_PASSWORD", BOOT_PW)
    _run_bootstrap(db)
    before = _owner(db).session_version

    with db() as s:
        set_password(s, s.scalar(select(User).where(User.email == OWNER)), "new-one-1")
    bumped = _owner(db).session_version
    assert bumped > before

    _run_bootstrap(db)
    assert _owner(db).session_version == bumped


def test_bootstrap_is_still_idempotent_without_the_variable(db):
    from web.app.models import Category, InboundAddress
    _run_bootstrap(db)
    _run_bootstrap(db)
    with db() as s:
        assert len(s.scalars(select(InboundAddress)).all()) == 1
        names = [c.name for c in s.scalars(select(Category)).all()]
        assert len(names) == len(set(names)), "categories were seeded twice"
