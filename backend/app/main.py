from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .bootstrap import ensure_initial_owner, initial_owner_password_path
from .config import get_settings
from .db import init_database
from .scheduler import Scheduler


settings = get_settings()
scheduler = Scheduler(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database(settings=settings)
    initial_password = ensure_initial_owner(settings)
    if initial_password:
        logger.warning("INITIAL OWNER PASSWORD: %s", initial_password)
        logger.warning(
            "Use this password once to activate the owner, or retrieve it with "
            "`affogato-rss-reader initial-password`. It is also stored at %s until activation.",
            initial_owner_password_path(settings),
        )
    background_enabled = settings.scheduler_enabled or settings.update_check_enabled
    if background_enabled:
        await scheduler.start()
    try:
        yield
    finally:
        if background_enabled:
            await scheduler.stop()


app = FastAPI(
    title=f"{settings.app_name} API",
    version=settings.version,
    description=(
        "Self-hosted bilingual RSS/Atom reader with domain spaces, "
        "cross-domain views, optional translation, and deterministic briefs."
    ),
    lifespan=lifespan,
)
app.include_router(router, prefix=settings.api_prefix)

assets = settings.static_dir / "assets"
if assets.is_dir():
    app.mount("/assets", StaticFiles(directory=assets), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str, request: Request):
    if path.startswith(settings.api_prefix.strip("/") + "/"):
        raise HTTPException(status_code=404, detail="API route not found")
    candidate = (settings.static_dir / path).resolve()
    static_root = settings.static_dir.resolve()
    if candidate.is_file() and static_root in candidate.parents:
        return FileResponse(candidate)
    index = settings.static_dir / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend has not been built")
