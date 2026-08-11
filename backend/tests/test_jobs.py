from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from typer.testing import CliRunner

from backend.app import cli as cli_module
from backend.app import jobs as jobs_module
from backend.app import scheduler as scheduler_module
from backend.app.cli import app as cli_app
from backend.app.jobs import (
    BACKUP_KIND,
    BRIEF_KIND,
    FEED_SYNC_KIND,
    MAINTENANCE_KIND,
    TRANSLATION_KIND,
    backup_due,
    brief_due,
    claim_next_job,
    enqueue_job,
    feed_sync_due,
    recover_interrupted_jobs,
    recover_interrupted_operations,
    run_queued_jobs,
    translation_due,
)
from backend.app.briefs import brief_schedule_window_key, schedule_window
from backend.app.models import (
    BriefSchedule,
    Entry,
    Feed,
    Job,
    SyncRun,
    Translation,
    Work,
    utcnow,
)
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


def test_background_jobs_are_executed_per_kind(db_factory, settings, monkeypatch, tmp_path):
    install_successful_services(monkeypatch, tmp_path / "backup.db")
    with db_factory() as db:
        backup = enqueue_job(db, BACKUP_KIND, reason="test")
        sync = enqueue_job(db, FEED_SYNC_KIND, reason="test")
        translation = enqueue_job(db, TRANSLATION_KIND, reason="test")
        brief = enqueue_job(db, BRIEF_KIND, reason="test")
        assert claim_next_job(db, BACKUP_KIND, settings).id == backup.id
        assert claim_next_job(db, FEED_SYNC_KIND, settings).id == sync.id
        assert claim_next_job(db, TRANSLATION_KIND, settings).id == translation.id
        assert claim_next_job(db, BRIEF_KIND, settings).id == brief.id
        assert claim_next_job(db, BRIEF_KIND, settings) is None
        assert db.get(Job, backup.id).status == "complete"
        assert db.get(Job, sync.id).status == "complete"
        assert db.get(Job, translation.id).status == "complete"
        assert db.get(Job, brief.id).status == "complete"
        assert db.get(Job, backup.id).result["backup"]["created"] is True
        assert db.get(Job, brief.id).result["brief"] == {"created": 1, "ids": [41]}


