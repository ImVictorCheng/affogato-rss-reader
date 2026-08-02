from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from .config import Settings
from .db import SessionLocal
from .jobs import (
    enqueue_maintenance,
    maintenance_due,
    recover_interrupted_jobs,
    recover_interrupted_operations,
    run_queued_jobs,
)
from .updates import check_for_updates, update_check_due

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.task: asyncio.Task[None] | None = None
        self.update_task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.task is None or self.task.done():
            self.stop_event = asyncio.Event()
            self.task = asyncio.create_task(self._run(), name="affogato-rss-reader-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
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
        def maintenance_work() -> None:
            with SessionLocal() as db:
                if maintenance_due(db, self.settings):
                    enqueue_maintenance(db, reason="scheduler")
                run_queued_jobs(db, self.settings)

        if self.settings.scheduler_enabled:
            await asyncio.to_thread(maintenance_work)
        self._launch_update_check(force=False)

    async def _startup(self) -> None:
        def maintenance_work() -> None:
            with SessionLocal() as db:
                operations = recover_interrupted_operations(db)
                recover_interrupted_jobs(db)
                if self.settings.sync_on_startup or any(operations.values()):
                    enqueue_maintenance(db, reason="startup")
                run_queued_jobs(db, self.settings)

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
