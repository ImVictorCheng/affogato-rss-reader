from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from .config import Settings, get_settings

CallCategory = Literal["llm", "translation"]
CallStatus = Literal["success", "error"]

_log_lock = Lock()
logger = logging.getLogger(__name__)


def _restrict_mode(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        # Windows ACLs and some mounted filesystems do not expose POSIX modes.
        pass


def prepare_call_log_path(settings: Settings | None = None) -> Path:
    """Create and tighten the private call-log location before it is used."""

    settings = settings or get_settings()
    path = settings.effective_call_log_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_mode(path.parent, 0o700)
    for index in range(settings.call_log_backups + 1):
        candidate = path if index == 0 else path.with_name(f"{path.name}.{index}")
        if candidate.is_file():
            _restrict_mode(candidate, 0o600)
    return path


def _rotate_logs(path: Path, *, max_bytes: int, backup_count: int) -> None:
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    if backup_count == 0:
        path.unlink()
        return
    oldest = path.with_name(f"{path.name}.{backup_count}")
    if oldest.exists():
        oldest.unlink()
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def write_call_log(
    *,
    category: CallCategory,
    operation: str,
    status: CallStatus,
    duration_ms: int,
    input_chars: int = 0,
    output_chars: int = 0,
    feature: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    connection_id: int | None = None,
    connection_name: str | None = None,
    target_language: str | None = None,
    cached: bool = False,
    error: str | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    record = {
        "id": uuid4().hex,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "category": category,
        "operation": operation,
        "feature": feature,
        "provider": provider,
        "model": model,
        "connection_id": connection_id,
        "connection_name": connection_name,
        "target_language": target_language,
        "status": status,
        "duration_ms": max(0, int(duration_ms)),
        "input_chars": max(0, int(input_chars)),
        "output_chars": max(0, int(output_chars)),
        "cached": bool(cached),
        "error": str(error)[:2000] if error else None,
    }
    with _log_lock:
        path = prepare_call_log_path(settings)
        _rotate_logs(
            path,
            max_bytes=settings.call_log_max_bytes,
            backup_count=settings.call_log_backups,
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        _restrict_mode(path, 0o600)
    return record


def safe_write_call_log(**kwargs) -> dict | None:
    try:
        return write_call_log(**kwargs)
    except OSError as exc:
        logger.warning("Unable to write the LLM/translation call log: %s", exc)
        return None


def _reverse_log_lines(path: Path):
    block_size = 64 * 1024
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(block_size, position)
            position -= size
            handle.seek(position)
            parts = (handle.read(size) + remainder).split(b"\n")
            remainder = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line
        if remainder:
            yield remainder


def read_call_logs(
    *,
    limit: int = 200,
    category: CallCategory | None = None,
    status: CallStatus | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    settings = settings or get_settings()
    path = prepare_call_log_path(settings)
    if not path.is_file():
        return []
    records: list[dict] = []
    with _log_lock:
        for line in _reverse_log_lines(path):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                continue
            if category and record.get("category") != category:
                continue
            if status and record.get("status") != status:
                continue
            records.append(record)
            if len(records) >= limit:
                break
    return records
