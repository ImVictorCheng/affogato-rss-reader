from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from threading import Event, Thread, current_thread

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select

from backend.app.auto_tag import (
    BatchClassification,
    EntryClassification,
    TopicChoice,
    _build_batch_prompt,
    _is_english_canonical_name,
    apply_cleanup,
    approve_preview,
    cleanup_preview,
    configure_auto_tag,
    create_preview,
    list_proposals,
    normalize_topic_name,
    run_auto_tag_preview,
    tag_entries_batch,
)
from backend.app.llm import LLMConnectionError
from backend.app.models import (
    AppSetting,
    AutoTagRecord,
    Entry,
    EntryFeed,
    EntryTag,
    EntryTagSource,
    Feed,
    FeedTag,
    LLMConnection,
    Tag,
    TagProposal,
    TagProposalAlias,
    TagProposalSupport,
    Work,
    utcnow,
)
from backend.app.sync import _merge_entry_into


def _add_entry(db, title: str, *, published_at=None) -> Entry:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    work = Work(
        dedup_key=f"guardrail:{digest}",
        canonical_url=f"https://guardrail.test/{digest}",
    )
    db.add(work)
    db.flush()
    entry = Entry(
        work_id=work.id,
        version_key="v1",
        title=title,
        summary=f"A reusable summary for {title}.",
        url=f"https://guardrail.test/{digest}/v1",
        authors=[],
        categories=[],
        source_hash=digest,
        published_at=published_at,
    )
    db.add(entry)
    db.flush()
    return entry


def _bind_auto_tag_connection(db) -> LLMConnection:
    connection = LLMConnection(
        name="Guardrail tag model",
        base_url="https://llm.guardrail.test/v1",
        model="guardrail-model",
        api_key_encrypted="fernet:v1:test",
        api_key_hint="****test",
    )
    db.add(connection)
    db.commit()
    configure_auto_tag(
        db,
        enabled=False,
        growth_mode="closed",
        llm_connection_id=connection.id,
    )
    return connection


def _confirm_cleanup(db) -> str:
    preview = cleanup_preview(db)
    apply_cleanup(
        db,
        remove_tag_ids=[],
        keep_tag_ids=[],
        review_token=preview["review_token"],
    )
    return str(preview["review_token"])


def _enable_auto_tag(db, *, growth_mode: str, promotion_threshold: int = 10) -> None:
    connection = _bind_auto_tag_connection(db)
    configure_auto_tag(
        db,
        enabled=False,
        growth_mode=growth_mode,
        promotion_threshold=promotion_threshold,
        support_window_days=365,
        canonical_language="en",
        llm_connection_id=connection.id,
    )
    preview_required = db.get(AppSetting, "auto_tag_preview_required")
    assert preview_required is not None
    preview_required.value = "false"
    db.commit()
    configure_auto_tag(db, enabled=True, growth_mode=growth_mode)


def _add_approved_tag(db, name: str) -> Tag:
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


@pytest.mark.parametrize("invalid_ids", ["unknown", "duplicate"])
def test_unknown_or_duplicate_batch_entry_id_rejects_output_without_applying_tags(
    db_factory,
    monkeypatch,
    invalid_ids,
):
    calls = 0

    def invalid_response(*args, **kwargs):
        nonlocal calls
        calls += 1
        request = json.loads(kwargs["user_prompt"])
        request_id = request["articles"][0]["entry_id"]
        entries = [
            {
                "entry_id": request_id,
                "topics": [
                    {"kind": "tag", "id": tag.id, "confidence": 0.95}
                ],
            }
        ]
        entries.append(
            {
                "entry_id": request_id if invalid_ids == "duplicate" else request_id + 1,
                "topics": [],
            }
        )
        return json.dumps({"entries": entries})

    with db_factory() as db:
        entry = _add_entry(db, f"Strict {invalid_ids} identifier")
        tag = _add_approved_tag(db, "Controlled topic")
        _enable_auto_tag(db, growth_mode="closed")
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            invalid_response,
        )
        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == entry.id)
        )
        assert record is not None

        tag_entries_batch(db, [record])

        assert calls >= 1
        assert db.scalar(
            select(EntryTag.id).where(
                EntryTag.entry_id == entry.id,
                EntryTag.tag_id == tag.id,
            )
        ) is None
        assert db.scalar(
            select(EntryTagSource.id)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == entry.id,
                EntryTagSource.source == "auto",
            )
        ) is None


