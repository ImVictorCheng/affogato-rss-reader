from __future__ import annotations

from collections.abc import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.api import router
from backend.app.config import Settings, get_settings
from backend.app.db import get_db


def test_no_auth_mode_bypasses_login_but_keeps_csrf(db_factory, tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'noauth.db').as_posix()}",
        auth_mode="none",
        scheduler_enabled=False,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        status = client.get("/api/v1/auth/status")
        assert status.status_code == 200
        assert status.json()["authenticated"] is True
        assert status.json()["mode"] == "none"
        assert status.json()["onboarding_required"] is True
        assert status.json()["warning"]
        assert client.get("/api/v1/feeds").status_code == 200
        assert client.post(
            "/api/v1/feeds", json={"url": "https://noauth.test/rss"}
        ).status_code == 403
        assert client.post(
            "/api/v1/feeds",
            json={"url": "https://noauth.test/rss"},
            headers={"X-CSRF-Token": status.json()["csrf_token"]},
        ).status_code == 201
