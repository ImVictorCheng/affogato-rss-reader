from __future__ import annotations

import sqlite3
from contextlib import closing
import os
from datetime import datetime, timedelta

import pytest

from backend.app.backup import backup_database, backup_once_daily, prune_backups, sqlite_path
from backend.app.models import AppSetting, Entry, Work


def test_daily_backup_is_consistent_and_idempotent(db_factory, settings):
    with db_factory() as db:
        work = Work(
            dedup_key="url:https://backup.test/paper",
            canonical_url="https://backup.test/paper",
        )
        db.add(work)
        db.flush()
        db.add(
            Entry(
                work_id=work.id,
                version_key="default",
                title="Backed up paper",
                summary="Consistent snapshot",
                url="https://backup.test/paper",
                source_hash="b" * 64,
            )
        )
        db.commit()

        first = backup_once_daily(db, settings)
        second = backup_once_daily(db, settings)

        assert first is not None and first.is_file()
        assert second is None
        assert db.get(AppSetting, "last_backup_date") is not None

    with closing(sqlite3.connect(first)) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT title FROM entries").fetchone() == ("Backed up paper",)


def test_backup_rejects_live_database_as_destination(settings):
    with pytest.raises(ValueError, match="must differ"):
        backup_database(settings, sqlite_path(settings))


def test_backup_pruning_enforces_count_and_soft_byte_limit(settings):
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir()
    now = datetime.now().timestamp()
    files = []
    for index in range(4):
        path = backup_dir / f"affogato-rss-reader-202607{index + 1:02d}-000000-000000.db"
        path.write_bytes(b"x" * 600_000)
        timestamp = now - timedelta(days=index).total_seconds()
        os.utime(path, (timestamp, timestamp))
        files.append(path)

    configured = settings.model_copy(
        update={
            "backup_keep_days": 30,
            "backup_max_count": 3,
            "backup_min_count": 2,
            "backup_max_total_bytes": 1024 * 1024,
        }
    )
    removed = prune_backups(configured)

    assert set(removed) == set(files[2:])
    assert files[0].is_file() and files[1].is_file()
    assert sum(path.stat().st_size for path in files[:2]) > configured.backup_max_total_bytes
