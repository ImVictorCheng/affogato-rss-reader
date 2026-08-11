"""Automatic LLM tagging of collected article summaries.

The owner enables the feature manually and chooses between two modes:

- ``existing_only``: the LLM picks tags only from the current tag library.
- ``create_new``: the LLM may also invent new tags, which are added to the
  library (controlled by a secondary switch).

The LLM attaches at most ``MAX_AUTO_TAGS_PER_ENTRY`` tags per article; the
owner can always attach more tags by hand — auto-attached tags are tracked in
``AutoTagRecord.tag_ids`` and are the only ones the feature ever removes.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .llm import (
    AUTO_TAG_FEATURE,
    LLMConnectionError,
    bind_llm_connection,
    complete_feature_chat,
    get_feature_connection,
    get_llm_connection,
)
from .models import AppSetting, AutoTagRecord, Entry, EntryTag, Tag, utcnow
from .schemas import TagCreate

MAX_AUTO_TAGS_PER_ENTRY = 5
AUTO_TAG_LLM_TIMEOUT_SECONDS = 30.0
AUTO_TAG_RETRY_MINUTES = 5
STALE_RUNNING_MINUTES = 30


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def is_auto_tag_enabled(db: Session, settings=None) -> bool:
    del settings
    row = db.get(AppSetting, "auto_tag_enabled")
    return bool(row and row.value.lower() == "true")


def auto_tag_create_new(db: Session) -> bool:
    row = db.get(AppSetting, "auto_tag_create_new")
    return bool(row and row.value.lower() == "true")


def configure_auto_tag(
    db: Session,
    *,
    enabled: bool,
    create_new: bool,
    llm_connection_id: int | None = None,
) -> None:
    _set_setting(db, "auto_tag_enabled", "true" if enabled else "false")
    _set_setting(db, "auto_tag_create_new", "true" if create_new else "false")
    if llm_connection_id is not None:
        requested = get_llm_connection(db, llm_connection_id)
        if requested is None:
            raise ValueError("LLM connection not found")
        bind_llm_connection(db, feature_key=AUTO_TAG_FEATURE, connection=requested)
    if enabled and get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        raise ValueError("Select an existing LLM connection for auto tagging")
    if enabled:
        ensure_auto_tag_queue(db)
    db.commit()


def ensure_auto_tag_queue(db: Session) -> int:
    existing_ids = {row[0] for row in db.execute(select(AutoTagRecord.entry_id))}
    entries = list(
        db.scalars(
            select(Entry)
            .where(Entry.id.not_in(existing_ids))
            .order_by(Entry.id)
        )
    )
    for entry in entries:
        db.add(
            AutoTagRecord(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status="pending",
                tag_ids=[],
            )
        )
    if entries:
        db.flush()
    return len(entries)


def _entry_text(entry: Entry, max_chars: int = 4000) -> str:
    parts = [f"Title: {entry.title}"]
    if entry.summary:
        parts.append(f"Summary: {entry.summary[:max_chars]}")
    if entry.authors:
        parts.append(f"Authors: {', '.join(entry.authors[:8])}")
    return "\n".join(parts)


def _build_prompt(entry: Entry, existing_tags: list[Tag], create_new: bool) -> tuple[str, str]:
    tag_list = "\n".join(f"- {tag.name}" for tag in existing_tags) or "(empty)"
    system = (
        "You are an article tagging assistant. Based on the article title and "
        "summary, choose 1 to 5 concise, accurate tags. Output ONLY a JSON "
        "array of tag name strings, for example [\"quantum computing\", "
        "\"machine learning\"]. Do not output anything else."
    )
    rule = (
        "Rule: you may only pick tags from the existing tag library above; do "
        "not invent new tags."
        if not create_new
        else (
            "Rule: prefer tags from the existing tag library above; you may "
            "also invent new tags when no existing tag is precise enough. "
            "New tags will be added to the library."
        )
    )
    user = f"{_entry_text(entry)}\n\nExisting tag library:\n{tag_list}\n\n{rule}"
    return system, user


def _parse_tag_names(payload: str) -> list[str]:
    text = payload.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        values = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    names: list[str] = []
    normalized_names: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            name = TagCreate(name=value).name.strip()
        except ValidationError:
            continue
        normalized = name.lower()
        if name and normalized not in normalized_names:
            names.append(name)
            normalized_names.add(normalized)
    return names[:MAX_AUTO_TAGS_PER_ENTRY]


def tag_entry(db: Session, record: AutoTagRecord) -> AutoTagRecord:
    entry = db.get(Entry, record.entry_id)
    if entry is None:
        record.status = "failed"
        record.last_error = "Entry no longer exists"
        record.next_retry_at = None
        db.commit()
        return record
    record.source_hash = entry.source_hash
    record.attempts += 1
    record.status = "running"
    record.last_error = None
    db.commit()
    existing_tags = list(db.scalars(select(Tag).order_by(Tag.id)))
    system_prompt, user_prompt = _build_prompt(
        entry, existing_tags, auto_tag_create_new(db)
    )
    try:
        payload = complete_feature_chat(
            db,
            feature_key=AUTO_TAG_FEATURE,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=get_settings(),
            timeout_seconds=AUTO_TAG_LLM_TIMEOUT_SECONDS,
        )
    except (LLMConnectionError, ValueError) as exc:
        record.status = "failed"
        record.last_error = str(exc)[:4000]
        record.next_retry_at = utcnow() + timedelta(minutes=AUTO_TAG_RETRY_MINUTES)
        db.commit()
        return record

    names = _parse_tag_names(payload)
    by_name = {tag.name.lower(): tag for tag in existing_tags}
    chosen: list[Tag] = []
    for name in names:
        normalized_name = name.lower()
        tag = by_name.get(normalized_name)
        if tag is None:
            if not auto_tag_create_new(db):
                continue
            tag = db.scalar(
                select(Tag).where(func.lower(Tag.name) == normalized_name)
            )
            if tag is None:
                tag = Tag(name=name)
                db.add(tag)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    tag = db.scalar(
                        select(Tag).where(func.lower(Tag.name) == normalized_name)
                    )
                    if tag is None:
                        raise
            by_name[normalized_name] = tag
        if tag.id not in {item.id for item in chosen}:
            chosen.append(tag)

    previous_ids = set(record.tag_ids or [])
    chosen_ids = {tag.id for tag in chosen}
    stale_ids = previous_ids - chosen_ids
    if stale_ids:
        db.execute(
            delete(EntryTag).where(
                EntryTag.entry_id == entry.id,
                EntryTag.tag_id.in_(stale_ids),
            )
        )
    for tag in chosen:
        exists = db.scalar(
            select(EntryTag.id).where(
                EntryTag.entry_id == entry.id,
                EntryTag.tag_id == tag.id,
            )
        )
        if exists is None:
            db.add(EntryTag(entry_id=entry.id, tag_id=tag.id))

    record.tag_ids = list(chosen_ids)
    record.status = "complete"
    record.last_error = None
    record.next_retry_at = None
    db.commit()
    return record


def auto_tag_pending(
    db: Session,
    *,
    limit: int = 10,
    retry_failed: bool = False,
) -> list[AutoTagRecord]:
    if not is_auto_tag_enabled(db) or get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        return []
    ensure_auto_tag_queue(db)
    db.commit()
    stale_running = utcnow() - timedelta(minutes=STALE_RUNNING_MINUTES)
    statuses = ["pending"] + (["failed"] if retry_failed else [])
    rows = list(
        db.scalars(
            select(AutoTagRecord)
            .where(
                or_(
                    AutoTagRecord.status.in_(statuses),
                    and_(
                        AutoTagRecord.status == "running",
                        AutoTagRecord.updated_at <= stale_running,
                    ),
                ),
                or_(
                    AutoTagRecord.next_retry_at.is_(None),
                    AutoTagRecord.next_retry_at <= utcnow(),
                ),
            )
            .order_by(AutoTagRecord.updated_at, AutoTagRecord.id)
            .limit(limit)
        )
    )
    for row in rows:
        if row.status == "running":
            row.status = "pending"
            row.last_error = "Recovered an interrupted auto-tagging run"
    db.commit()
    return [tag_entry(db, row) for row in rows]


def auto_tag_due(db: Session, settings=None) -> bool:
    if not is_auto_tag_enabled(db, settings):
        return False
    if get_feature_connection(db, AUTO_TAG_FEATURE) is None:
        return False
    now = utcnow()
    stale_running = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    retryable = db.scalar(
        select(AutoTagRecord.id)
        .where(
            AutoTagRecord.status.in_(("pending", "failed")),
            or_(
                AutoTagRecord.next_retry_at.is_(None),
                AutoTagRecord.next_retry_at <= now,
            ),
        )
        .limit(1)
    )
    if retryable is not None:
        return True
    stale = db.scalar(
        select(AutoTagRecord.id)
        .where(
            AutoTagRecord.status == "running",
            AutoTagRecord.updated_at <= stale_running,
        )
        .limit(1)
    )
    if stale is not None:
        return True
    missing = db.scalar(
        select(Entry.id)
        .where(Entry.id.not_in(select(AutoTagRecord.entry_id)))
        .limit(1)
    )
    return missing is not None


def auto_tag_status(db: Session) -> dict:
    connection = get_feature_connection(db, AUTO_TAG_FEATURE)
    counts = dict(
        db.execute(
            select(AutoTagRecord.status, func.count()).group_by(AutoTagRecord.status)
        ).all()
    )
    return {
        "enabled": is_auto_tag_enabled(db),
        "create_new": auto_tag_create_new(db),
        "llm_connection_id": connection.id if connection else None,
        "llm_connection_name": connection.name if connection else None,
        "model": connection.model if connection else None,
        "configured": connection is not None,
        "max_tags_per_entry": MAX_AUTO_TAGS_PER_ENTRY,
        "counts": {
            "pending": int(counts.get("pending", 0)),
            "running": int(counts.get("running", 0)),
            "complete": int(counts.get("complete", 0)),
            "failed": int(counts.get("failed", 0)),
        },
    }
