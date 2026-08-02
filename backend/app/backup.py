from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import AppSetting


BACKUP_PATTERNS = ("affogato-rss-reader-*.db",)


def sqlite_path(settings: Settings) -> Path:
    prefix = "sqlite:///"
    url = settings.effective_database_url
    if not url.startswith(prefix):
        raise RuntimeError("Built-in backups currently support SQLite databases only")
    return Path(url[len(prefix):]).resolve()


def _backup_candidates(backup_dir: Path) -> list[Path]:
    candidates: dict[Path, Path] = {}
    for pattern in BACKUP_PATTERNS:
        for candidate in backup_dir.glob(pattern):
            if candidate.is_file():
                candidates[candidate.resolve()] = candidate
    return sorted(
        candidates.values(),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def prune_backups(settings: Settings, *, protected: Path | None = None) -> list[Path]:
    backup_dir = settings.data_dir / "backups"
    if not backup_dir.is_dir():
        return []
    candidates = _backup_candidates(backup_dir)
    protected_paths = {
        path.resolve()
        for path in candidates[: min(settings.backup_min_count, settings.backup_max_count)]
    }
    if protected is not None:
        protected_resolved = protected.resolve()
        if protected_resolved.is_relative_to(backup_dir.resolve()):
            protected_paths.add(protected_resolved)

    cutoff = datetime.now(ZoneInfo(settings.timezone)).timestamp() - timedelta(
        days=settings.backup_keep_days
    ).total_seconds()
    removed: list[Path] = []

    def remove(candidate: Path) -> None:
        candidate.unlink()
        removed.append(candidate)

    for candidate in reversed(candidates):
        if candidate.resolve() not in protected_paths and candidate.stat().st_mtime < cutoff:
            remove(candidate)

    remaining = [candidate for candidate in candidates if candidate not in removed]
    for candidate in reversed(remaining):
        if len(remaining) <= settings.backup_max_count:
            break
        if candidate.resolve() in protected_paths:
            continue
        remove(candidate)
        remaining.remove(candidate)

    total_bytes = sum(candidate.stat().st_size for candidate in remaining)
    for candidate in reversed(remaining):
        if total_bytes <= settings.backup_max_total_bytes:
            break
        if candidate.resolve() in protected_paths:
            continue
        size = candidate.stat().st_size
        remove(candidate)
        remaining.remove(candidate)
        total_bytes -= size
    return removed


def backup_database(settings: Settings | None = None, output: Path | None = None) -> Path:
    settings = settings or get_settings()
    source = sqlite_path(settings)
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ZoneInfo(settings.timezone))
    output = output or backup_dir / f"affogato-rss-reader-{now.strftime('%Y%m%d-%H%M%S-%f')}.db"
    output = output.resolve()
    if output == source:
        raise ValueError("Backup destination must differ from the live database")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(source)) as source_db:
            with closing(sqlite3.connect(temporary)) as target_db:
                source_db.backup(target_db)
            with closing(sqlite3.connect(temporary)) as check_db:
                if check_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("The newly created SQLite backup failed its integrity check")
            os.replace(temporary, output)
            # Do not block active readers; journal_size_limit handles truncation
            # the next time SQLite can safely reset the WAL.
            try:
                source_db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.DatabaseError:
                # The verified backup is already complete. A concurrent reader
                # may defer WAL maintenance until a later connection/reset.
                pass
    finally:
        temporary.unlink(missing_ok=True)
    prune_backups(settings, protected=output)
    return output


def backup_once_daily(db: Session, settings: Settings | None = None) -> Path | None:
    settings = settings or get_settings()
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    row = db.get(AppSetting, "last_backup_date")
    if row and row.value == today:
        return None
    path = backup_database(settings)
    if row:
        row.value = today
    else:
        db.add(AppSetting(key="last_backup_date", value=today))
    db.commit()
    return path