def test_preview_request_cannot_lower_the_fixed_fifty_entry_target(
    authenticated_client,
):
    client, factory, headers = authenticated_client
    with factory() as db:
        for index in range(3):
            _add_entry(db, f"Preview request {index}")
        _bind_auto_tag_connection(db)
        db.commit()

    response = client.post(
        "/api/v1/auto-tag/previews",
        headers=headers,
        json={"sample_size": 1},
    )

    assert response.status_code == 422


def test_preview_api_requires_empty_cleanup_snapshot_to_be_confirmed(
    authenticated_client,
):
    client, factory, headers = authenticated_client
    with factory() as db:
        _add_entry(db, "API cleanup-gated preview")
        _bind_auto_tag_connection(db)
        db.commit()

    blocked = client.post(
        "/api/v1/auto-tag/previews",
        headers=headers,
        json={"sample_size": 50},
    )
    assert blocked.status_code == 400
    assert "cleanup" in blocked.json()["detail"].casefold()

    cleanup = client.get("/api/v1/auto-tag/cleanup-preview")
    assert cleanup.status_code == 200
    assert cleanup.json()["items"] == []
    assert cleanup.json()["reviewed"] is False
    with factory() as db:
        assert db.get(AppSetting, "auto_tag_cleanup_review_token") is None

    confirmed = client.post(
        "/api/v1/auto-tag/cleanup",
        headers=headers,
        json={
            "review_token": cleanup.json()["review_token"],
            "remove_tag_ids": [],
            "keep_tag_ids": [],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.get("/api/v1/auto-tag/cleanup-preview").json()["reviewed"] is True

    created = client.post(
        "/api/v1/auto-tag/previews",
        headers=headers,
        json={"sample_size": 50},
    )
    assert created.status_code == 202, created.text
    assert created.json()["sample_size"] == 1


def test_cleanup_api_rejects_a_stale_review_token(authenticated_client):
    client, factory, headers = authenticated_client
    stale = client.get("/api/v1/auto-tag/cleanup-preview")
    assert stale.status_code == 200
    assert stale.json()["reviewed"] is False

    with factory() as db:
        entry = _add_entry(db, "Cleanup token changed")
        tag = _add_approved_tag(db, "Late cleanup candidate")
        link = EntryTag(entry_id=entry.id, tag_id=tag.id)
        db.add(link)
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=link.id, source="legacy"),
                AutoTagRecord(
                    entry_id=entry.id,
                    source_hash=entry.source_hash,
                    status="complete",
                    attempts=1,
                    tag_ids=[tag.id],
                ),
            ]
        )
        db.commit()

    rejected = client.post(
        "/api/v1/auto-tag/cleanup",
        headers=headers,
        json={
            "review_token": stale.json()["review_token"],
            "remove_tag_ids": [],
            "keep_tag_ids": [],
        },
    )

    assert rejected.status_code == 409
    assert "changed" in rejected.json()["detail"].casefold()


def test_preview_uses_every_available_work_when_fewer_than_fifty_exist(db_factory):
    with db_factory() as db:
        entries = [_add_entry(db, f"Small preview {index}") for index in range(3)]
        _bind_auto_tag_connection(db)
        _confirm_cleanup(db)

        preview = create_preview(db, sample_size=50)

        assert preview.sample_size == len(entries) == 3
        assert set(preview.entry_ids) == {entry.id for entry in entries}


def test_preview_fills_fifty_distinct_works_past_recent_duplicate_versions(
    db_factory,
):
    with db_factory() as db:
        feed = Feed(
            title="Version-heavy feed",
            url="https://guardrail.test/version-heavy.xml",
        )
        db.add(feed)
        db.flush()
        old_entries = [
            _add_entry(
                db,
                f"Older distinct work {index}",
                published_at=utcnow() - timedelta(days=100),
            )
            for index in range(25)
        ]
        recent_entries = [
            _add_entry(db, f"Recent versioned work {index}", published_at=utcnow())
            for index in range(25)
        ]
        db.add_all(
            [
                EntryFeed(entry_id=entry.id, feed_id=feed.id)
                for entry in old_entries + recent_entries
            ]
        )
        for index, entry in enumerate(recent_entries):
            digest = hashlib.sha256(f"duplicate:{index}".encode()).hexdigest()
            duplicate = Entry(
                work_id=entry.work_id,
                version_key="v2",
                title=f"{entry.title} v2",
                summary=entry.summary,
                url=f"{entry.url}/v2",
                authors=[],
                categories=[],
                source_hash=digest,
                published_at=utcnow(),
            )
            db.add(duplicate)
            db.flush()
            db.add(EntryFeed(entry_id=duplicate.id, feed_id=feed.id))
        db.commit()
        _bind_auto_tag_connection(db)
        _confirm_cleanup(db)

        preview = create_preview(db, sample_size=50)
        selected = [db.get(Entry, entry_id) for entry_id in preview.entry_ids]

        assert preview.sample_size == 50
        assert len(selected) == 50
        assert len({entry.work_id for entry in selected if entry is not None}) == 50


