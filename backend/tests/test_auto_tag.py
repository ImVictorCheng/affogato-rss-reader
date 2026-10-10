from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app.auto_tag import (
    AUTO_TAG_BATCH_SIZE,
    AUTO_TAG_INPUT_CHAR_BUDGET,
    AUTO_TAG_MAX_ATTEMPTS,
    MAX_AUTO_TAGS_PER_ENTRY,
    _build_batch_prompt,
    _parse_tag_names,
    add_manual_tag,
    apply_cleanup,
    auto_tag_due,
    auto_tag_pending,
    auto_tag_status,
    cleanup_preview,
    configure_auto_tag,
    create_preview,
    ensure_auto_tag_queue,
    normalize_topic_name,
    promote_proposal,
    remove_manual_tag,
    run_auto_tag_preview,
    tag_entry,
    tag_entries_batch,
)
from backend.app.models import (
    AppSetting,
    AutoTagRecord,
    AutoTagSuppression,
    Domain,
    Entry,
    EntryDomain,
    EntryFeed,
    EntryTag,
    EntryTagSource,
    Feed,
    LLMConnection,
    Tag,
    TagAlias,
    TagProposal,
    TagProposalAlias,
    TagProposalSupport,
    Work,
    utcnow,
)


def add_entry(
    factory,
    *,
    title: str = "Tagged article",
    work: Work | None = None,
    published_at=None,
) -> int:
    with factory() as db:
        if work is None:
            work = Work(
                dedup_key=f"url:https://tag.test/{title}",
                canonical_url=f"https://tag.test/{title}",
            )
            db.add(work)
            db.flush()
        else:
            work = db.merge(work)
        existing_versions = list(
            db.scalars(select(Entry).where(Entry.work_id == work.id))
        )
        version = len(existing_versions) + 1
        entry = Entry(
            work_id=work.id,
            version_key=f"v{version}",
            title=title,
            summary="A summary about quantum error correction.",
            url=f"https://tag.test/{title}/{version}",
            authors=["Alice"],
            source_hash=(title[0].lower() if title else "t") * 64,
            published_at=published_at,
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


def response_for_articles(*topics: dict) -> callable:
    def complete(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        return json.dumps(
            {
                "entries": [
                    {"entry_id": article["entry_id"], "topics": list(topics)}
                    for article in request["articles"]
                ]
            }
        )

    return complete


def enable_closed(db, factory) -> None:
    connection_id = add_llm_connection(factory)
    configure_auto_tag(
        db,
        enabled=False,
        growth_mode="closed",
        llm_connection_id=connection_id,
    )
    row = db.get(AppSetting, "auto_tag_preview_required")
    if row is None:
        db.add(AppSetting(key="auto_tag_preview_required", value="false"))
    else:
        row.value = "false"
    db.commit()
    configure_auto_tag(db, enabled=True, growth_mode="closed")


def enable_threshold(db, factory, *, threshold: int = 10) -> None:
    connection_id = add_llm_connection(factory)
    configure_auto_tag(
        db,
        enabled=False,
        growth_mode="threshold",
        promotion_threshold=threshold,
        support_window_days=365,
        canonical_language="en",
        llm_connection_id=connection_id,
    )
    row = db.get(AppSetting, "auto_tag_preview_required")
    if row is None:
        db.add(AppSetting(key="auto_tag_preview_required", value="false"))
    else:
        row.value = "false"
    db.commit()
    configure_auto_tag(db, enabled=True, growth_mode="threshold")


def add_approved_tag(db, name: str) -> Tag:
    tag = Tag(
        name=name,
        normalized_name=normalize_topic_name(name),
        description=f"Articles about {name}.",
        origin="manual",
        auto_assignable=True,
    )
    db.add(tag)
    db.commit()
    return tag


def test_legacy_parser_uses_new_three_tag_limit_and_normalizes():
    payload = '["A", "a", "b", "c", "d"]'
    assert _parse_tag_names(payload) == ["A", "b", "c"]
    assert len(_parse_tag_names(payload)) == MAX_AUTO_TAGS_PER_ENTRY == 3
    assert normalize_topic_name(" Quantum-error  Correction ") == "quantum error correction"


def test_closed_mode_allows_zero_and_ignores_low_confidence(db_factory, monkeypatch):
    with db_factory() as db:
        tag = add_approved_tag(db, "Physics")
        entry_id = add_entry(db_factory)
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            response_for_articles(
                {"kind": "tag", "id": tag.id, "confidence": 0.79},
            ),
        )
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        assert record.status == "complete"
        assert record.tag_ids == []
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == entry_id)) is None
        assert db.scalar(select(TagProposal.id)) is None