def test_running_job_is_recovered_and_reexecuted(db_factory, settings, monkeypatch):
    install_successful_services(monkeypatch)
    with db_factory() as db:
        interrupted = Job(
            kind=FEED_SYNC_KIND,
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


def test_legacy_maintenance_jobs_are_superseded(db_factory, settings, monkeypatch):
    with db_factory() as db:
        running = Job(
            kind=MAINTENANCE_KIND,
            status="running",
            payload={"reason": "scheduler"},
            result={},
            started_at=utcnow(),
        )
        queued = Job(kind=MAINTENANCE_KIND, status="queued", payload={}, result={})
        db.add_all([running, queued])
        db.commit()
        recover_interrupted_jobs(db)
        db.refresh(running)
        db.refresh(queued)
        assert running.status == "failed"
        assert queued.status == "failed"
        assert "Superseded" in running.error


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


def test_job_failure_is_isolated_to_its_kind(db_factory, settings, monkeypatch):
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
        backup = enqueue_job(db, BACKUP_KIND, reason="test")
        failing = enqueue_job(db, FEED_SYNC_KIND, reason="test")
        translation = enqueue_job(db, TRANSLATION_KIND, reason="test")
        brief = enqueue_job(db, BRIEF_KIND, reason="test")
        assert claim_next_job(db, BACKUP_KIND, settings) is not None
        assert claim_next_job(db, FEED_SYNC_KIND, settings) is not None
        assert claim_next_job(db, TRANSLATION_KIND, settings) is not None
        assert claim_next_job(db, BRIEF_KIND, settings) is not None
        assert db.get(Job, backup.id).status == "complete"
        assert db.get(Job, backup.id).result["backup"] == {
            "created": True,
            "path": str(backup_path),
        }
        assert db.get(Job, failing.id).status == "failed"
        assert "database unavailable" in db.get(Job, failing.id).error
        assert db.get(Job, translation.id).status == "complete"
        assert db.get(Job, brief.id).status == "complete"


def test_enqueue_deduplicates_active_jobs_per_kind(db_factory):
    with db_factory() as db:
        first = enqueue_job(db, FEED_SYNC_KIND, reason="first")
        second = enqueue_job(db, FEED_SYNC_KIND, reason="second")
        other = enqueue_job(db, BACKUP_KIND, reason="other")
        assert second.id == first.id
        assert other.id != first.id
        assert len(list(db.scalars(select(Job)))) == 2


def test_due_checks_split_by_kind(db_factory, settings):
    with db_factory() as db:
        assert feed_sync_due(db, settings) is False
        assert translation_due(db, settings) is False
        assert brief_due(db, settings) is False
        assert backup_due(db, settings) is True
        db.add(Feed(title="Due", url="https://due.test/rss", enabled=True, next_fetch_at=utcnow()))
        db.add(
            BriefSchedule(
                name="Due",
                period="daily",
                timezone="UTC",
                cutoff_time="09:00",
                enabled=True,
                created_at=datetime(2026, 1, 1),
            )
        )
        db.commit()
        assert feed_sync_due(db, settings) is True
        assert brief_due(db, settings) is True


def test_stopped_schedule_window_is_not_due_again(db_factory, settings):
    with db_factory() as db:
        schedule = BriefSchedule(
            name="Stopped",
            period="daily",
            timezone="UTC",
            cutoff_time="17:10",
            start_time="00:00",
            enabled=True,
            created_at=datetime(2026, 1, 1),
        )
        db.add(schedule)
        db.flush()
        start_at, end_at = schedule_window(schedule)
        key = brief_schedule_window_key(schedule.id, start_at, end_at)
        stopped = Job(
            kind="brief_generation",
            status="failed",
            payload={"idempotency_key": key, "period": "daily", "schedule_id": schedule.id},
            result={"stopped": True, "attempt": 1},
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(stopped)
        db.commit()
        assert brief_due(db, settings) is False

        failed = Job(
            kind="brief_generation",
            status="failed",
            payload={"idempotency_key": key, "period": "daily", "schedule_id": schedule.id},
            result={"attempt": 1},
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(failed)
        db.commit()
        assert brief_due(db, settings) is True


def test_scheduler_startup_recovers_and_workers_execute(db_factory, settings, monkeypatch):
    install_successful_services(monkeypatch)
    with db_factory() as db:
        db.add(Job(kind=FEED_SYNC_KIND, status="running", payload={}, result={}))
        db.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", db_factory)
    monkeypatch.setattr(scheduler_module, "backup_due", lambda _db, _s: False)
    monkeypatch.setattr(scheduler_module, "feed_sync_due", lambda _db, _s: False)
    monkeypatch.setattr(scheduler_module, "translation_due", lambda _db, _s: False)
    monkeypatch.setattr(scheduler_module, "brief_due", lambda _db, _s: False)
    enabled = settings.model_copy(update={"scheduler_enabled": True})

    async def scenario() -> None:
        scheduler = Scheduler(enabled)
        await scheduler.start()
        try:
            deadline = asyncio.get_event_loop().time() + 10
            while asyncio.get_event_loop().time() < deadline:
                with db_factory() as db:
                    status = db.scalar(select(Job)).status
                if status == "complete":
                    return
                await asyncio.sleep(0.1)
            raise AssertionError("worker did not complete the recovered job")
        finally:
            await scheduler.stop()

    asyncio.run(scenario())


def test_scheduler_cycle_enqueues_each_due_kind_and_is_not_blocked_by_slow_update(
    db_factory,
    settings,
    monkeypatch,
):
    update_started = threading.Event()
    release_update = threading.Event()
    operations: list[str] = []

    def slow_update(_db, _settings):
        operations.append("update-started")
        update_started.set()
        assert release_update.wait(timeout=2)
        operations.append("update-finished")

    monkeypatch.setattr(scheduler_module, "SessionLocal", db_factory)
    monkeypatch.setattr(
        scheduler_module,
        "DUE_CHECKS",
        (
            ("backup", lambda _db, _s: True),
            ("sync", lambda _db, _s: True),
            ("translation", lambda _db, _s: True),
            ("brief", lambda _db, _s: True),
        ),
    )
    monkeypatch.setattr(scheduler_module, "update_check_due", lambda _settings: True)
    monkeypatch.setattr(scheduler_module, "check_for_updates", slow_update)
    monkeypatch.setattr(scheduler_module, "enqueue_job", lambda _db, kind, reason: operations.append(f"enqueued:{kind}"))
    enabled = settings.model_copy(
        update={"scheduler_enabled": True, "update_check_enabled": True}
    )

    async def scenario() -> None:
        scheduler = Scheduler(enabled)
        await scheduler._cycle()
        assert operations[:4] == [
            "enqueued:backup",
            "enqueued:sync",
            "enqueued:translation",
            "enqueued:brief",
        ]
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
        queued = enqueue_job(db, BACKUP_KIND, reason="cli-test")
    monkeypatch.setattr(cli_module, "ready", lambda: None)
    monkeypatch.setattr(cli_module, "SessionLocal", db_factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli_app, ["run-jobs", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "processed=1" in result.output
    with db_factory() as db:
        assert db.get(Job, queued.id).status == "complete"