def test_preview_requires_explicit_cleanup_confirmation_even_when_empty(db_factory):
    with db_factory() as db:
        _add_entry(db, "Cleanup-gated preview")
        _bind_auto_tag_connection(db)

        first = cleanup_preview(db)
        second = cleanup_preview(db)

        assert first["items"] == []
        assert first["reviewed"] is False
        assert second["review_token"] == first["review_token"]
        assert second["reviewed"] is False
        assert db.get(AppSetting, "auto_tag_cleanup_review_token") is None
        with pytest.raises(ValueError, match="Review and confirm"):
            create_preview(db, sample_size=50)

        apply_cleanup(
            db,
            remove_tag_ids=[],
            keep_tag_ids=[],
            review_token=first["review_token"],
        )

        assert cleanup_preview(db)["reviewed"] is True
        assert create_preview(db, sample_size=50).sample_size == 1


def test_preview_approval_rejects_an_underfilled_sample_after_work_count_grows(
    db_factory,
    monkeypatch,
):
    def empty_response(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        return json.dumps(
            {
                "entries": [
                    {"entry_id": article["entry_id"], "topics": []}
                    for article in request["articles"]
                ]
            }
        )

    with db_factory() as db:
        _add_entry(db, "First approval work")
        _bind_auto_tag_connection(db)
        _confirm_cleanup(db)
        preview = create_preview(db, sample_size=50)
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            empty_response,
        )
        run_auto_tag_preview(db, preview.id)
        _add_entry(db, "Second approval work")
        db.commit()

        with pytest.raises(ValueError, match="sample changed"):
            approve_preview(db, preview.id)


def test_preview_approval_requires_cleanup_snapshot_to_remain_reviewed(
    db_factory,
    monkeypatch,
):
    def empty_response(*args, **kwargs):
        request = json.loads(kwargs["user_prompt"])
        return json.dumps(
            {
                "entries": [
                    {"entry_id": article["entry_id"], "topics": []}
                    for article in request["articles"]
                ]
            }
        )

    with db_factory() as db:
        entry = _add_entry(db, "Approval cleanup gate")
        _bind_auto_tag_connection(db)
        _confirm_cleanup(db)
        preview = create_preview(db, sample_size=50)
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            empty_response,
        )
        run_auto_tag_preview(db, preview.id)
        tag = _add_approved_tag(db, "New legacy cleanup candidate")
        link = EntryTag(entry_id=entry.id, tag_id=tag.id)
        db.add(link)
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=link.id, source="legacy"),
                AutoTagRecord(
                    entry_id=entry.id,
                    source_hash=entry.source_hash,
                    status="complete",
                    attempts=1,
                    tag_ids=[tag.id],
                ),
            ]
        )
        db.commit()

        assert cleanup_preview(db)["reviewed"] is False
        with pytest.raises(ValueError, match="Review and confirm"):
            approve_preview(db, preview.id)


def test_closed_mode_prompt_offers_only_formal_tag_choices(db_factory):
    with db_factory() as db:
        entry = _add_entry(db, "Closed taxonomy prompt")
        _add_approved_tag(db, "Formal topic")
        _bind_auto_tag_connection(db)
        configure_auto_tag(db, enabled=False, growth_mode="closed")

        system_prompt, user_prompt, _tag_ids, proposal_ids = _build_batch_prompt(
            db, [entry]
        )
        payload = json.loads(user_prompt)
        example_topics = payload["required_output_schema_example"]["entries"][0][
            "topics"
        ]

        assert payload["active_proposals"] == []
        assert proposal_ids == set()
        assert {topic["kind"] for topic in example_topics} <= {"tag"}
        assert "active proposal" not in system_prompt.casefold()
        assert "new topic" not in system_prompt.casefold()


@pytest.mark.parametrize(
    "name",
    [
        "AI 中文",
        "AI машинное обучение",
        "AI تعلم آلي",
        "AI Ελληνικά",
    ],
)
def test_english_canonical_name_rejects_mixed_non_latin_scripts(name):
    assert _is_english_canonical_name(name) is False


@pytest.mark.parametrize(
    "name",
    ["Machine Learning", "GPT-4 / RAG", "AI: C++ & .NET", "ＡＩ systems"],
)
def test_english_canonical_name_accepts_ascii_names_and_nfkc_forms(name):
    assert _is_english_canonical_name(name) is True


