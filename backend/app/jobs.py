from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .auto_tag import auto_tag_due, auto_tag_pending
from .backup import backup_once_daily
from .config import Settings, get_settings
from .briefs import brief_schedule_window_key, run_due_schedules, schedule_window
from .models import AppSetting, AutoTagRecord, Brief, BriefSchedule, Entry, Feed, Job, SyncRun, Translation, utcnow
from .sync import sync_due_feeds
from .translation import (
    LEGACY_TRANSLATION_PROVIDER,
    TRANSLATION_RECORD_PROVIDER,
    is_translation_enabled,
    translate_pending,
    translation_target,
)

MAINTENANCE_KIND = "maintenance"
FEED_SYNC_KIND = "sync"
TRANSLATION_KIND = "translation"
BRIEF_KIND = "brief"
BACKUP_KIND = "backup"
AUTO_TAG_KIND = "auto_tag"
SUPPORTED_KINDS = {FEED_SYNC_KIND, TRANSLATION_KIND, BRIEF_KIND, BACKUP_KIND, AUTO_TAG_KIND}


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


def feed_sync_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether any enabled feed is ready to be polled."""
    now = utcnow()
    return (
        db.scalar(
            select(Feed.id)
            .where(
                Feed.enabled.is_(True),
                or_(Feed.next_fetch_at.is_(None), Feed.next_fetch_at <= now),
            )
            .limit(1)
        )
        is not None
    )


def translation_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether translation has pending or retryable work."""
    settings = settings or get_settings()
    if not is_translation_enabled(db, settings):
        return False
    now = utcnow()
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
    if retryable_translation is not None:
        return True
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
    return missing_translation is not None


def brief_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether any enabled schedule is missing its current brief.

    There is no historical backfill: a window that completed before the
    schedule was created is never due, and a window whose generation was
    explicitly stopped by the owner is not due either (the owner resumes,
    restarts, or reruns it manually).
    """
    for schedule in db.scalars(
        select(BriefSchedule).where(BriefSchedule.enabled.is_(True))
    ):
        start_at, end_at = schedule_window(schedule)
        if end_at <= schedule.created_at:
            continue
        if (
            db.scalar(
                select(Brief.id).where(
                    Brief.schedule_id == schedule.id,
                    Brief.start_at == start_at,
                    Brief.end_at == end_at,
                )
            )
            is not None
        ):
            continue
        key = brief_schedule_window_key(schedule.id, start_at, end_at)
        stopped_job = db.scalar(
            select(Job.id)
            .where(
                Job.kind == "brief_generation",
                Job.payload["idempotency_key"].as_string() == key,
                Job.status == "failed",
            )
            .order_by(Job.id.desc())
            .limit(1)
        )
        if stopped_job is not None:
            job = db.get(Job, stopped_job)
            if (job.result or {}).get("stopped"):
                continue
        return True
    return False


def backup_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether today's safety backup has not been created yet."""
    settings = settings or get_settings()
    local_date = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    last_backup = db.get(AppSetting, "last_backup_date")
    return last_backup is None or last_backup.value != local_date


def maintenance_due(db: Session, settings: Settings | None = None) -> bool:
    """Return whether any background kind has useful work to perform."""
    return any(
        (
            feed_sync_due(db, settings),
            translation_due(db, settings),
            brief_due(db, settings),
            backup_due(db, settings),
            auto_tag_due(db, settings),
        )
    )


def enqueue_job(
    db: Session,
    kind: str,
    payload: dict | None = None,
    *,
    reason: str | None = None,
) -> Job:
    """Queue one background job, avoiding duplicate queued/running work of the same kind."""
    existing = db.scalar(
        select(Job)
        .where(
            Job.kind == kind,
            Job.status.in_(("queued", "running")),
        )
        .order_by(Job.id)
        .limit(1)
    )
    if existing is not None:
        return existing
    payload = dict(payload or {})
    if reason:
        payload["reason"] = reason
    job = Job(
        kind=kind,
        status="queued",
        payload=payload,
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
        if job.kind == MAINTENANCE_KIND:
            # The monolithic maintenance pass was replaced by per-kind
            # background workers; a half-finished legacy pass cannot resume.
            job.status = "failed"
            job.finished_at = utcnow()
            job.error = "Superseded by per-kind background job workers"
        elif job.kind == "brief_generation":
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
    queued_legacy = list(
        db.scalars(
            select(Job).where(
                Job.kind == MAINTENANCE_KIND,
                Job.status == "queued",
            )
        )
    )
    for job in queued_legacy:
        job.status = "failed"
        job.finished_at = utcnow()
        job.error = "Superseded by per-kind background job workers"
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
        briefs = run_due_schedules(db)
        _record_result(
            db,
            job,
            "brief",
            {"created": len(briefs), "ids": [brief.id for brief in briefs]},
        )
    elif job.kind == "backup":
        path = backup_once_daily(db, settings)
        _record_result(
            db,
            job,
            "backup",
            {"created": path is not None, "path": str(path) if path else None},
        )
    elif job.kind == "auto_tag":
        rows = auto_tag_pending(
            db,
            limit=max(1, min(int(payload.get("limit", 10)), 100)),
            retry_failed=bool(payload.get("retry_failed", True)),
        )
        _record_result(
            db,
            job,
            "auto_tag",
            {
                "processed": len(rows),
                "complete": sum(row.status == "complete" for row in rows),
                "failed": sum(row.status == "failed" for row in rows),
            },
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


def claim_next_job(db: Session, kind: str, settings: Settings | None = None) -> Job | None:
    """Claim and run the oldest queued job of one kind, or return None."""
    job_id = db.scalar(
        select(Job.id)
        .where(Job.kind == kind, Job.status == "queued")
        .order_by(Job.created_at, Job.id)
        .limit(1)
    )
    if job_id is None:
        return None
    return run_job(db, job_id, settings)


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
