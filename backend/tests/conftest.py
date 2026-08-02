from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api import router
from backend.app.config import Settings, get_settings
from backend.app.db import create_fts, get_db
from backend.app.models import Base


REPO_DIR = Path(__file__).resolve().parents[2]


def pytest_configure(config) -> None:
    """Keep explicitly configured pytest temp roots out of application data."""
    base_temp = config.getoption("basetemp")
    if not base_temp:
        return
    resolved = Path(base_temp).resolve()
    protected = (REPO_DIR / "data", REPO_DIR / "backend" / "data")
    if any(resolved.is_relative_to(root.resolve()) for root in protected):
        raise pytest.UsageError(
            "pytest --basetemp must not point inside an application data directory"
        )


@pytest.fixture
def db_factory(tmp_path: Path):
    database = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def sqlite_pragmas(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    create_fts(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        debug=False,
        scheduler_enabled=False,
        update_check_enabled=False,
        sync_on_startup=False,
    )


@pytest.fixture
def api_client(db_factory, settings):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    yield client, db_factory
    client.close()


@pytest.fixture
def authenticated_client(api_client):
    client, factory = api_client
    response = client.post("/api/v1/auth/setup", json={"password": "reader88"})
    assert response.status_code == 201, response.text
    csrf = response.json()["csrf_token"]
    return client, factory, {"X-CSRF-Token": csrf}