def test_llm_topic_names_and_aliases_reject_nfkc_expansion_past_storage_limit():
    expanding_value = "\ufb03" * 120
    assert len(expanding_value) == 120
    assert len(normalize_topic_name(expanding_value)) == 360

    with pytest.raises(ValueError, match="Unicode normalization"):
        TopicChoice(
            kind="new",
            name=expanding_value,
            confidence=0.95,
        )
    with pytest.raises(ValueError, match="Unicode normalization"):
        TopicChoice(
            kind="new",
            name="Safe topic",
            aliases=[expanding_value],
            confidence=0.95,
        )


def test_tag_api_rejects_nfkc_expansion_past_normalized_storage_limit(
    authenticated_client,
):
    client, factory, headers = authenticated_client
    expanding_value = "\ufb03" * 120

    overlong_name = client.post(
        "/api/v1/tags",
        json={"name": expanding_value},
        headers=headers,
    )
    overlong_alias = client.post(
        "/api/v1/tags",
        json={"name": "Safe API topic", "aliases": [expanding_value]},
        headers=headers,
    )

    assert overlong_name.status_code == 422
    assert overlong_alias.status_code == 422
    assert "Unicode normalization" in overlong_name.text
    assert "Unicode normalization" in overlong_alias.text

    created = client.post(
        "/api/v1/tags",
        json={"name": "Unchanged API topic"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    tag_id = created.json()["id"]
    assert client.patch(
        f"/api/v1/tags/{tag_id}",
        json={"name": expanding_value},
        headers=headers,
    ).status_code == 422
    assert client.patch(
        f"/api/v1/tags/{tag_id}",
        json={"aliases": [expanding_value]},
        headers=headers,
    ).status_code == 422
    with factory() as db:
        tag = db.get(Tag, tag_id)
        assert tag is not None
        assert tag.name == "Unchanged API topic"
        assert tag.normalized_name == "unchanged api topic"


def test_nonretryable_llm_error_is_attempted_only_once_during_preview(
    db_factory,
    monkeypatch,
):
    calls = 0

    def fail_permanently(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise LLMConnectionError("invalid request", retryable=False)

    with db_factory() as db:
        _add_entry(db, "Permanent preview failure")
        _bind_auto_tag_connection(db)
        _confirm_cleanup(db)
        preview = create_preview(db, sample_size=50)
        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            fail_permanently,
        )
        monkeypatch.setattr("backend.app.auto_tag.sleep", lambda _seconds: None)

        with pytest.raises(LLMConnectionError, match="invalid request"):
            run_auto_tag_preview(db, preview.id)

        assert calls == 1


def test_promotion_backfills_matching_support_older_than_the_counting_window(
    db_factory,
    monkeypatch,
):
    with db_factory() as db:
        old_entry = _add_entry(
            db,
            "Old proposal evidence",
            published_at=utcnow() - timedelta(days=500),
        )
        recent_entries = [
            _add_entry(db, f"Recent proposal evidence {index}", published_at=utcnow())
            for index in range(2)
        ]
        proposal = TagProposal(
            name="Quantum interconnects",
            normalized_name=normalize_topic_name("Quantum interconnects"),
            description="Interconnects for distributed quantum systems.",
            status="active",
            support_count=0,
        )
        db.add(proposal)
        db.flush()
        db.add(
            TagProposalSupport(
                proposal_id=proposal.id,
                entry_id=old_entry.id,
                work_id=old_entry.work_id,
                source_hash=old_entry.source_hash,
                confidence=0.92,
            )
        )
        db.commit()
        _enable_auto_tag(db, growth_mode="threshold", promotion_threshold=2)

        def proposal_response(*args, **kwargs):
            request = json.loads(kwargs["user_prompt"])
            return json.dumps(
                {
                    "entries": [
                        {
                            "entry_id": article["entry_id"],
                            "topics": [
                                {
                                    "kind": "proposal",
                                    "id": proposal.id,
                                    "confidence": 0.94,
                                }
                            ],
                        }
                        for article in request["articles"]
                    ]
                }
            )

        monkeypatch.setattr(
            "backend.app.auto_tag.complete_feature_chat",
            proposal_response,
        )
        records = list(
            db.scalars(
                select(AutoTagRecord)
                .where(AutoTagRecord.entry_id.in_([entry.id for entry in recent_entries]))
                .order_by(AutoTagRecord.entry_id)
            )
        )

        tag_entries_batch(db, records)

        db.refresh(proposal)
        assert proposal.status == "promoted"
        assert proposal.promoted_tag_id is not None
        assert db.scalar(
            select(EntryTagSource.id)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == old_entry.id,
                EntryTag.tag_id == proposal.promoted_tag_id,
                EntryTagSource.source == "auto",
            )
        ) is not None


def test_cleanup_preview_ignores_stale_record_tag_ids(db_factory):
    with db_factory() as db:
        entry = _add_entry(db, "Stale cleanup preview")
        tag = _add_approved_tag(db, "Unrelated retained tag")
        db.add(
            AutoTagRecord(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status="complete",
                attempts=1,
                tag_ids=[tag.id],
            )
        )
        db.commit()

        preview = cleanup_preview(db)

        assert preview["inferred_auto_association_count"] == 0
        assert not any(item["tag_id"] == tag.id for item in preview["items"])


def test_cleanup_does_not_delete_a_tag_selected_only_through_stale_record_ids(
    db_factory,
):
    with db_factory() as db:
        entry = _add_entry(db, "Stale cleanup apply")
        tag = _add_approved_tag(db, "Tag without a suspected association")
        tag_id = tag.id
        db.add(
            AutoTagRecord(
                entry_id=entry.id,
                source_hash=entry.source_hash,
                status="complete",
                attempts=1,
                tag_ids=[tag_id],
            )
        )
        db.commit()

        cleanup = cleanup_preview(db)
        result = apply_cleanup(
            db,
            remove_tag_ids=[tag_id],
            keep_tag_ids=[],
            review_token=cleanup["review_token"],
        )

        assert result["removed_count"] == 0
        assert result["removed_tag_ids"] == []
        assert db.get(Tag, tag_id) is not None


def test_cleanup_retires_a_promoted_proposal_when_its_tag_is_deleted(
    db_factory,
):
    with db_factory() as db:
        entry = _add_entry(db, "Promoted cleanup lifecycle")
        tag = Tag(
            name="Promoted cleanup topic",
            normalized_name=normalize_topic_name("Promoted cleanup topic"),
            description="A promoted topic.",
            origin="auto_promoted",
            auto_assignable=True,
        )
        db.add(tag)
        db.flush()
        proposal = TagProposal(
            name=tag.name,
            normalized_name=tag.normalized_name,
            description=tag.description,
            status="promoted",
            support_count=10,
            promoted_tag_id=tag.id,
        )
        link = EntryTag(entry_id=entry.id, tag_id=tag.id)
        db.add_all([proposal, link])
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=link.id, source="legacy"),
                AutoTagRecord(
                    entry_id=entry.id,
                    source_hash=entry.source_hash,
                    status="complete",
                    attempts=1,
                    tag_ids=[tag.id],
                ),
            ]
        )
        db.commit()
        tag_id = tag.id
        proposal_id = proposal.id

        cleanup = cleanup_preview(db)
        apply_cleanup(
            db,
            remove_tag_ids=[tag_id],
            keep_tag_ids=[],
            review_token=cleanup["review_token"],
        )
        db.expire_all()

        assert db.get(Tag, tag_id) is None
        recovered = db.get(TagProposal, proposal_id)
        assert recovered is not None
        assert recovered.promoted_tag_id is None
        assert recovered.status == "retired"


