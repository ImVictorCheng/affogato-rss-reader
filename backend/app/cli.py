from __future__ import annotations

import getpass
import uuid
from datetime import datetime
from pathlib import Path

import typer
from sqlalchemy import select

from .backup import backup_database
from .bootstrap import ensure_initial_owner, read_initial_owner_password
from .briefs import brief_markdown, create_manual_brief, run_due_schedules
from .config import get_settings
from .db import SessionLocal, init_database
from .jobs import (
    enqueue_maintenance,
    recover_interrupted_jobs,
    recover_interrupted_operations,
    run_queued_jobs,
)
from .llm import rotate_encrypted_secrets
from .models import Brief, Feed, Job, Owner
from .opml import export_opml_document, import_opml_document
from .security import hash_password
from .sync import sync_feed
from .translation import translate_pending

app = typer.Typer(help="Affogato RSS Reader maintenance CLI.", no_args_is_help=True)
brief_app = typer.Typer(help="Generate, schedule, and export briefs.", no_args_is_help=True)
opml_app = typer.Typer(help="Import and export subscriptions.", no_args_is_help=True)
secrets_app = typer.Typer(help="Manage encrypted application secrets.", no_args_is_help=True)
app.add_typer(brief_app, name="brief")
app.add_typer(opml_app, name="opml")
app.add_typer(secrets_app, name="secrets")


def ready() -> None:
    init_database(settings=get_settings())


@app.command("initial-password")
def initial_password() -> None:
    """Print the pending one-time owner password."""
    settings = get_settings()
    if settings.auth_mode != "owner":
        raise typer.BadParameter("Initial owner passwords are disabled in no-auth mode")
    ready()
    ensure_initial_owner(settings)
    try:
        typer.echo(read_initial_owner_password(settings))
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("create-owner")
def create_owner(
    password: str | None = typer.Option(None, help="Owner password; omit to prompt securely."),
) -> None:
    """Create the single owner account without the web setup screen."""
    settings = get_settings()
    if settings.auth_mode == "none":
        raise typer.BadParameter("Owner creation is disabled when auth mode is none")
    ready()
    with SessionLocal() as db:
        if db.scalar(select(Owner.id).limit(1)) is not None:
            raise typer.BadParameter("An owner already exists")
        if password is None:
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise typer.BadParameter("Passwords do not match")
        try:
            encoded = hash_password(password)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        db.add(Owner(id=1, username="owner", password_hash=encoded))
        db.commit()
    typer.echo("Owner created.")


@app.command()
def sync(
    feed_id: int | None = typer.Option(None, help="Sync one feed; default syncs all enabled feeds."),
) -> None:
    ready()
    with SessionLocal() as db:
        if feed_id is not None:
            feed = db.get(Feed, feed_id)
            if feed is None:
                raise typer.BadParameter(f"Feed {feed_id} does not exist")
            runs = [sync_feed(db, feed)]
        else:
            feeds = list(db.scalars(select(Feed).where(Feed.enabled.is_(True)).order_by(Feed.id)))
            runs = [sync_feed(db, feed) for feed in feeds]
        for run in runs:
            typer.echo(
                f"feed={run.feed_id} status={run.status} fetched={run.fetched_count} "
                f"created={run.created_count} updated={run.updated_count}"
            )


@app.command()
def translate(
    limit: int = typer.Option(20, min=1, max=1000),
    retry_failed: bool = typer.Option(False),
    target: str | None = typer.Option(None, help="BCP-47 target language."),
) -> None:
    ready()
    with SessionLocal() as db:
        rows = translate_pending(
            db, limit=limit, retry_failed=retry_failed, target=target
        )
    typer.echo(
        f"processed={len(rows)} "
        f"complete={sum(row.status == 'complete' for row in rows)} "
        f"failed={sum(row.status == 'failed' for row in rows)}"
    )


@brief_app.command("generate")
def brief_generate(
    period: str = typer.Option("daily"),
    at: str | None = typer.Option(None, help="ISO timestamp selecting the completed window."),
    idempotency_key: str | None = typer.Option(None),
) -> None:
    ready()
    try:
        parsed_at = datetime.fromisoformat(at) if at else None
    except ValueError as exc:
        raise typer.BadParameter("--at must be an ISO timestamp") from exc
    with SessionLocal() as db:
        row = create_manual_brief(
            db,
            period,
            at=parsed_at,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        typer.echo(f"brief={row.id} period={row.period} start={row.start_at} end={row.end_at}")


@brief_app.command("run-due")
def brief_run_due() -> None:
    ready()
    with SessionLocal() as db:
        rows = run_due_schedules(db)
    typer.echo(f"briefs={len(rows)} ids={','.join(str(row.id) for row in rows)}")


@brief_app.command("export")
def brief_export(
    brief_id: int = typer.Option(...),
    output: Path | None = typer.Option(None),
) -> None:
    ready()
    with SessionLocal() as db:
        row = db.get(Brief, brief_id)
        if row is None:
            raise typer.BadParameter(f"Brief {brief_id} does not exist")
        markdown = brief_markdown(db, row)
    if output is None:
        typer.echo(markdown)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8", newline="\n")
        typer.echo(str(output))


@opml_app.command("export")
def opml_export(output: Path = typer.Option(...)) -> None:
    ready()
    with SessionLocal() as db:
        payload = export_opml_document(db)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    typer.echo(str(output))


@opml_app.command("import")
def opml_import(path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    ready()
    with SessionLocal() as db:
        result = import_opml_document(db, path.read_bytes(), get_settings())
    typer.echo(f"imported={result['imported']} skipped={result['skipped']}")


@app.command()
def backup(output: Path | None = typer.Option(None, help="Destination .db file.")) -> None:
    ready()
    typer.echo(str(backup_database(output=output)))


@secrets_app.command("rotate")
def secrets_rotate() -> None:
    """Re-encrypt saved API keys with the configured active master key."""

    ready()
    with SessionLocal() as db:
        count = rotate_encrypted_secrets(db, get_settings())
        db.commit()
    typer.echo(f"rotated={count}")


@app.command("jobs")
def list_jobs(
    status: str | None = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=500),
) -> None:
    ready()
    with SessionLocal() as db:
        query = select(Job)
        if status:
            query = query.where(Job.status == status)
        rows = list(db.scalars(query.order_by(Job.created_at.desc()).limit(limit)))
    for job in rows:
        typer.echo(
            f"id={job.id} kind={job.kind} status={job.status} "
            f"created={job.created_at.isoformat()} error={job.error or '-'}"
        )


@app.command("run-jobs")
def run_jobs(
    limit: int = typer.Option(100, min=1, max=1000),
    enqueue: bool = typer.Option(False, "--enqueue-maintenance"),
) -> None:
    ready()
    with SessionLocal() as db:
        operations = recover_interrupted_operations(db)
        recovered = recover_interrupted_jobs(db)
        if enqueue:
            enqueue_maintenance(db, reason="cli")
        rows = run_queued_jobs(db, get_settings(), limit=limit)
    typer.echo(
        f"recovered={recovered} recovered_syncs={operations['sync_runs']} "
        f"recovered_translations={operations['translations']} processed={len(rows)}"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
