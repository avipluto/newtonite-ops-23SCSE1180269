"""Test setup: a real Postgres test database (ops_test), migrated once, emptied before every test.

Concurrency behaviour depends on real row locks and transactions, so we deliberately
do not use SQLite or mocks here.
"""
import os

# Point the app at the test database BEFORE any app module creates its engine.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ops:ops@localhost:5434/ops_test")

from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuthToken, Membership, Team, User  # noqa: E402
from app.security import hash_password, hash_token  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLES = ("notifications, jobs, idempotency_keys, activity_events, comments, work_items, "
          "auth_tokens, memberships, teams, users")
PASSWORD = "pw-for-tests"
PASSWORD_HASH = hash_password(PASSWORD)  # hashed once: hashing is slow on purpose


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_tables():
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def world():
    """Team 'Ops' with one user per role (two leads, so one can approve the other's work),
    and team 'Other' with an outsider. Returns ids by name."""
    members = {
        "alice": {"Ops": "lead"},
        "dave": {"Ops": "lead"},
        "bob": {"Ops": "member"},
        "carol": {"Ops": "member"},
        "erin": {"Ops": "member"},
        "vic": {"Ops": "viewer"},
        "adm": {"Ops": "admin"},
        "oscar": {"Other": "member"},
        "olga": {"Other": "lead"},
    }
    with SessionLocal() as db:
        teams = {name: Team(name=name) for name in ("Ops", "Other")}
        db.add_all(teams.values())
        db.flush()
        users = {}
        for username, roles in members.items():
            user = User(username=username, display_name=username.title(), password_hash=PASSWORD_HASH)
            db.add(user)
            db.flush()
            users[username] = user.id
            for team_name, role in roles.items():
                db.add(Membership(user_id=user.id, team_id=teams[team_name].id, role=role))
        db.commit()
        return SimpleNamespace(ops=teams["Ops"].id, other=teams["Other"].id, users=users)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def headers(world):
    """headers('bob') -> {'Authorization': 'Bearer ...'} for that user (token inserted directly)."""
    def make(username: str) -> dict:
        token = f"test-token-{username}"
        with SessionLocal() as db:
            if db.get(AuthToken, hash_token(token)) is None:
                db.add(AuthToken(token_hash=hash_token(token), user_id=world.users[username],
                                 expires_at=text("now() + interval '1 hour'")))
                db.commit()
        return {"Authorization": f"Bearer {token}"}
    return make


@pytest.fixture
def make_item(client, headers, world):
    """Create an item through the API as `as_user` (default bob) and return its JSON."""
    def make(as_user: str = "bob", **fields) -> dict:
        body = {"team_id": world.ops, "title": "Payment gateway timeout", "type": "incident",
                "priority": "high", **fields}
        response = client.post("/api/items", json=body, headers=headers(as_user))
        assert response.status_code == 201, response.text
        return response.json()
    return make