def test_direct_tag_deletion_retires_its_promoted_proposal(
    authenticated_client,
):
    client, factory, headers = authenticated_client
    with factory() as db:
        tag = Tag(
            name="Directly deleted promoted topic",
            normalized_name=normalize_topic_name("Directly deleted promoted topic"),
            origin="auto_promoted",
            auto_assignable=True,
        )
        db.add(tag)
        db.flush()
        proposal = TagProposal(
            name=tag.name,
            normalized_name=tag.normalized_name,
            status="promoted",
            support_count=10,
            promoted_tag_id=tag.id,
        )
        db.add(proposal)
        db.commit()
        tag_id = tag.id
        proposal_id = proposal.id

    response = client.delete(f"/api/v1/tags/{tag_id}", headers=headers)

    assert response.status_code == 204, response.text
    with factory() as db:
        recovered = db.get(TagProposal, proposal_id)
        assert recovered is not None
        assert recovered.promoted_tag_id is None
        assert recovered.status == "retired"


def test_duplicate_entry_merge_never_rewrites_proposal_evidence_to_another_hash(
    db_factory,
):
    with db_factory() as db:
        source = _add_entry(db, "Proposal source content")
        target = _add_entry(db, "Different surviving content")
        assert source.source_hash != target.source_hash
        proposal = TagProposal(
            name="Merge-sensitive evidence",
            normalized_name=normalize_topic_name("Merge-sensitive evidence"),
            status="active",
            support_count=1,
        )
        db.add(proposal)
        db.flush()
        db.add(
            TagProposalSupport(
                proposal_id=proposal.id,
                entry_id=source.id,
                work_id=source.work_id,
                source_hash=source.source_hash,
                confidence=0.96,
            )
        )
        db.commit()
        source_id = source.id
        target_id = target.id
        target_hash = target.source_hash
        proposal_id = proposal.id

        _merge_entry_into(db, source, target)
        db.commit()

        assert db.get(Entry, source_id) is None
        assert db.scalar(
            select(TagProposalSupport.id).where(
                TagProposalSupport.proposal_id == proposal_id,
                TagProposalSupport.entry_id == target_id,
                TagProposalSupport.source_hash == target_hash,
            )
        ) is None


