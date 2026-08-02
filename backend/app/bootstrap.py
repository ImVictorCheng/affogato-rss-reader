from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .db import SessionLocal
from .models import Owner
from .security import hash_password


INITIAL_OWNER_PASSWORD_FILENAME = "initial-owner-password.txt"


def initial_owner_password_path(settings: Settings) -> Path:
    return settings.data_dir / INITIAL_OWNER_PASSWORD_FILENAME


def _initial_password_file(settings: Settings) -> tuple[Path, str]:
    path = initial_owner_password_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) < 8:
            raise RuntimeError(f"The initial owner password file is invalid: {path}")
        return path, existing
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(password + "\n")
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, password


def ensure_initial_owner(
    settings: Settings,
    session_factory: Callable[[], Session] = SessionLocal,
) -> str | None:
    """Create a pending owner and return its new one-time password once."""

    if settings.auth_mode != "owner":
        return None
    with session_factory() as db:
        owner = db.scalar(select(Owner).limit(1))
        if owner is not None:
            if not owner.activation_required:
                initial_owner_password_path(settings).unlink(missing_ok=True)
            return None
        _path, password = _initial_password_file(settings)
        db.add(
            Owner(
                id=1,
                username="owner",
                password_hash=hash_password(password),
                activation_required=True,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Concurrent startup processes share the O_EXCL-created password file;
            # only one owner row wins, and both therefore reference the same secret.
            db.rollback()
            return None
        return password


def read_initial_owner_password(settings: Settings) -> str:
    path = initial_owner_password_path(settings)
    try:
        password = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError("No pending initial owner password is available") from exc
    if len(password) < 8:
        raise RuntimeError(f"The initial owner password file is invalid: {path}")
    return password


def remove_initial_owner_password(settings: Settings) -> None:
    initial_owner_password_path(settings).unlink(missing_ok=True)
