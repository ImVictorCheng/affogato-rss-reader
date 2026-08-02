from __future__ import annotations

from sqlalchemy import select

from backend.app.bootstrap import (
    ensure_initial_owner,
    initial_owner_password_path,
    read_initial_owner_password,
)
from backend.app.models import Owner
from backend.app.security import verify_password


def test_bootstrap_creates_one_pending_owner_and_reuses_password_file(settings, db_factory):
    password = ensure_initial_owner(settings, db_factory)

    assert password is not None
    assert len(password) >= 32
    assert read_initial_owner_password(settings) == password
    assert ensure_initial_owner(settings, db_factory) is None
    assert read_initial_owner_password(settings) == password

    with db_factory() as db:
        owner = db.scalar(select(Owner).limit(1))
        assert owner is not None
        assert owner.activation_required is True
        assert verify_password(password, owner.password_hash)


def test_owner_activation_consumes_initial_password(api_client, settings, db_factory):
    client, _factory = api_client
    initial_password = ensure_initial_owner(settings, db_factory)
    assert initial_password is not None

    status = client.get("/api/v1/auth/status").json()
    assert status["setup_required"] is False
    assert status["activation_required"] is True
    assert status["authenticated"] is False
    assert client.post("/api/v1/auth/login", json={"password": initial_password}).status_code == 409

    reused = client.post(
        "/api/v1/auth/activate",
        json={"initial_password": initial_password, "password": initial_password},
    )
    assert reused.status_code == 422

    activated = client.post(
        "/api/v1/auth/activate",
        json={"initial_password": initial_password, "password": "permanent-reader-88"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["activation_required"] is False
    assert activated.json()["authenticated"] is True
    assert not initial_owner_password_path(settings).exists()

    with db_factory() as db:
        owner = db.scalar(select(Owner).limit(1))
        assert owner is not None
        assert owner.activation_required is False
        assert not verify_password(initial_password, owner.password_hash)
        assert verify_password("permanent-reader-88", owner.password_hash)

    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"password": initial_password}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "permanent-reader-88"}).status_code == 200