def test_cleanup_apply_serializes_with_an_inflight_auto_tag_write(
    db_factory,
    monkeypatch,
):
    classifier_ready = Event()
    release_classifier = Event()
    cleanup_reviewed = Event()
    release_cleanup = Event()
    writer_lock_attempted = Event()
    classification_done = Event()
    cleanup_done = Event()
    classification_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []

    with db_factory() as db:
        legacy_entry = _add_entry(db, "Cleanup serialization legacy article")
        classification_entry = _add_entry(db, "Cleanup serialization new article")
        retained_entry = _add_entry(db, "Cleanup serialization retained article")
        tag = _add_approved_tag(db, "Serialized controlled topic")
        _enable_auto_tag(db, growth_mode="closed")

        legacy_link = EntryTag(entry_id=legacy_entry.id, tag_id=tag.id)
        retained_link = EntryTag(entry_id=retained_entry.id, tag_id=tag.id)
        db.add_all([legacy_link, retained_link])
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=legacy_link.id, source="legacy"),
                EntryTagSource(entry_tag_id=retained_link.id, source="manual"),
            ]
        )
        legacy_record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == legacy_entry.id)
        )
        classification_record = db.scalar(
            select(AutoTagRecord).where(
                AutoTagRecord.entry_id == classification_entry.id
            )
        )
        assert legacy_record is not None
        assert classification_record is not None
        legacy_record.status = "complete"
        legacy_record.tag_ids = [tag.id]
        classification_record.status = "pending"
        classification_record.tag_ids = []
        db.commit()

        legacy_entry_id = legacy_entry.id
        classification_entry_id = classification_entry.id
        classification_record_id = classification_record.id
        tag_id = tag.id
        review_token = str(cleanup_preview(db)["review_token"])

    def paused_classification(_db, entries):
        assert [entry.id for entry in entries] == [classification_entry_id]
        classifier_ready.set()
        if not release_classifier.wait(5):
            raise RuntimeError("classification interleaving timed out")
        return (
            BatchClassification(
                entries=[
                    EntryClassification(
                        entry_id=classification_entry_id,
                        topics=[
                            TopicChoice(
                                kind="tag",
                                id=tag_id,
                                confidence=0.95,
                            )
                        ],
                    )
                ]
            ),
            {tag_id},
            set(),
        )

    original_cleanup_preview = cleanup_preview

    def paused_cleanup_preview(db):
        result = original_cleanup_preview(db)
        if not cleanup_reviewed.is_set():
            cleanup_reviewed.set()
            if not release_cleanup.wait(5):
                raise RuntimeError("cleanup interleaving timed out")
        return result

    import backend.app.auto_tag as auto_tag_module

    original_acquire_cleanup_snapshot = auto_tag_module.acquire_cleanup_snapshot

    def observed_acquire_cleanup_snapshot(db):
        if current_thread().name == "classification-writer":
            writer_lock_attempted.set()
        return original_acquire_cleanup_snapshot(db)

    monkeypatch.setattr(auto_tag_module, "_classify_batch", paused_classification)
    monkeypatch.setattr(auto_tag_module, "cleanup_preview", paused_cleanup_preview)
    monkeypatch.setattr(
        auto_tag_module,
        "acquire_cleanup_snapshot",
        observed_acquire_cleanup_snapshot,
    )

    def classify() -> None:
        try:
            with db_factory() as db:
                record = db.get(AutoTagRecord, classification_record_id)
                assert record is not None
                tag_entries_batch(db, [record])
        except BaseException as exc:  # pragma: no cover - asserted below
            classification_errors.append(exc)
        finally:
            classification_done.set()

    def clean_up() -> None:
        try:
            with db_factory() as db:
                apply_cleanup(
                    db,
                    remove_tag_ids=[tag_id],
                    keep_tag_ids=[],
                    review_token=review_token,
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            cleanup_errors.append(exc)
        finally:
            cleanup_done.set()

    classifier = Thread(target=classify, name="classification-writer", daemon=True)
    cleaner = Thread(target=clean_up, name="cleanup-reviewer", daemon=True)
    classifier.start()
    assert classifier_ready.wait(5)
    cleaner.start()
    try:
        assert cleanup_reviewed.wait(5)
        release_classifier.set()
        assert writer_lock_attempted.wait(5)
        assert not classification_done.wait(0.2)
    finally:
        release_classifier.set()
        release_cleanup.set()
        classifier.join(10)
        cleaner.join(10)

    assert classification_done.is_set()
    assert cleanup_done.is_set()
    assert classification_errors == []
    assert cleanup_errors == []
    with db_factory() as db:
        completed_record = db.get(AutoTagRecord, classification_record_id)
        assert completed_record is not None
        assert completed_record.status == "complete", (
            completed_record.status,
            completed_record.last_error,
        )
        assert db.scalar(
            select(EntryTagSource.id)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == legacy_entry_id,
                EntryTag.tag_id == tag_id,
                EntryTagSource.source.in_(("legacy", "auto")),
            )
        ) is None
        assert db.scalar(
            select(EntryTagSource.id)
            .join(EntryTag, EntryTag.id == EntryTagSource.entry_tag_id)
            .where(
                EntryTag.entry_id == classification_entry_id,
                EntryTag.tag_id == tag_id,
                EntryTagSource.source == "auto",
            )
        ) is not None


