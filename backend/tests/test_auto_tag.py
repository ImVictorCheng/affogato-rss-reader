from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.auto_tag import (
    MAX_AUTO_TAGS_PER_ENTRY,
    _parse_tag_names,
    auto_tag_due,
    auto_tag_pending,
    auto_tag_status,
    configure_auto_tag,
    ensure_auto_tag_queue,
    tag_entry,
)
from backend.app.models import (
    AutoTagRecord,
    Entry,
    EntryTag,
    LLMConnection,
    Tag,
    Work,
)
from backend.app.llm import LLMConnectionError


def add_entry(factory, *, title: str = "Tagged article") -> int:
    with factory() as db:
        work = Work(
            dedup_key=f"url:https://tag.test/{title}",
            canonical_url=f"https://tag.test/{title}",
        )
        db.add(work)
        db.flush()
        entry = Entry(
            work_id=work.id,
            version_key="default",
            title=title,
            summary="A summary about quantum error correction.",
            url=f"https://tag.test/{title}",
            authors=["Alice"],
            source_hash="t" * 64,
        )
        db.add(entry)
        db.commit()
        return entry.id


def add_llm_connection(factory) -> int:
    with factory() as db:
        connection = LLMConnection(
            name="Tag LLM",
            base_url="https://llm.test/v1",
            model="tag-model",
            api_key_encrypted="fernet:v1:test",
            api_key_hint="****test",
        )
        db.add(connection)
        db.commit()
        return connection.id


def test_parse_tag_names_limits_to_five_and_deduplicates():
    payload = '["a", "b", "b", "c", "d", "e", "f"]'
    assert _parse_tag_names(payload) == ["a", "b", "c", "d", "e"]
    assert len(_parse_tag_names(payload)) <= MAX_AUTO_TAGS_PER_ENTRY
    assert _parse_tag_names("not json") == []
    assert _parse_tag_names("text [\"x\", 3, \"y\"] trailing") == ["x", "y"]
    longest_valid = "x" * 120
    assert _parse_tag_names(
        json.dumps(
            [
                "   ",
                " Physics ",
                "physics",
                longest_valid,
                "y" * 121,
            ]
        )
    ) == ["Physics", longest_valid]


def test_auto_tag_reuses_case_insensitive_manual_tag_name(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        lambda *args, **kwargs: '[" physics ", "PHYSICS"]',
    )
    with db_factory() as db:
        existing = Tag(name="Physics")
        db.add(existing)
        db.commit()
        entry_id = add_entry(db_factory)
        configure_auto_tag(
            db,
            enabled=True,
            create_new=True,
            llm_connection_id=add_llm_connection(db_factory),
        )
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        assert record is not None
        tag_entry(db, record)

        assert list(db.scalars(select(Tag.name))) == ["Physics"]
        link = db.scalar(
            select(EntryTag).where(EntryTag.entry_id == entry_id)
        )
        assert link is not None
        assert link.tag_id == existing.id


def test_tag_entry_existing_only_mode_never_creates_tags(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        lambda *args, **kwargs: '["physics", "brand-new-tag"]',
    )
    with db_factory() as db:
        existing = Tag(name="physics")
        db.add(existing)
        db.commit()
        entry_id = add_entry(db_factory)
        configure_auto_tag(db, enabled=True, create_new=False, llm_connection_id=add_llm_connection(db_factory))
        db.commit()
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        assert record is not None
        tag_entry(db, record)
        db.refresh(record)
        assert record.status == "complete"
        attached = list(db.scalars(select(Tag).order_by(Tag.id)))
        assert [tag.name for tag in attached] == ["physics"]
        links = db.scalars(
            select(EntryTag).where(EntryTag.entry_id == entry_id)
        ).all()
        assert len(links) == 1
        assert links[0].tag_id == existing.id


def test_tag_entry_create_new_mode_adds_new_tags_to_library(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        lambda *args, **kwargs: '["physics", "quantum-error-correction"]',
    )
    with db_factory() as db:
        db.add(Tag(name="physics"))
        db.commit()
        entry_id = add_entry(db_factory)
        configure_auto_tag(db, enabled=True, create_new=True, llm_connection_id=add_llm_connection(db_factory))
        db.commit()
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        names = list(db.scalars(select(Tag.name).order_by(Tag.id)))
        assert "quantum-error-correction" in names
        db.refresh(record)
        assert len(record.tag_ids) == 2


def test_retagging_replaces_only_previous_auto_tags(db_factory, monkeypatch):
    calls = {"n": 0}

    def summarize(*args, **kwargs):
        calls["n"] += 1
        return '["old"]' if calls["n"] == 1 else '["new"]'

    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        summarize,
    )
    with db_factory() as db:
        entry_id = add_entry(db_factory)
        configure_auto_tag(db, enabled=True, create_new=True, llm_connection_id=add_llm_connection(db_factory))
        db.commit()
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        old = db.scalar(select(Tag).where(Tag.name == "old"))
        assert old is not None
        # The owner adds a tag by hand; it must survive the next auto run.
        manual = Tag(name="manual-tag")
        db.add(manual)
        db.flush()
        db.add(EntryTag(entry_id=entry_id, tag_id=manual.id))
        # Simulate a re-run after content change.
        entry = db.get(Entry, entry_id)
        entry.summary = "A summary about new directions."
        entry.source_hash = "u" * 64
        record.source_hash = "v" * 64
        db.commit()
        tag_entry(db, record)
        names = list(
            db.scalars(select(Tag.name).order_by(Tag.id))
        )
        assert "old" in names  # library keeps it
        links = list(
            db.scalars(select(EntryTag).where(EntryTag.entry_id == entry_id))
        )
        assert sorted(db.get(Tag, link.tag_id).name for link in links) == ["manual-tag", "new"]


def test_llm_failure_marks_record_failed_with_retry_backoff(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LLMConnectionError("503", retryable=True)
        ),
    )
    with db_factory() as db:
        entry_id = add_entry(db_factory)
        configure_auto_tag(db, enabled=True, create_new=False, llm_connection_id=add_llm_connection(db_factory))
        db.commit()
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        db.refresh(record)
        assert record.status == "failed"
        assert record.next_retry_at is not None


def test_auto_tag_pending_processes_queue_and_due_check(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        lambda *args, **kwargs: '["physics"]',
    )
    with db_factory() as db:
        db.add(Tag(name="physics"))
        db.commit()
        first = add_entry(db_factory, title="One")
        second = add_entry(db_factory, title="Two")
        configure_auto_tag(db, enabled=True, create_new=False, llm_connection_id=add_llm_connection(db_factory))
        db.commit()
        assert auto_tag_due(db) is True
        processed = auto_tag_pending(db, limit=10)
        assert len(processed) == 2
        assert all(row.status == "complete" for row in processed)
        assert auto_tag_due(db) is False
        with db_factory() as db2:
            status = auto_tag_status(db2)
            assert status["enabled"] is True
            assert status["counts"]["complete"] == 2
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == first))
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == second))


def test_disabled_auto_tag_does_not_process_or_count_due(db_factory, monkeypatch):
    with db_factory() as db:
        add_entry(db_factory)
        configure_auto_tag(db, enabled=False, create_new=False)
        db.commit()
        assert auto_tag_due(db) is False
        assert auto_tag_pending(db, limit=10) == []
