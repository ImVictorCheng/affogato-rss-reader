from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress

from .auto_tag import auto_tag_due
from .config import Settings
from .db import SessionLocal
from .jobs import (
    backup_due,
    brief_due,
    claim_next_job,
    enqueue_job,
    feed_sync_due,
    recover_interrupted_jobs,
    recover_interrupted_operations,
    translation_due,
)
from .updates import check_for_updates, update_check_due

logger = logging.getLogger(__name__)

# One long-lived worker per background kind. A slow or hanging worker only
# delays its own kind: brief generation is never blocked behind a stalled
# translation pass, and vice versa.
WORKER_KINDS = ("backup", "sync", "translation", "brief", "auto_tag")
DUE_CHECKS: tuple[tuple[str, object], ...] = (
    ("backup", backup_due),
    ("sync", feed_sync_due),
    ("translation", translation_due),
    ("brief", brief_due),
    ("auto_tag", auto_tag_due),
)


class Scheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.task: asyncio.Task[None] | None = None
        self.update_task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self._worker_stop = threading.Event()
        self.workers: dict[str, threading.Thread] = {}

    async def start(self) -> None:
        if self.task is None or self.task.done():
            self.stop_event = asyncio.Event()
            self._worker_stop = threading.Event()
            self._spawn_workers()
            self.task = asyncio.create_task(self._run(), name="affogato-rss-reader-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        self._worker_stop.set()
        for thread in self.workers.values():
            thread.join(timeout=5)
        self.workers.clear()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.update_task:
            self.update_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.update_task
            self.update_task = None

    def _spawn_workers(self) -> None:
        for kind in WORKER_KINDS:
            thread = threading.Thread(
                target=self._worker_loop,
                args=(kind,),
                name=f"affogato-worker-{kind}",
                daemon=True,
            )
            thread.start()
            self.workers[kind] = thread

    def _worker_loop(self, kind: str) -> None:
        while not self._worker_stop.is_set():
            try:
                with SessionLocal() as db:
                    job = claim_next_job(db, kind, self.settings)
                if job is not None:
                    continue
            except Exception:
                logger.exception("Background worker %s failed", kind)
            self._worker_stop.wait(1.0)

    def _launch_update_check(self, *, force: bool) -> None:
        if not self.settings.update_check_enabled:
            return
        if self.update_task is not None and not self.update_task.done():
            return

        def update_work() -> None:
            with SessionLocal() as db:
                if force or update_check_due(self.settings):
                    check_for_updates(db, self.settings)

        async def run_update() -> None:
            try:
                await asyncio.to_thread(update_work)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s update check failed", "Startup" if force else "Scheduled")

        self.update_task = asyncio.create_task(
            run_update(),
            name="affogato-rss-reader-update-check",
        )

    async def _cycle(self) -> None:
        def due_work() -> None:
            with SessionLocal() as db:
                for kind, due in DUE_CHECKS:
                    try:
                        if due(db, self.settings):
                            enqueue_job(db, kind, reason="scheduler")
                    except Exception:
                        logger.exception("Due check for %s failed", kind)

        if self.settings.scheduler_enabled:
            await asyncio.to_thread(due_work)
        self._launch_update_check(force=False)

    async def _startup(self) -> None:
        def maintenance_work() -> None:
            with SessionLocal() as db:
                operations = recover_interrupted_operations(db)
                recover_interrupted_jobs(db)
                for kind, due in DUE_CHECKS:
                    try:
                        if due(db, self.settings):
                            enqueue_job(db, kind, reason="startup")
                    except Exception:
                        logger.exception("Due check for %s failed", kind)
                if self.settings.sync_on_startup:
                    enqueue_job(db, "sync", reason="startup")
                if any(operations.values()):
                    logger.info(
                        "Recovered interrupted operations: %s", operations
                    )

        if self.settings.scheduler_enabled:
            await asyncio.to_thread(maintenance_work)
        self._launch_update_check(force=True)

    async def _run(self) -> None:
        try:
            await self._startup()
        except Exception:
            logger.exception("Job recovery/startup cycle failed")
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                try:
                    await self._cycle()
                except Exception:
                    logger.exception("Scheduler cycle failed")
