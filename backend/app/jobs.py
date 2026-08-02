from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .backup import backup_once_daily
from .config import Settings, get_settings
from .briefs import create_manual_brief, run_due_schedules, schedule_window
from .models import AppSetting, Brief, BriefSchedule, Entry, Feed, Job, SyncRun, Translation, utcnow
from .sync import sync_due_feeds
from .translation import (
    LEGACY_TRANSLATION_PROVIDER,
    TRANSLATION_RECORD_PROVIDER,
    is_translation_enabled,
    translate_pending,
    translation_target,
)

MAINTENANCE_KIND = "maintenance"
SUPPORTED_KINDS = {MAINTENANCE_KIND, "sync", "translation", "brief", "backup"}


def recover_interrupted_operations(db: Session) -> dict[str, int]:
    """Make durable sub-operations left running by a process crash retryable.

    The supported deployment runs one scheduler process. At startup, a
    ``running`` sync or translation therefore belongs to the previous process
    and cannot still be making progress.
    """
    now = utcnow()
    sync_runs = list(db.scalars(select(SyncRun).where(SyncRun.status == "running")))
    feed_ids = {run.feed_id for run in sync_runs if run.feed_id is not None}
    for run in sync_runs:
        run.status = "failed"
        run.error = "Interrupted by process restart"
        run.finished_at = now
    if feed_ids:
        for feed in db.scalars(select(Feed).where(Feed.id.in_(feed_ids))):
            feed.next_fetch_at = now

    translations = list(db.scalars(select(Translation).where(Translation.status == "running")))
    for translation in translations:
        translation.status = "pending"
        translation.last_error = "Recovered after process restart"
        translation.next_retry_at = None
    db.commit()
    return {"sync_runs": len(sync_runs), "translations": len(translations)}