def test_unknown_tag_id_fails_without_removing_previous_auto_tag(db_factory, monkeypatch):
    calls = {"count": 0}

    def complete(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        calls["count"] += 1
        tag_id = 1 if calls["count"] == 1 else 999_999
        return json.dumps(
            {
                "entries": [
                    {
                        "entry_id": article["entry_id"],
                        "topics": [{"kind": "tag", "id": tag_id, "confidence": 0.95}],
                    }
                    for article in request["articles"]
                ]
            }
        )

    monkeypatch.setattr("backend.app.auto_tag.complete_feature_chat", complete)
    with db_factory() as db:
        tag = add_approved_tag(db, "Physics")
        assert tag.id == 1
        entry_id = add_entry(db_factory)
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        assert record.tag_ids == [tag.id]
        tag_entry(db, record)
        assert record.status == "failed"
        assert record.tag_ids == [tag.id]
        assert db.scalar(
            select(EntryTag).where(EntryTag.entry_id == entry_id, EntryTag.tag_id == tag.id)
        ) is not None


def test_alias_and_punctuation_variant_resolve_to_existing_tag(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "quantum-computing",
                "description": "Quantum information processing.",
                "aliases": ["量子计算"],
                "confidence": 0.94,
            }
        ),
    )
    with db_factory() as db:
        tag = add_approved_tag(db, "Quantum Computing")
        db.add(
            TagAlias(
                tag_id=tag.id,
                alias="量子计算",
                normalized_alias=normalize_topic_name("量子计算"),
            )
        )
        db.commit()
        entry_id = add_entry(db_factory)
        enable_threshold(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        assert record.tag_ids == [tag.id]
        assert db.scalar(select(TagProposal.id)) is None


def test_threshold_requires_approved_preview(db_factory, enabled_auto_tag_preview):
    with db_factory() as db:
        connection_id = add_llm_connection(db_factory)
        configure_auto_tag(
            db,
            enabled=True,
            growth_mode="threshold",
            llm_connection_id=connection_id,
        )
        status = auto_tag_status(db)
        assert status["enabled"] is False
        assert status["preview_required"] is True
        with pytest.raises(ValueError, match="preview"):
            configure_auto_tag(
                db,
                enabled=True,
                growth_mode="threshold",
            )


def test_disabled_preview_does_not_block_a_stored_requirement(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles({"kind": "new", "name": "Quantum sensing", "confidence": 0.93}),
    )
    with db_factory() as db:
        add_entry(db_factory, title="Direct tagging", published_at=utcnow())
        enable_threshold(db, db_factory)
        db.get(AppSetting, "auto_tag_preview_required").value = "true"
        db.commit()
        configure_auto_tag(db, enabled=True, growth_mode="threshold")
        assert auto_tag_status(db)["enabled"] is True
        assert auto_tag_status(db)["preview_required"] is False
        assert db.get(AppSetting, "auto_tag_preview_required").value == "true"
        assert auto_tag_due(db) is True
        records = auto_tag_pending(db)
        assert len(records) == 1 and records[0].status == "complete"
        with pytest.raises(ValueError, match="currently disabled"):
            create_preview(db)
        with pytest.raises(ValueError, match="currently disabled"):
            run_auto_tag_preview(db, 1)


def test_nine_works_remain_a_proposal(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Quantum error correction",
                "description": "Methods that protect quantum information from errors.",
                "aliases": ["QEC", "量子纠错"],
                "confidence": 0.93,
            }
        ),
    )
    with db_factory() as db:
        for index in range(9):
            add_entry(db_factory, title=f"Paper {index}", published_at=utcnow())
        enable_threshold(db, db_factory)
        records = auto_tag_pending(db, limit=AUTO_TAG_BATCH_SIZE)
        assert len(records) == 9
        proposal = db.scalar(select(TagProposal))
        assert proposal is not None
        assert proposal.support_count == 9
        assert proposal.status == "active"
        assert db.scalar(select(Tag).where(Tag.origin == "auto_promoted")) is None
        assert db.scalar(select(EntryTag.id)) is None


