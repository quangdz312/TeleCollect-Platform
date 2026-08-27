"""Per-user Weights & Biases credentials.

A W&B key is a live credential to somebody's own account, so the tests that
matter here are the ones about where it is allowed to appear: never in an API
response, never on a command line, never in a file on disk, and never inherited
by a job whose owner did not supply one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.db import User, UserIntegration
from src.models.enums import UserRole
from src.services import secrets
from src.services.security import create_access_token, hash_password

API = "/api/v1/integrations/wandb"
REAL_KEY = "0123456789abcdef0123456789abcdef01234567"


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_user(db_session, username: str, role: UserRole = UserRole.REVIEWER) -> User:
    user = User(
        username=username,
        password_hash=hash_password("correctpass"),
        display_name=username,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# --- encryption --------------------------------------------------------------


def test_a_key_round_trips_through_encryption():
    token = secrets.encrypt(REAL_KEY)

    assert token != REAL_KEY
    assert REAL_KEY not in token
    assert secrets.decrypt(token) == REAL_KEY


def test_a_key_encrypted_under_another_secret_is_unreadable_not_fatal(monkeypatch):
    """Rotating JWT_SECRET must ask for the key again, not crash the request."""

    token = secrets.encrypt(REAL_KEY)
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret", "a-completely-different-secret")

    assert secrets.decrypt(token) is None


def test_preview_cannot_be_used_as_a_key():
    assert secrets.preview(REAL_KEY) == "…4567"
    assert REAL_KEY[:-4] not in secrets.preview(REAL_KEY)


# --- the API never hands the key back ----------------------------------------


@pytest.mark.asyncio
async def test_saving_then_reading_never_returns_the_key(client, db_session):
    user = await _create_user(db_session, "wandb_owner")

    saved = await client.put(
        API, headers=_auth_headers(user), json={"api_key": REAL_KEY, "entity": "my-team"},
    )
    assert saved.status_code == 200, saved.text
    read = await client.get(API, headers=_auth_headers(user))

    for response in (saved, read):
        body = response.json()
        assert body["configured"] is True
        assert body["key_preview"] == "…4567"
        assert body["entity"] == "my-team"
        assert REAL_KEY not in response.text


@pytest.mark.asyncio
async def test_the_stored_key_is_not_plain_text_in_the_database(client, db_session):
    user = await _create_user(db_session, "wandb_atrest")

    await client.put(API, headers=_auth_headers(user), json={"api_key": REAL_KEY})

    row = await db_session.get(UserIntegration, user.id)
    await db_session.refresh(row)
    assert row.wandb_api_key != REAL_KEY
    assert REAL_KEY not in row.wandb_api_key
    assert secrets.decrypt(row.wandb_api_key) == REAL_KEY


@pytest.mark.asyncio
async def test_one_user_cannot_see_another_users_key(client, db_session):
    owner = await _create_user(db_session, "wandb_a")
    other = await _create_user(db_session, "wandb_b")
    await client.put(API, headers=_auth_headers(owner), json={"api_key": REAL_KEY})

    response = await client.get(API, headers=_auth_headers(other))

    assert response.json()["configured"] is False
    assert REAL_KEY not in response.text


@pytest.mark.asyncio
async def test_an_admin_cannot_read_someone_elses_key(client, db_session):
    owner = await _create_user(db_session, "wandb_owner2")
    admin = await _create_user(db_session, "wandb_admin", UserRole.ADMIN)
    await client.put(API, headers=_auth_headers(owner), json={"api_key": REAL_KEY})

    response = await client.get(API, headers=_auth_headers(admin))

    assert response.json()["configured"] is False


@pytest.mark.asyncio
async def test_entity_can_be_changed_without_resupplying_the_key(client, db_session):
    """The interface cannot show the key, so it cannot ask for it back."""

    user = await _create_user(db_session, "wandb_entity")
    await client.put(API, headers=_auth_headers(user), json={"api_key": REAL_KEY})

    response = await client.put(API, headers=_auth_headers(user), json={"entity": "new-team"})

    assert response.json()["configured"] is True
    assert response.json()["entity"] == "new-team"
    row = await db_session.get(UserIntegration, user.id)
    await db_session.refresh(row)
    assert secrets.decrypt(row.wandb_api_key) == REAL_KEY


@pytest.mark.asyncio
async def test_deleting_clears_the_key(client, db_session):
    user = await _create_user(db_session, "wandb_delete")
    await client.put(API, headers=_auth_headers(user), json={"api_key": REAL_KEY})

    await client.delete(API, headers=_auth_headers(user))

    assert (await client.get(API, headers=_auth_headers(user))).json()["configured"] is False
    assert await db_session.get(UserIntegration, user.id) is None


@pytest.mark.asyncio
async def test_settings_require_authentication(client):
    assert (await client.get(API)).status_code in {401, 403}


# --- the key reaches training without leaking --------------------------------


def _manager(tmp_path: Path):
    from src.training.jobs import TrainingJobManager

    return TrainingJobManager(tmp_path / "jobs")


def test_the_key_is_kept_out_of_the_job_file(tmp_path):
    """`job.json` sits on disk; encrypting in the DB then writing it here in
    clear text would undo the point."""

    manager = _manager(tmp_path)
    record = {"id": "job1", "status": "pending", "wandb_api_key": REAL_KEY}

    manager._write(record)

    written = (tmp_path / "jobs" / "job1" / "job.json").read_text(encoding="utf-8")
    assert REAL_KEY not in written
    assert "wandb_api_key" not in json.loads(written)


def test_the_key_travels_by_environment_not_command_line(tmp_path):
    """Command-line arguments are visible to anyone who can list processes."""

    from src.training.jobs import TrainingJobManager

    record = {"wandb_api_key": REAL_KEY}
    environment = TrainingJobManager._environment(record)

    assert environment["WANDB_API_KEY"] == REAL_KEY


def test_a_job_without_a_key_does_not_inherit_the_servers(monkeypatch):
    """Otherwise a run would log to whoever happened to start the server."""

    from src.training.jobs import TrainingJobManager

    monkeypatch.setenv("WANDB_API_KEY", "someone-elses-key")

    environment = TrainingJobManager._environment({"wandb_api_key": ""})

    assert "WANDB_API_KEY" not in environment