def test_delete_feed_locks_cleanup_snapshot_before_cascading_feed_tags(
    authenticated_client,
    monkeypatch,
):
    client, factory, headers = authenticated_client
    with factory() as db:
        entry = _add_entry(db, "Feed cascade cleanup fingerprint")
        tag = Tag(
            name="Feed cascade topic",
            normalized_name="feed cascade topic",
            origin="legacy",
            auto_assignable=True,
        )
        feed = Feed(
            title="Feed cascade source",
            url="https://guardrail.test/feed-cascade.xml",
        )
        db.add_all([tag, feed])
        db.flush()
        entry_tag = EntryTag(entry_id=entry.id, tag_id=tag.id)
        db.add(entry_tag)
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=entry_tag.id, source="legacy"),
                FeedTag(feed_id=feed.id, tag_id=tag.id),
                AutoTagRecord(
                    entry_id=entry.id,
                    source_hash=entry.source_hash,
                    status="complete",
                    attempts=1,
                    tag_ids=[tag.id],
                ),
            ]
        )
        db.commit()
        feed_id = feed.id
        tag_id = tag.id
        engine = db.get_bind()
        before = cleanup_preview(db)
        before_item = next(item for item in before["items"] if item["tag_id"] == tag_id)
        assert before_item["feed_count"] == 1
        assert before_item["deletable"] is False

    import backend.app.api as api_module

    original_acquire_cleanup_snapshot = api_module.acquire_cleanup_snapshot
    lock_acquired = False
    feed_delete_seen = False

    def observed_acquire_cleanup_snapshot(db):
        nonlocal lock_acquired
        original_acquire_cleanup_snapshot(db)
        lock_acquired = True

    def assert_lock_precedes_delete(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal feed_delete_seen
        if statement.lstrip().upper().startswith("DELETE FROM FEEDS"):
            feed_delete_seen = True
            assert lock_acquired

    monkeypatch.setattr(
        api_module,
        "acquire_cleanup_snapshot",
        observed_acquire_cleanup_snapshot,
    )
    sqlalchemy_event.listen(
        engine,
        "before_cursor_execute",
        assert_lock_precedes_delete,
    )
    try:
        response = client.delete(f"/api/v1/feeds/{feed_id}", headers=headers)
    finally:
        sqlalchemy_event.remove(
            engine,
            "before_cursor_execute",
            assert_lock_precedes_delete,
        )

    assert response.status_code == 204, response.text
    assert feed_delete_seen is True
    with factory() as db:
        assert db.get(Feed, feed_id) is None
        assert db.scalar(
            select(FeedTag.id).where(FeedTag.feed_id == feed_id)
        ) is None
        after = cleanup_preview(db)
        after_item = next(item for item in after["items"] if item["tag_id"] == tag_id)
        assert after_item["feed_count"] == 0
        assert after_item["deletable"] is True
        assert after["review_token"] != before["review_token"]


def test_proposal_listing_uses_current_supports_for_ordering_and_pagination(
    db_factory,
):
    with db_factory() as db:
        recent_one = _add_entry(db, "Proposal current evidence one", published_at=utcnow())
        recent_two = _add_entry(db, "Proposal current evidence two", published_at=utcnow())
        old_entry = _add_entry(
            db,
            "Proposal expired evidence",
            published_at=utcnow() - timedelta(days=500),
        )
        duplicate_hash = hashlib.sha256(b"proposal duplicate version").hexdigest()
        duplicate_version = Entry(
            work_id=recent_one.work_id,
            version_key="v2",
            title="Proposal current evidence one v2",
            summary="A duplicate version of the same Work.",
            url="https://guardrail.test/proposal-duplicate/v2",
            authors=[],
            categories=[],
            source_hash=duplicate_hash,
            published_at=utcnow(),
        )
        db.add(duplicate_version)
        proposals = [
            TagProposal(
                name="Stored count only",
                normalized_name="stored count only",
                status="active",
                support_count=99,
            ),
            TagProposal(
                name="Two current works",
                normalized_name="two current works",
                status="active",
                support_count=0,
            ),
            TagProposal(
                name="One current work",
                normalized_name="one current work",
                status="active",
                support_count=0,
            ),
            TagProposal(
                name="Retired topic",
                normalized_name="retired topic",
                status="retired",
                support_count=100,
            ),
        ]
        db.add_all(proposals)
        db.flush()
        db.add_all(
            [
                TagProposalSupport(
                    proposal_id=proposals[0].id,
                    entry_id=old_entry.id,
                    work_id=old_entry.work_id,
                    source_hash=old_entry.source_hash,
                    confidence=0.9,
                ),
                TagProposalSupport(
                    proposal_id=proposals[1].id,
                    entry_id=recent_one.id,
                    work_id=recent_one.work_id,
                    source_hash=recent_one.source_hash,
                    confidence=0.95,
                ),
                TagProposalSupport(
                    proposal_id=proposals[1].id,
                    entry_id=duplicate_version.id,
                    work_id=duplicate_version.work_id,
                    source_hash=duplicate_version.source_hash,
                    confidence=0.94,
                ),
                TagProposalSupport(
                    proposal_id=proposals[1].id,
                    entry_id=recent_two.id,
                    work_id=recent_two.work_id,
                    source_hash=recent_two.source_hash,
                    confidence=0.93,
                ),
                TagProposalSupport(
                    proposal_id=proposals[2].id,
                    entry_id=recent_one.id,
                    work_id=recent_one.work_id,
                    source_hash=recent_one.source_hash,
                    confidence=0.92,
                ),
                TagProposalAlias(
                    proposal_id=proposals[1].id,
                    alias="Alias B",
                    normalized_alias="alias b",
                ),
                TagProposalAlias(
                    proposal_id=proposals[1].id,
                    alias="Alias A",
                    normalized_alias="alias a",
                ),
            ]
        )
        db.commit()

        first_page = list_proposals(db, offset=0, limit=2)
        second_page = list_proposals(db, offset=1, limit=2)

        assert first_page["total"] == 4
        assert [item["name"] for item in first_page["items"]] == [
            "Two current works",
            "One current work",
        ]
        assert [item["support_count"] for item in first_page["items"]] == [2, 1]
        assert first_page["items"][0]["aliases"] == ["Alias A", "Alias B"]
        assert [item["name"] for item in second_page["items"]] == [
            "One current work",
            "Stored count only",
        ]
        assert [item["support_count"] for item in second_page["items"]] == [1, 0]


def test_proposal_listing_query_count_is_constant_for_a_large_catalog(db_factory):
    with db_factory() as db:
        proposals = [
            TagProposal(
                name=f"Query bounded candidate {index:03d}",
                normalized_name=f"query bounded candidate {index:03d}",
                status="active",
                support_count=index,
            )
            for index in range(300)
        ]
        db.add_all(proposals)
        db.flush()
        db.add_all(
            TagProposalAlias(
                proposal_id=proposal.id,
                alias=f"QBC {index:03d}",
                normalized_alias=f"qbc {index:03d}",
            )
            for index, proposal in enumerate(proposals[:60])
        )
        db.commit()
        db.expire_all()

        query_count = 0

        def count_query(*_args) -> None:
            nonlocal query_count
            query_count += 1

        engine = db.get_bind()
        sqlalchemy_event.listen(engine, "before_cursor_execute", count_query)
        try:
            result = list_proposals(db, offset=0, limit=50)
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", count_query)

        assert result["total"] == 300
        assert len(result["items"]) == 50
        assert result["items"][0]["name"] == "Query bounded candidate 000"
        assert result["items"][0]["aliases"] == ["QBC 000"]
        assert query_count <= 4