def test_tenth_distinct_work_promotes_and_backfills(db_factory, monkeypatch, caplog):
    caplog.set_level("INFO", logger="uvicorn.error.auto_tag")
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Quantum error correction",
                "description": "Methods that protect quantum information from errors.",
                "aliases": ["QEC", "量子纠错"],
                "confidence": 0.93,
            }
        ),
    )
    with db_factory() as db:
        for index in range(10):
            add_entry(db_factory, title=f"Paper {index}", published_at=utcnow())
        enable_threshold(db, db_factory)
        records = auto_tag_pending(db, limit=AUTO_TAG_BATCH_SIZE)
        assert len(records) == 10
        proposal = db.scalar(select(TagProposal))
        tag = db.scalar(select(Tag).where(Tag.origin == "auto_promoted"))
        assert proposal is not None and proposal.status == "promoted"
        assert proposal.support_count == 10
        assert tag is not None and tag.name == "Quantum error correction"
        assert db.scalars(select(EntryTag).where(EntryTag.tag_id == tag.id)).all()
        assert len(db.scalars(select(EntryTag).where(EntryTag.tag_id == tag.id)).all()) == 10
        assert len(db.scalars(select(TagAlias).where(TagAlias.tag_id == tag.id)).all()) == 2
        status = auto_tag_status(db)
        assert status["counts"]["complete"] == 10
        assert status["outdated_count"] == 0
        assert status["needs_rebuild"] is False
        assert status["estimated_calls"] == 0
        assert auto_tag_due(db) is False
        promotion_logs = [
            record.getMessage()
            for record in caplog.records
            if record.name == "uvicorn.error.auto_tag"
        ]
        assert promotion_logs == [
            f"Auto-tag topic promoted: proposal_id={proposal.id} "
            f"name='Quantum error correction' tag_id={tag.id} "
            "support_count=10 threshold=10 window_days=365"
        ]


def test_failed_promotion_commit_does_not_write_a_promotion_log(db_factory, monkeypatch, caplog):
    caplog.set_level("INFO", logger="uvicorn.error.auto_tag")
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles({
            "kind": "new",
            "name": "Quantum error correction",
            "confidence": 0.93,
        }),
    )
    with db_factory() as db:
        for index in range(2):
            add_entry(db_factory, title=f"Paper {index}", published_at=utcnow())
        enable_threshold(db, db_factory, threshold=2)
        commit = db.commit

        def fail_promotion_commit():
            if db.scalar(select(Tag).where(Tag.origin == "auto_promoted")) is not None:
                raise RuntimeError("Promotion commit failed")
            commit()

        monkeypatch.setattr(db, "commit", fail_promotion_commit)
        records = auto_tag_pending(db, limit=AUTO_TAG_BATCH_SIZE)
        assert all(record.status == "failed" for record in records)
        assert db.scalar(select(Tag).where(Tag.origin == "auto_promoted")) is None
        assert not [record for record in caplog.records if record.name == "uvicorn.error.auto_tag"]


def test_manual_promotion_below_threshold_transfers_aliases_and_valid_supports(
    db_factory, monkeypatch, caplog,
):
    caplog.set_level("INFO", logger="uvicorn.error.auto_tag")
    calls = []
    response = response_for_articles({
        "kind": "new", "name": "Quantum networking",
        "description": "Distribution of quantum information.",
        "aliases": ["QNet"], "confidence": 0.93,
    })

    def complete(*args, **kwargs):
        calls.append(kwargs)
        return response(*args, **kwargs)

    monkeypatch.setattr("backend.app.auto_tag.complete_feature_chat", complete)
    with db_factory() as db:
        first_id = add_entry(db_factory, title="First manual support", published_at=utcnow())
        second_id = add_entry(db_factory, title="Changed manual support", published_at=utcnow())
        enable_threshold(db, db_factory)
        records = auto_tag_pending(db)
        assert all(record.status == "complete" for record in records)
        proposal = db.scalar(select(TagProposal))
        assert proposal.status == "active" and proposal.support_count == 2
        db.get(Entry, second_id).source_hash = "z" * 64
        db.commit()
        calls_before = len(calls)

        tag = promote_proposal(db, proposal.id)

        assert len(calls) == calls_before
        assert tag.origin == "manual_promoted" and tag.auto_assignable is True
        assert tag.description == "Distribution of quantum information."
        assert proposal.status == "promoted" and proposal.promoted_tag_id == tag.id
        assert proposal.support_count == 1
        assert db.scalar(select(TagAlias).where(TagAlias.tag_id == tag.id)).alias == "QNet"
        assert db.scalar(select(TagProposalAlias)) is None
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == first_id)).tag_id == tag.id
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == second_id)) is None
        assert all(record.status == "complete" for record in records)
        assert auto_tag_status(db)["needs_rebuild"] is False
        assert promote_proposal(db, proposal.id).id == tag.id
        assert len(db.scalars(select(Tag)).all()) == 1
        assert len(db.scalars(select(EntryTagSource)).all()) == 1
        logs = [record.getMessage() for record in caplog.records if record.name == "uvicorn.error.auto_tag"]
        assert len(logs) == 1 and "topic manually promoted:" in logs[0]
        assert ensure_auto_tag_queue(db) == 1  # Only the changed article enters the queue.


