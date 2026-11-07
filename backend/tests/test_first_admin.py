"""The first admin is configuration, never a constant.

Before this, ``app.main._seed_admin_user`` fell back to ``admin@example.com``
(and compose never passed INITIAL_ADMIN_EMAIL to the backend), so every fresh
install — and the live one — had a demo-looking admin. Now:

* no seed path invents an address: without INITIAL_ADMIN_EMAIL nothing is
  created, in any environment, and an ``example.*`` address is refused;
* the startup seed, ``make seed`` and the UAT seed read the same function;
* a deployment that already has accounts moves to its configured admin with
  ``python -m app.ops.first_admin ensure`` — it creates that account next to
  the existing ones (audited) and never touches another account, so there is
  no moment without a usable admin.
"""

from __future__ import annotations

import json
from pathlib import Path

import bcrypt
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.main as app_main
from app.core.db import Base
from app.core.first_admin import DEV_PASSWORD, FirstAdminNotConfigured, first_admin_from_env
from app.models.admin_audit_log import AdminAuditLogORM
from app.models.admin_user import AdminUserORM
from app.ops import first_admin as ops_first_admin

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent      # the checkout; in the test lane only the compose files are mounted beside /app


def _sessionmaker(tmp_path, name="first_admin.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _clear(monkeypatch):
    for k in ("INITIAL_ADMIN_EMAIL", "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


class TestTheAddressIsConfiguration:
    def test_no_address_means_no_admin_even_in_development(self):
        with pytest.raises(FirstAdminNotConfigured) as e:
            first_admin_from_env({"APP_ENV": "development"})
        assert "INITIAL_ADMIN_EMAIL" in str(e.value)

    @pytest.mark.parametrize("email", ["admin@example.com", "Admin@Example.COM", "ops@example.org", "x@example.net"])
    def test_a_placeholder_address_is_refused(self, email):
        with pytest.raises(FirstAdminNotConfigured) as e:
            first_admin_from_env({"APP_ENV": "development", "INITIAL_ADMIN_EMAIL": email})
        assert "placeholder" in str(e.value)

    def test_not_an_address_is_refused(self):
        with pytest.raises(FirstAdminNotConfigured):
            first_admin_from_env({"INITIAL_ADMIN_EMAIL": "admin"})

    def test_the_configured_address_is_used_as_given(self):
        first = first_admin_from_env({"APP_ENV": "development", "INITIAL_ADMIN_EMAIL": " Admin@Corp.Test "})
        assert first.email == "admin@corp.test"
        assert first.username == "admin@corp.test"          # no username configured: the e-mail
        assert first.password == DEV_PASSWORD                 # development only

    def test_outside_development_the_password_is_required(self):
        with pytest.raises(FirstAdminNotConfigured) as e:
            first_admin_from_env({"APP_ENV": "production", "INITIAL_ADMIN_EMAIL": "admin@corp.test"})
        assert "INITIAL_ADMIN_PASSWORD" in str(e.value)
        first = first_admin_from_env({"APP_ENV": "production", "INITIAL_ADMIN_EMAIL": "admin@corp.test",
                                      "INITIAL_ADMIN_USERNAME": "root", "INITIAL_ADMIN_PASSWORD": "s3cret-s3cret-s3cret"})
        assert (first.username, first.password) == ("root", "s3cret-s3cret-s3cret")

    def test_no_code_path_names_an_address(self):
        """Nothing under backend/app, the seed scripts or compose carries an admin address of its own."""
        files = [*(BACKEND / "app").rglob("*.py"), BACKEND / "scripts" / "seed_audit_data.py",
                 REPO / "scripts" / "uat" / "02_seed_uat.py", REPO / "docker-compose.yml", REPO / "docker-compose.prod.yml"]
        hits = [p.name for p in files
                if p.exists() and "admin@" in p.read_text(encoding="utf-8", errors="ignore")
                and p.name != "admin_session.py"]   # a comment there quotes a sign-in example
        assert hits == []
        assert (BACKEND / "app" / "main.py").exists() and (REPO / "docker-compose.yml").exists()

    def test_compose_passes_the_address_to_the_backend(self):
        text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        assert "INITIAL_ADMIN_EMAIL: ${INITIAL_ADMIN_EMAIL:-}" in text
        assert "INITIAL_ADMIN_USERNAME: ${INITIAL_ADMIN_USERNAME:-}" in text


class TestTheStartupSeed:
    def test_a_fresh_install_without_an_address_creates_no_admin(self, monkeypatch, tmp_path, capsys):
        Session = _sessionmaker(tmp_path)
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        monkeypatch.setattr(app_main, "_acquire_startup_lock", lambda s, i: None)
        monkeypatch.setenv("APP_ENV", "development")
        _clear(monkeypatch)
        app_main._seed_admin_user()
        with Session() as s:
            assert s.execute(select(AdminUserORM)).scalars().all() == []
        assert "INITIAL_ADMIN_EMAIL" in capsys.readouterr().out

    def test_a_fresh_install_gets_its_own_address_never_example_com(self, monkeypatch, tmp_path):
        Session = _sessionmaker(tmp_path)
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        monkeypatch.setattr(app_main, "_acquire_startup_lock", lambda s, i: None)
        monkeypatch.setenv("APP_ENV", "production")
        _clear(monkeypatch)
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@corp.test")
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "s3cret-s3cret-s3cret")
        app_main._seed_admin_user()
        with Session() as s:
            rows = s.execute(select(AdminUserORM)).scalars().all()
        assert [(r.email, r.username, r.role, r.is_active) for r in rows] == [("admin@corp.test", "admin@corp.test", "admin", True)]
        assert bcrypt.checkpw(b"s3cret-s3cret-s3cret", rows[0].hashed_password.encode())

    def test_a_configured_example_com_is_not_seeded(self, monkeypatch, tmp_path):
        Session = _sessionmaker(tmp_path)
        monkeypatch.setattr("app.core.db.SessionLocal", Session)
        monkeypatch.setattr(app_main, "_acquire_startup_lock", lambda s, i: None)
        monkeypatch.setenv("APP_ENV", "development")
        _clear(monkeypatch)
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
        app_main._seed_admin_user()
        with Session() as s:
            assert s.execute(select(AdminUserORM)).scalars().all() == []


class TestMovingAnExistingDeployment:
    def _old(self, s):
        s.add(AdminUserORM(id="old-1", username="admin", email="admin@example.com",
                           hashed_password="x", role="admin", is_active=True))
        s.commit()

    def test_ensure_creates_the_configured_admin_beside_the_old_one(self, monkeypatch, tmp_path):
        Session = _sessionmaker(tmp_path)
        _clear(monkeypatch)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@corp.test")
        monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "admin")          # taken by the old account
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "s3cret-s3cret-s3cret")
        with Session() as s:
            self._old(s)
            assert ops_first_admin.ensure(s) == ("created", "admin@corp.test")
            s.commit()
            # A working admin at every moment: the old one is untouched, the new one is active.
            assert ops_first_admin.active_admins(s) == [("admin@corp.test", "admin"), ("admin@example.com", "admin")]
            new = s.execute(select(AdminUserORM).where(AdminUserORM.email == "admin@corp.test")).scalar_one()
            assert new.username == "admin@corp.test"                    # the taken username is not fought over
            assert bcrypt.checkpw(b"s3cret-s3cret-s3cret", new.hashed_password.encode())
            old = s.get(AdminUserORM, "old-1")
            assert (old.is_active, old.hashed_password) == (True, "x")
            entry = s.execute(select(AdminAuditLogORM)).scalar_one()
            assert (entry.action, entry.actor_key_id, entry.resource_id) == ("admin_account.created", "system:first-admin", new.id)
            details = json.loads(entry.details)
            assert details == {"email": "admin@corp.test", "role": "admin", "via": "first-admin"}
            assert "s3cret" not in entry.details

    def test_ensure_changes_nothing_when_the_account_exists(self, monkeypatch, tmp_path):
        Session = _sessionmaker(tmp_path)
        _clear(monkeypatch)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@corp.test")
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "another-password-entirely")
        with Session() as s:
            s.add(AdminUserORM(id="n-1", username="admin@corp.test", email="admin@corp.test",
                               hashed_password="kept", role="viewer", is_active=False))
            s.commit()
            assert ops_first_admin.ensure(s) == ("exists", "admin@corp.test")
            s.commit()
            row = s.get(AdminUserORM, "n-1")
            assert (row.hashed_password, row.role, row.is_active) == ("kept", "viewer", False)   # no reset, no reactivation
            assert s.execute(select(AdminAuditLogORM)).scalars().all() == []

    def test_ensure_refuses_when_nothing_is_configured(self, monkeypatch, tmp_path):
        Session = _sessionmaker(tmp_path)
        _clear(monkeypatch)
        with Session() as s:
            with pytest.raises(FirstAdminNotConfigured):
                ops_first_admin.ensure(s)
