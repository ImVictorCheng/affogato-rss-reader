from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from typer.testing import CliRunner

from backend.app import cli as cli_module
from backend.app import jobs as jobs_module
from backend.app.cli import app as cli_app
from backend.app.jobs import (
    enqueue_maintenance,
    recover_interrupted_jobs,
    recover_interrupted_operations,
    run_queued_jobs,
)
from backend.app.models import Entry, Feed, Job, SyncRun, Translation, Work, utcnow
from backend.app.scheduler import Scheduler


def install_successful_services(monkeypatch, backup_path: Path | None = None) -> None:
    monkeypatch.setattr(
        jobs_module,
        "sync_due_feeds",
        lambda _db, _settings, feed_id=None: [
            SimpleNamespace(
                status="success",
                fetched_count=3,
                created_count=2,
                updated_count=1,
            )
        ],
    )
    monkeypatch.setattr(
        jobs_module,
        "translate_pending",
        lambda _db, limit, retry_failed: [SimpleNamespace(status="complete")],
    )
    monkeypatch.setattr(jobs_module, "run_due_schedules", lambda _db: [SimpleNamespace(id=41)])
    monkeypatch.setattr(jobs_module, "backup_once_daily", lambda _db, _settings: backup_path)


def test_maintenance_job_records_all_independent_phases(
    db_factory, settings, monkeypatch, tmp_path
):
    install_successful_services(monkeypatch, tmp_path / "backup.db")
    with db_factory() as db:
        queued = enqueue_maintenance(db, reason="test")
        rows = run_queued_jobs(db, settings)
        assert [row.id for row in rows] == [queued.id]
        job = db.get(Job, queued.id)
        assert job.status == "complete"
        assert set(job.result) == {"sync", "translation", "brief", "backup"}
        assert job.result["brief"] == {"created": 1, "ids": [41]}


def test_running_job_is_recovered_and_reexecuted(db_factory, settings, monkeypatch):
    install_successful_services(monkeypatch)
    with db_factory() as db:
        interrupted = Job(
            kind="maintenance",
            status="running",
            payload={"reason": "scheduler"},
            result={"sync": {"processed": 1}},
            started_at=utcnow(),
        )
        db.add(interrupted)
        db.commit()
        assert recover_interrupted_jobs(db) == 1
        rows = run_queued_jobs(db, settings)
        assert len(rows) == 1
        db.refresh(interrupted)
        assert interrupted.status == "complete"
        assert interrupted.payload["recovered"] is True


def test_interrupted_sync_and_translation_are_retryable(db_factory):
    with db_factory() as db:
        feed = Feed(
            title="Interrupted feed",
            url="https://interrupted.test/rss",
            next_fetch_at=utcnow() + timedelta(days=1),
        )
        work = Work(
            dedup_key="url:https://interrupted.test/article",
            canonical_url="https://interrupted.test/article",
        )
        db.add_all([feed, work])
        db.flush()
        entry = Entry(
            work_id=work.id,
            version_key="default",
            title="Interrupted translation",
            summary="Summary",
            url="https://interrupted.test/article",
            source_hash="e" * 64,
        )
        db.add(entry)
        db.flush()
        run = SyncRun(feed_id=feed.id, status="running")
        translation = Translation(
            entry_id=entry.id,
            source_hash=entry.source_hash,
            status="running",
            attempts=1,
        )
        db.add_all([run, translation])
        db.commit()
        assert recover_interrupted_operations(db) == {
            "sync_runs": 1,
            "translations": 1,
        }
        assert run.status == "failed"
        assert translation.status == "pending"
        assert feed.next_fetch_at <= utcnow()