def test_failed_manual_promotion_commit_does_not_log_success(db_factory, monkeypatch, caplog):
    caplog.set_level("INFO", logger="uvicorn.error.auto_tag")
    with db_factory() as db:
        proposal = TagProposal(name="Manual topic", normalized_name="manual topic", status="active")
        db.add(proposal)
        db.commit()

        def fail_commit():
            raise RuntimeError("Promotion commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="Promotion commit failed"):
            promote_proposal(db, proposal.id)
        db.rollback()
        assert db.scalar(select(Tag)) is None
        assert proposal.status == "active"
        assert not [record for record in caplog.records if record.name == "uvicorn.error.auto_tag"]


def test_duplicate_versions_and_old_articles_do_not_inflate_support(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Quantum sensing",
                "description": "Quantum-enhanced measurement.",
                "aliases": [],
                "confidence": 0.9,
            }
        ),
    )
    with db_factory() as db:
        shared = Work(dedup_key="shared-work", canonical_url="https://tag.test/shared")
        db.add(shared)
        db.commit()
        add_entry(db_factory, title="Shared v1", work=shared, published_at=utcnow())
        add_entry(db_factory, title="Shared v2", work=shared, published_at=utcnow())
        for index in range(8):
            add_entry(db_factory, title=f"Recent {index}", published_at=utcnow())
        add_entry(
            db_factory,
            title="Old work",
            published_at=utcnow() - timedelta(days=366),
        )
        enable_threshold(db, db_factory)
        auto_tag_pending(db, limit=AUTO_TAG_BATCH_SIZE)
        auto_tag_pending(db, limit=AUTO_TAG_BATCH_SIZE)
        proposal = db.scalar(select(TagProposal))
        assert proposal is not None
        assert proposal.support_count == 9
        assert proposal.status == "active"
        assert len(db.scalars(select(TagProposalSupport)).all()) == 11


def test_retagging_removes_only_auto_source_and_preserves_manual(db_factory, monkeypatch):
    calls = {"count": 0}

    def complete(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        calls["count"] += 1
        topics = (
            [{"kind": "tag", "id": 1, "confidence": 0.95}]
            if calls["count"] == 1
            else []
        )
        return json.dumps(
            {
                "entries": [
                    {"entry_id": article["entry_id"], "topics": topics}
                    for article in request["articles"]
                ]
            }
        )

    monkeypatch.setattr("backend.app.auto_tag.complete_feature_chat", complete)
    with db_factory() as db:
        tag = add_approved_tag(db, "Physics")
        assert tag.id == 1
        entry_id = add_entry(db_factory)
        add_manual_tag(db, entry_id, tag.id)
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        link = db.scalar(select(EntryTag).where(EntryTag.entry_id == entry_id))
        assert {row.source for row in db.scalars(select(EntryTagSource).where(EntryTagSource.entry_tag_id == link.id))} == {"manual", "auto"}
        tag_entry(db, record)
        assert db.get(EntryTag, link.id) is not None
        assert [row.source for row in db.scalars(select(EntryTagSource).where(EntryTagSource.entry_tag_id == link.id))] == ["manual"]


def test_owner_removal_suppresses_future_automatic_assignment(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles({"kind": "tag", "id": 1, "confidence": 0.95}),
    )
    with db_factory() as db:
        tag = add_approved_tag(db, "Physics")
        entry_id = add_entry(db_factory)
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        remove_manual_tag(db, entry_id, tag.id)
        assert db.scalar(
            select(AutoTagSuppression).where(
                AutoTagSuppression.entry_id == entry_id,
                AutoTagSuppression.tag_id == tag.id,
            )
        ) is not None
        tag_entry(db, record)
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == entry_id)) is None


