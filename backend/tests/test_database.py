from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from backend.app.db import init_database, make_engine


def test_sqlite_accepts_parallel_connections_after_initialization(settings):
    engine = make_engine(settings)
    init_database(engine, settings)

    def query_database(_index: int) -> int:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT 1"))

    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            assert list(executor.map(query_database, range(60))) == [1] * 60
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert connection.exec_driver_sql("PRAGMA wal_autocheckpoint").scalar() == 1000
            assert connection.exec_driver_sql("PRAGMA journal_size_limit").scalar() == 64 * 1024 * 1024
    finally:
        engine.dispose()