def test_phase_exception_marks_job_failed(db_factory, settings, monkeypatch):
    backup_path = Path("backup-after-failure.db")
    backup_calls: list[Path] = []
    monkeypatch.setattr(
        jobs_module,
        "backup_once_daily",
        lambda _db, _settings: backup_calls.append(backup_path) or backup_path,
    )
    monkeypatch.setattr(
        jobs_module,
        "sync_due_feeds",
        lambda _db, _settings, feed_id=None: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        ),
    )
    monkeypatch.setattr(
        jobs_module,
        "translate_pending",
        lambda _db, limit, retry_failed: [SimpleNamespace(status="complete")],
    )
    monkeypatch.setattr(jobs_module, "run_due_schedules", lambda _db: [])
    with db_factory() as db:
        queued = enqueue_maintenance(db, reason="test")
        run_queued_jobs(db, settings)
        job = db.get(Job, queued.id)
        assert job.status == "failed"
        assert "database unavailable" in job.error
        assert backup_calls == [backup_path]
        assert job.result["backup"] == {
            "created": True,
            "path": str(backup_path),
        }
        assert job.result["sync"] == {
            "error": "database unavailable",
            "error_type": "RuntimeError",
        }
        assert job.result["translation"]["complete"] == 1
        assert job.result["brief"] == {"created": 0, "ids": []}


def test_enqueue_deduplicates_active_maintenance_jobs(db_factory):
    with db_factory() as db:
        first = enqueue_maintenance(db, reason="first")
        second = enqueue_maintenance(db, reason="second")
        assert second.id == first.id
        assert len(list(db.scalars(select(Job)))) == 1


def test_scheduler_startup_recovers_jobs(db_factory, settings, monkeypatch):
    install_successful_services(monkeypatch)
    with db_factory() as db:
        db.add(Job(kind="maintenance", status="running", payload={}, result={}))
        db.commit()
    import backend.app.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "SessionLocal", db_factory)
    enabled = settings.model_copy(update={"scheduler_enabled": True})
    asyncio.run(Scheduler(enabled)._startup())
    with db_factory() as db:
        assert db.scalar(select(Job)).status == "complete"


def test_scheduler_maintenance_is_not_blocked_by_slow_update_check(
    db_factory,
    settings,
    monkeypatch,
):
    import backend.app.scheduler as scheduler_module

    update_started = threading.Event()
    release_update = threading.Event()
    operations: list[str] = []

    def slow_update(_db, _settings):
        operations.append("update-started")
        update_started.set()
        assert release_update.wait(timeout=2)
        operations.append("update-finished")

    monkeypatch.setattr(scheduler_module, "SessionLocal", db_factory)
    monkeypatch.setattr(scheduler_module, "maintenance_due", lambda _db, _settings: True)
    monkeypatch.setattr(
        scheduler_module,
        "enqueue_maintenance",
        lambda _db, reason: operations.append(f"maintenance-enqueued:{reason}"),
    )
    monkeypatch.setattr(
        scheduler_module,
        "run_queued_jobs",
        lambda _db, _settings: operations.append("maintenance-finished"),
    )
    monkeypatch.setattr(scheduler_module, "update_check_due", lambda _settings: True)
    monkeypatch.setattr(scheduler_module, "check_for_updates", slow_update)
    enabled = settings.model_copy(
        update={"scheduler_enabled": True, "update_check_enabled": True}
    )

    async def scenario() -> None:
        scheduler = Scheduler(enabled)
        await scheduler._cycle()
        assert operations[:2] == ["maintenance-enqueued:scheduler", "maintenance-finished"]
        assert await asyncio.to_thread(update_started.wait, 1)
        assert scheduler.update_task is not None
        assert not scheduler.update_task.done()
        release_update.set()
        await scheduler.update_task

    asyncio.run(scenario())
    assert operations[-2:] == ["update-started", "update-finished"]


def test_cli_exposes_generic_commands_and_processes_jobs(
    db_factory, settings, monkeypatch
):
    command_names = {
        command.name or command.callback.__name__.replace("_", "-")
        for command in cli_app.registered_commands
    }
    assert {"jobs", "run-jobs", "sync", "translate"} <= command_names
    group_names = {group.name for group in cli_app.registered_groups}
    assert {"brief", "opml"} <= group_names
    install_successful_services(monkeypatch)
    with db_factory() as db:
        queued = enqueue_maintenance(db, reason="cli-test")
    monkeypatch.setattr(cli_module, "ready", lambda: None)
    monkeypatch.setattr(cli_module, "SessionLocal", db_factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli_app, ["run-jobs", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "processed=1" in result.output
    with db_factory() as db:
        assert db.get(Job, queued.id).status == "complete"