def test_source_hash_change_requeues_completed_record(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(),
    )
    with db_factory() as db:
        entry_id = add_entry(db_factory)
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        assert record.status == "complete"
        entry = db.get(Entry, entry_id)
        entry.source_hash = "z" * 64
        db.commit()
        assert auto_tag_due(db) is True
        assert ensure_auto_tag_queue(db) == 1
        assert record.status == "pending"


def test_cleanup_preview_is_read_only_then_removes_selected_legacy_auto_links(db_factory):
    with db_factory() as db:
        tag = add_approved_tag(db, "One-off")
        entry_id = add_entry(db_factory)
        link = EntryTag(entry_id=entry_id, tag_id=tag.id)
        db.add(link)
        db.flush()
        db.add(EntryTagSource(entry_tag_id=link.id, source="legacy"))
        db.add(
            AutoTagRecord(
                entry_id=entry_id,
                source_hash=db.get(Entry, entry_id).source_hash,
                status="complete",
                tag_ids=[tag.id],
            )
        )
        db.commit()
        before = cleanup_preview(db)
        assert before["inferred_auto_association_count"] == 1
        assert db.get(Tag, tag.id) is not None
        result = apply_cleanup(
            db,
            remove_tag_ids=[tag.id],
            keep_tag_ids=[],
            review_token=before["review_token"],
        )
        assert result["removed_count"] == 1
        assert result["removed_tag_ids"] == [tag.id]
        assert db.get(Tag, tag.id) is None


def test_status_estimates_one_call_per_ten_articles(db_factory):
    with db_factory() as db:
        for index in range(21):
            add_entry(db_factory, title=f"Queued {index}")
        configure_auto_tag(
            db,
            enabled=False,
            growth_mode="closed",
            llm_connection_id=add_llm_connection(db_factory),
        )
        ensure_auto_tag_queue(db)
        db.commit()
        status = auto_tag_status(db)
        assert status["estimated_calls"] == 3
        assert status["max_tags_per_entry"] == 3
        assert status["promotion_threshold"] == 10
        assert status["support_window_days"] == 365


def test_complete_batch_prompt_is_bounded_uses_ordinals_and_excludes_local_metadata(
    db_factory,
):
    with db_factory() as db:
        entry_ids = [
            add_entry(db_factory, title=f"Visible article {index}")
            for index in range(12)
        ]
        selected_ids = entry_ids[-2:]
        for index, entry_id in enumerate(selected_ids):
            entry = db.get(Entry, entry_id)
            entry.summary = f"Visible summary {index} " + ("x" * 50_000)
            entry.authors = [f"PRIVATE_AUTHOR_{index}"]
            entry.categories = [f"PRIVATE_CATEGORY_{index}"]

        feed = Feed(
            title="PRIVATE_FEED_TITLE",
            url="https://private-feed.test/rss",
        )
        domain = Domain(name="PRIVATE_DOMAIN_NAME")
        db.add_all([feed, domain])
        db.flush()
        for entry_id in selected_ids:
            db.add(EntryFeed(entry_id=entry_id, feed_id=feed.id))
            db.add(EntryDomain(entry_id=entry_id, domain_id=domain.id))

        for index in range(110):
            db.add(
                Tag(
                    name=f"Controlled topic {index}",
                    normalized_name=f"controlled topic {index}",
                    description="d" * 1_000,
                    auto_assignable=True,
                )
            )
        for index in range(60):
            db.add(
                TagProposal(
                    name=f"Candidate topic {index}",
                    normalized_name=f"candidate topic {index}",
                    description="p" * 1_000,
                    status="active",
                    support_count=index,
                )
            )
        db.commit()
        configure_auto_tag(db, enabled=False, growth_mode="threshold")

        entries = [db.get(Entry, entry_id) for entry_id in selected_ids]
        system_prompt, user_prompt, _tag_ids, _proposal_ids = _build_batch_prompt(
            db, entries
        )
        payload = json.loads(user_prompt)
        serialized_prompt = system_prompt + user_prompt

        assert len(serialized_prompt) <= AUTO_TAG_INPUT_CHAR_BUDGET == 24_000
        assert selected_ids != [1, 2]
        assert [article["entry_id"] for article in payload["articles"]] == [1, 2]
        assert all(
            set(article) == {"entry_id", "title", "summary"}
            for article in payload["articles"]
        )
        for private_value in (
            "PRIVATE_AUTHOR_0",
            "PRIVATE_AUTHOR_1",
            "PRIVATE_CATEGORY_0",
            "PRIVATE_CATEGORY_1",
            "PRIVATE_FEED_TITLE",
            "PRIVATE_DOMAIN_NAME",
        ):
            assert private_value not in serialized_prompt