def maintenance_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether a maintenance pass has useful work to perform."""
    settings = settings or get_settings()
    now = utcnow()
    due_feed = db.scalar(
        select(Feed.id)
        .where(
            Feed.enabled.is_(True),
            or_(Feed.next_fetch_at.is_(None), Feed.next_fetch_at <= now),
        )
        .limit(1)
    )
    if due_feed is not None:
        return True

    if is_translation_enabled(db, settings):
        target = translation_target(db, settings)
        retryable_translation = db.scalar(
            select(Translation.id)
            .where(
                Translation.language == target,
                Translation.provider.in_(
                    [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
                ),
                or_(
                    Translation.status == "pending",
                    and_(
                        Translation.status == "failed",
                        or_(Translation.next_retry_at.is_(None), Translation.next_retry_at <= now),
                    ),
                    and_(
                        Translation.status == "running",
                        Translation.updated_at <= now - timedelta(minutes=30),
                    ),
                )
            )
            .limit(1)
        )
        missing_translation = db.scalar(
            select(Entry.id)
            .outerjoin(
                Translation,
                and_(
                    Translation.entry_id == Entry.id,
                    Translation.language == target,
                    Translation.provider.in_(
                        [TRANSLATION_RECORD_PROVIDER, LEGACY_TRANSLATION_PROVIDER]
                    ),
                ),
            )
            .where(Translation.id.is_(None))
            .limit(1)
        )
        if retryable_translation is not None or missing_translation is not None:
            return True

    for schedule in db.scalars(
        select(BriefSchedule).where(BriefSchedule.enabled.is_(True))
    ):
        start_at, end_at = schedule_window(schedule)
        if (
            db.scalar(
                select(Brief.id).where(
                    Brief.schedule_id == schedule.id,
                    Brief.start_at == start_at,
                    Brief.end_at == end_at,
                )
            )
            is None
        ):
            return True

    local_date = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    last_backup = db.get(AppSetting, "last_backup_date")
    return last_backup is None or last_backup.value != local_date


def enqueue_maintenance(
    db: Session,
    *,
    reason: str = "scheduler",
    deduplicate: bool = True,
) -> Job:
    """Queue one maintenance pass, avoiding duplicate queued/running passes."""
    if deduplicate:
        existing = db.scalar(
            select(Job)
            .where(
                Job.kind == MAINTENANCE_KIND,
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.id)
            .limit(1)
        )
        if existing is not None:
            return existing
    job = Job(
        kind=MAINTENANCE_KIND,
        status="queued",
        payload={"reason": reason},
        result={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def recover_interrupted_jobs(db: Session) -> int:
    """Return jobs left running by a process crash to the durable queue."""
    rows = list(db.scalars(select(Job).where(Job.status == "running").order_by(Job.id)))
    for job in rows:
        payload = dict(job.payload or {})
        payload["recovered"] = True
        payload["recovered_at"] = utcnow().isoformat() + "Z"
        job.payload = payload
        if job.kind == "brief_generation":
            # Interactive brief generation is resumed explicitly through its
            # checkpoint endpoint. Generic queue execution does not have the
            # owner's HTTP request context.
            job.status = "failed"
            job.finished_at = utcnow()
            job.error = "Generation was interrupted; retry from the saved checkpoint."
        else:
            job.status = "queued"
            job.started_at = None
            job.finished_at = None
            job.error = None
    db.commit()
    return len(rows)


def _sync_summary(runs: list[Any]) -> dict[str, int]:
    return {
        "processed": len(runs),
        "success": sum(run.status == "success" for run in runs),
        "not_modified": sum(run.status == "not_modified" for run in runs),
        "failed": sum(run.status == "failed" for run in runs),
        "fetched": sum(int(run.fetched_count or 0) for run in runs),
        "created": sum(int(run.created_count or 0) for run in runs),
        "updated": sum(int(run.updated_count or 0) for run in runs),
    }


def _translation_summary(rows: list[Any]) -> dict[str, int]:
    return {
        "processed": len(rows),
        "complete": sum(row.status == "complete" for row in rows),
        "failed": sum(row.status == "failed" for row in rows),
        "pending": sum(row.status in {"pending", "running"} for row in rows),
    }


def _record_result(db: Session, job: Job, key: str, value: Any) -> None:
    result = dict(job.result or {})
    result[key] = value
    job.result = result
    db.commit()


def _execute_maintenance(db: Session, job: Job, settings: Settings) -> None:
    def backup_phase() -> dict[str, Any]:
        path = backup_once_daily(db, settings)
        return {"created": path is not None, "path": str(path) if path else None}

    def sync_phase() -> dict[str, Any]:
        return _sync_summary(sync_due_feeds(db, settings))

    def translation_phase() -> dict[str, Any]:
        return _translation_summary(
            translate_pending(db, limit=10, retry_failed=True)
        )

    def brief_phase() -> dict[str, Any]:
        briefs = run_due_schedules(db)
        return {"created": len(briefs), "ids": [brief.id for brief in briefs]}

    # Backups are attempted first so optional network/LLM work cannot starve
    # the daily safety copy. Each phase is isolated so one failure does not
    # prevent the remaining maintenance work from running.
    phases: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("backup", backup_phase),
        ("sync", sync_phase),
        ("translation", translation_phase),
        ("brief", brief_phase),
    ]
    failures: list[tuple[str, Exception]] = []
    for key, execute in phases:
        try:
            result = execute()
        except Exception as exc:
            db.rollback()
            result = {
                "error": str(exc)[:4000],
                "error_type": exc.__class__.__name__,
            }
            failures.append((key, exc))
        _record_result(db, job, key, result)

    if failures:
        summary = "; ".join(f"{key}: {exc}" for key, exc in failures)
        raise RuntimeError(f"Maintenance phases failed: {summary}")


def _execute_single_kind(db: Session, job: Job, settings: Settings) -> None:
    payload = dict(job.payload or {})
    if job.kind == "sync":
        runs = sync_due_feeds(db, settings, feed_id=payload.get("feed_id"))
        _record_result(db, job, "sync", _sync_summary(runs))
    elif job.kind == "translation":
        rows = translate_pending(
            db,
            limit=max(1, min(int(payload.get("limit", 20)), 1000)),
            retry_failed=bool(payload.get("retry_failed", True)),
        )
        _record_result(db, job, "translation", _translation_summary(rows))
    elif job.kind == "brief":
        at_value = payload.get("at")
        at = datetime.fromisoformat(at_value) if at_value else None
        brief = create_manual_brief(
            db,
            str(payload.get("period", "daily")),
            at=at,
            filters=dict(payload.get("filters") or {}),
            idempotency_key=str(payload["idempotency_key"]),
            settings=settings,
        )
        _record_result(db, job, "brief", {"created": 1, "ids": [brief.id]})
    elif job.kind == "backup":
        path = backup_once_daily(db, settings)
        _record_result(
            db,
            job,
            "backup",
            {"created": path is not None, "path": str(path) if path else None},
        )
    else:
        raise ValueError(f"Unsupported job kind: {job.kind}")


def run_job(db: Session, job_id: int, settings: Settings | None = None) -> Job | None:
    """Atomically claim and execute one queued job."""
    settings = settings or get_settings()
    claimed = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "queued")
        .values(
            status="running",
            started_at=utcnow(),
            finished_at=None,
            error=None,
            result={},
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    job = db.get(Job, job_id)
    assert job is not None
    try:
        if job.kind == MAINTENANCE_KIND:
            _execute_maintenance(db, job, settings)
        else:
            _execute_single_kind(db, job, settings)
        job.status = "complete"
        job.finished_at = utcnow()
        job.error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(Job, job_id)
        assert job is not None
        job.status = "failed"
        job.finished_at = utcnow()
        job.error = str(exc)[:4000]
        db.commit()
    return job


def run_queued_jobs(
    db: Session,
    settings: Settings | None = None,
    *,
    limit: int | None = None,
) -> list[Job]:
    """Drain queued jobs in creation order, up to an optional limit."""
    completed: list[Job] = []
    while limit is None or len(completed) < limit:
        job_id = db.scalar(
            select(Job.id)
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )
        if job_id is None:
            break
        job = run_job(db, job_id, settings)
        if job is not None:
            completed.append(job)
    return completed