def test_closed_mode_also_requires_an_approved_preview(db_factory, enabled_auto_tag_preview):
    with db_factory() as db:
        connection_id = add_llm_connection(db_factory)
        configure_auto_tag(
            db,
            enabled=True,
            growth_mode="closed",
            llm_connection_id=connection_id,
        )
        status = auto_tag_status(db)
        assert status["enabled"] is False
        assert status["preview_required"] is True
        with pytest.raises(ValueError, match="preview"):
            configure_auto_tag(
                db,
                enabled=True,
                growth_mode="closed",
            )


def test_chinese_and_abbreviation_aliases_reuse_one_formal_tag(
    db_factory, monkeypatch
):
    def complete(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        topics = {
            1: {
                "kind": "new",
                "name": "qec",
                "description": "Quantum error protection.",
                "aliases": [],
                "confidence": 0.95,
            },
            2: {
                "kind": "new",
                "name": "Fault-tolerant quantum computing",
                "description": "Reliable quantum computation.",
                "aliases": ["量子纠错"],
                "confidence": 0.94,
            },
        }
        return json.dumps(
            {
                "entries": [
                    {
                        "entry_id": article["entry_id"],
                        "topics": [topics[article["entry_id"]]],
                    }
                    for article in request["articles"]
                ]
            }
        )

    monkeypatch.setattr("backend.app.auto_tag.complete_feature_chat", complete)
    with db_factory() as db:
        tag = add_approved_tag(db, "Quantum error correction")
        db.add_all(
            [
                TagAlias(
                    tag_id=tag.id,
                    alias="QEC",
                    normalized_alias=normalize_topic_name("QEC"),
                ),
                TagAlias(
                    tag_id=tag.id,
                    alias="量子纠错",
                    normalized_alias=normalize_topic_name("量子纠错"),
                ),
            ]
        )
        db.commit()
        entry_ids = [
            add_entry(db_factory, title=f"Alias article {index}") for index in range(2)
        ]
        enable_threshold(db, db_factory)
        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_(entry_ids))
                .order_by(AutoTagRecord.entry_id)
            )
        )

        tag_entries_batch(db, records)

        assert db.scalar(select(TagProposal.id)) is None
        assert len(db.scalars(select(Tag)).all()) == 1
        for entry_id in entry_ids:
            assert db.scalar(
                select(EntryTag).where(
                    EntryTag.entry_id == entry_id,
                    EntryTag.tag_id == tag.id,
                )
            ) is not None


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not JSON at all",
        "{}",
        '{"entries":[]}',
    ],
)
def test_malformed_missing_or_omitted_json_preserves_old_automatic_tag(
    db_factory, monkeypatch, bad_payload
):
    calls = {"count": 0}

    def complete(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps(
                {
                    "entries": [
                        {
                            "entry_id": article["entry_id"],
                            "topics": [
                                {"kind": "tag", "id": 1, "confidence": 0.95}
                            ],
                        }
                        for article in request["articles"]
                    ]
                }
            )
        return bad_payload

    monkeypatch.setattr("backend.app.auto_tag.complete_feature_chat", complete)
    with db_factory() as db:
        tag = add_approved_tag(db, "Physics")
        assert tag.id == 1
        entry_id = add_entry(db_factory)
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        record = tag_entry(db, record)
        assert record.tag_ids == [tag.id]

        record = tag_entry(db, record)

        assert record.status == "failed"
        assert record.tag_ids == [tag.id]
        link = db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == entry_id,
                EntryTag.tag_id == tag.id,
            )
        )
        assert link is not None
        assert db.scalar(
            select(EntryTagSource).where(
                EntryTagSource.entry_tag_id == link.id,
                EntryTagSource.source == "auto",
            )
        ) is not None


def test_format_failure_splits_batch_and_never_exceeds_retry_limit(
    db_factory, monkeypatch
):
    calls = {"count": 0}

    def invalid_response(*args, **kwargs):
        calls["count"] += 1
        return "invalid"

    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        invalid_response,
    )
    with db_factory() as db:
        entry_ids = [
            add_entry(db_factory, title=f"Split failure {index}") for index in range(4)
        ]
        enable_closed(db, db_factory)
        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_(entry_ids))
                .order_by(AutoTagRecord.entry_id)
            )
        )

        tag_entries_batch(db, records)

        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_(entry_ids))
                .order_by(AutoTagRecord.entry_id)
            )
        )
        assert calls["count"] == 7
        assert {record.status for record in records} == {"failed"}
        assert {record.attempts for record in records} == {3}
        assert all(record.attempts <= AUTO_TAG_MAX_ATTEMPTS for record in records)


def test_single_record_stops_calling_llm_after_five_attempts(db_factory, monkeypatch):
    calls = {"count": 0}

    def invalid_response(*args, **kwargs):
        calls["count"] += 1
        return "invalid"

    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        invalid_response,
    )
    with db_factory() as db:
        entry_id = add_entry(db_factory, title="Retry ceiling")
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )

        for _index in range(AUTO_TAG_MAX_ATTEMPTS + 2):
            record = tag_entry(db, record)

        assert calls["count"] == AUTO_TAG_MAX_ATTEMPTS == 5
        assert record.attempts == AUTO_TAG_MAX_ATTEMPTS
        assert record.status == "failed"
        assert record.next_retry_at is None


def test_status_estimates_unrecorded_entries_without_populating_queue(db_factory):
    with db_factory() as db:
        for index in range(21):
            add_entry(db_factory, title=f"Never queued {index}")
        configure_auto_tag(
            db,
            enabled=False,
            growth_mode="closed",
            llm_connection_id=add_llm_connection(db_factory),
        )

        assert db.scalar(select(AutoTagRecord.id)) is None
        status = auto_tag_status(db)

        assert status["estimated_calls"] == 3
        assert db.scalar(select(AutoTagRecord.id)) is None


@pytest.mark.parametrize("change", ["add", "rename", "alias", "description", "auto_assignable"])
def test_taxonomy_change_preserves_completed_results(
    db_factory, monkeypatch, change
):
    with db_factory() as db:
        tag = add_approved_tag(db, "Established topic")
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            response_for_articles({"kind": "tag", "id": tag.id, "confidence": 0.95}),
        )
        entry_id = add_entry(db_factory, title="Stable classified article")
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )
        tag_entry(db, record)
        assert auto_tag_status(db)["needs_rebuild"] is False
        original_policy_version = record.policy_version
        original_tag_ids = list(record.tag_ids)

        if change == "add":
            add_approved_tag(db, "New taxonomy topic")
        elif change == "rename":
            tag.name = "Renamed topic"
            tag.normalized_name = normalize_topic_name(tag.name)
        elif change == "alias":
            db.add(TagAlias(tag_id=tag.id, alias="New alias", normalized_alias="new alias"))
        elif change == "description":
            tag.description = "Updated topic description."
        else:
            tag.auto_assignable = False
        db.commit()
        status = auto_tag_status(db)

        assert status["needs_rebuild"] is False
        assert status["outdated_count"] == 0
        assert status["counts"]["complete"] == 1
        assert status["estimated_calls"] == 0
        assert ensure_auto_tag_queue(db) == 0
        assert record.status == "complete"
        assert record.policy_version == original_policy_version
        assert record.tag_ids == original_tag_ids == [tag.id]
        assert db.scalar(select(EntryTag).where(EntryTag.entry_id == entry_id)).tag_id == tag.id
        assert auto_tag_due(db) is False


@pytest.mark.parametrize("policy_version", [None, "older-policy"])
def test_completed_legacy_results_do_not_require_rebuild(db_factory, policy_version):
    with db_factory() as db:
        entry_id = add_entry(db_factory, title="Previously completed article")
        enable_closed(db, db_factory)
        record = db.scalar(select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id))
        record.status = "complete"
        record.policy_version = policy_version
        db.commit()

        status = auto_tag_status(db)

        assert status["outdated_count"] == 0
        assert status["needs_rebuild"] is False
        assert status["estimated_calls"] == 0
        assert ensure_auto_tag_queue(db) == 0
        assert record.status == "complete"
        assert record.policy_version == policy_version
        assert auto_tag_due(db) is False


def test_existing_proposal_alias_is_reused_then_promoted_once(db_factory, monkeypatch):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Fault-tolerant quantum computing",
                "description": "Reliable quantum computation.",
                "aliases": ["QEC"],
                "confidence": 0.94,
            }
        ),
    )
    with db_factory() as db:
        proposal = TagProposal(
            name="Quantum error correction",
            normalized_name=normalize_topic_name("Quantum error correction"),
            description="Quantum codes that protect information.",
            status="active",
            support_count=0,
        )
        db.add(proposal)
        db.flush()
        db.add(
            TagProposalAlias(
                proposal_id=proposal.id,
                alias="QEC",
                normalized_alias=normalize_topic_name("QEC"),
            )
        )
        db.commit()
        entry_ids = [
            add_entry(db_factory, title=f"Proposal alias {index}", published_at=utcnow())
            for index in range(2)
        ]
        enable_threshold(db, db_factory, threshold=2)
        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_(entry_ids))
                .order_by(AutoTagRecord.entry_id)
            )
        )

        tag_entries_batch(db, records)

        assert len(db.scalars(select(TagProposal)).all()) == 1
        assert proposal.status == "promoted"
        assert proposal.support_count == 2
        tag = db.get(Tag, proposal.promoted_tag_id)
        assert tag is not None
        assert tag.name == "Quantum error correction"
        assert db.scalar(
            select(TagAlias).where(
                TagAlias.tag_id == tag.id,
                TagAlias.normalized_alias == normalize_topic_name("QEC"),
            )
        ) is not None
        assert len(
            db.scalars(select(EntryTag).where(EntryTag.tag_id == tag.id)).all()
        ) == 2


def test_promotion_backfill_does_not_exceed_three_existing_auto_tags(
    db_factory, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Quantum networking",
                "description": "Distribution of quantum information.",
                "aliases": ["QNet"],
                "confidence": 0.93,
            }
        ),
    )
    with db_factory() as db:
        first_id = add_entry(db_factory, title="First support", published_at=utcnow())
        second_id = add_entry(db_factory, title="Second support", published_at=utcnow())
        enable_threshold(db, db_factory, threshold=2)
        first_record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == first_id)
        )
        tag_entry(db, first_record)
        proposal = db.scalar(select(TagProposal))
        assert proposal is not None and proposal.status == "active"

        for index in range(MAX_AUTO_TAGS_PER_ENTRY):
            tag = add_approved_tag(db, f"Existing automatic topic {index}")
            link = EntryTag(entry_id=first_id, tag_id=tag.id)
            db.add(link)
            db.flush()
            db.add(
                EntryTagSource(
                    entry_tag_id=link.id,
                    source="auto",
                    confidence=0.9,
                    policy_version="older-policy",
                )
            )
        db.commit()
        second_record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == second_id)
        )

        tag_entry(db, second_record)

        promoted = db.get(Tag, proposal.promoted_tag_id)
        assert promoted is not None
        first_auto_sources = list(
            db.scalars(
                select(EntryTagSource)
                .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
                .where(
                    EntryTag.entry_id == first_id,
                    EntryTagSource.source == "auto",
                )
            )
        )
        assert len(first_auto_sources) == MAX_AUTO_TAGS_PER_ENTRY == 3
        assert db.scalar(
            select(EntryTagSource)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == first_id,
                EntryTag.tag_id == promoted.id,
                EntryTagSource.source == "auto",
            )
        ) is None


def test_old_article_can_receive_formal_tag_without_promotion_window(
    db_factory, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles({"kind": "tag", "id": 1, "confidence": 0.95}),
    )
    with db_factory() as db:
        tag = add_approved_tag(db, "Established topic")
        assert tag.id == 1
        entry_id = add_entry(
            db_factory,
            title="Old but classifiable",
            published_at=utcnow() - timedelta(days=500),
        )
        enable_closed(db, db_factory)
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry_id)
        )

        tag_entry(db, record)

        assert db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == entry_id,
                EntryTag.tag_id == tag.id,
            )
        ) is not None


def test_missing_published_at_uses_recent_ingestion_time_for_promotion(
    db_factory, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.auto_tag.complete_feature_chat",
        response_for_articles(
            {
                "kind": "new",
                "name": "Quantum memories",
                "description": "Storage of quantum states.",
                "aliases": [],
                "confidence": 0.92,
            }
        ),
    )
    with db_factory() as db:
        entry_ids = [
            add_entry(
                db_factory,
                title=f"No publication date {index}",
                published_at=None,
            )
            for index in range(2)
        ]
        enable_threshold(db, db_factory, threshold=2)
        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_(entry_ids))
                .order_by(AutoTagRecord.entry_id)
            )
        )

        tag_entries_batch(db, records)

        proposal = db.scalar(select(TagProposal))
        assert proposal is not None
        assert proposal.support_count == 2
        assert proposal.status == "promoted"
        assert proposal.promoted_tag_id is not None
