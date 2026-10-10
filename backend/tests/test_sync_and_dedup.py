from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select

from backend.app.models import (
    AutoTagRecord,
    AutoTagSuppression,
    Entry,
    EntryFeed,
    EntryTag,
    EntryTagSource,
    Feed,
    NetworkProxyConfig,
    SyncRun,
    Tag,
    TagProposal,
    TagProposalSupport,
    Translation,
    Work,
    utcnow,
)
from backend.app.parsing import ParsedEntry
from backend.app.sync import (
    _merge_entry_into,
    _merge_work_into,
    discover_feeds,
    sync_due_feeds,
    sync_feed,
    upsert_entry,
)


@pytest.fixture(autouse=True)
def public_feed_dns(monkeypatch):
    """Keep injected MockTransport clients behind the production DNS boundary."""

    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda _hostname, _port: ("93.184.216.34",),
    )


def parsed(arxiv_id: str, version: int, doi: str | None = "10.1000/shared") -> ParsedEntry:
    base = arxiv_id.split("v", 1)[0]
    return ParsedEntry(
        guid=arxiv_id,
        title=f"Paper version {version}",
        summary="Summary",
        content=None,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        authors=["Alice"],
        categories=["quant-ph"],
        arxiv_id=arxiv_id,
        arxiv_base_id=base,
        arxiv_version=version,
        doi=doi,
        announce_type="replace" if version > 1 else "new",
        published_at=datetime(2026, 7, 20),
        updated_at=datetime(2026, 7, 20 + version),
    )


def test_cross_source_dedup_and_arxiv_versions(db_factory):
    with db_factory() as db:
        first = Feed(title="One", url="https://one.test/rss")
        second = Feed(title="Two", url="https://two.test/rss")
        db.add_all([first, second])
        db.commit()
        entry1, action1 = upsert_entry(db, first, parsed("2607.12345v1", 1))
        same, action2 = upsert_entry(db, second, parsed("2607.12345v1", 1))
        version2, action3 = upsert_entry(db, first, parsed("2607.12345v2", 2))
        db.commit()
        assert (action1, action2, action3) == ("created", "unchanged", "created")
        assert same.id == entry1.id
        assert version2.work_id == entry1.work_id
        assert db.scalar(select(func.count()).select_from(Work)) == 1
        assert db.scalar(select(func.count()).select_from(Entry)) == 2
        assert db.scalar(select(func.count()).select_from(EntryFeed)) == 3


def test_date_only_refresh_backfills_and_corrects_dates_without_reprocessing(db_factory):
    with db_factory() as db:
        feed = Feed(title="Journal", url="https://journal.test/rss")
        db.add(feed)
        db.commit()
        original = ParsedEntry(
            guid="paper", title="Paper", summary="Abstract", content=None,
            url="https://journal.test/paper",
        )
        entry, _ = upsert_entry(db, feed, original)
        translation = entry.translation
        translation.status = "complete"
        translation.title = "Translated paper"
        record = AutoTagRecord(
            entry_id=entry.id, source_hash=entry.source_hash, status="complete", tag_ids=[]
        )
        db.add(record)
        db.commit()
        collected_at = entry.created_at
        dated = replace(
            original, published_at=datetime(2026, 10, 8), updated_at=datetime(2026, 10, 9)
        )

        same, action = upsert_entry(db, feed, dated)
        db.commit()
        assert same.id == entry.id
        assert action == "updated"
        assert same.published_at == dated.published_at
        assert same.source_updated_at == dated.updated_at
        assert same.created_at == collected_at
        assert same.source_hash == original.source_hash
        assert translation.status == record.status == "complete"
        assert translation.title == "Translated paper"
        assert upsert_entry(db, feed, dated)[1] == "unchanged"

        corrected = replace(dated, published_at=datetime(2026, 10, 7))
        assert upsert_entry(db, feed, corrected)[1] == "updated"
        assert entry.published_at == corrected.published_at
        assert upsert_entry(db, feed, original)[1] == "unchanged"
        assert entry.published_at == corrected.published_at
        assert entry.source_updated_at == dated.updated_at


def test_mirror_can_fill_missing_dates_but_cannot_overwrite_primary_dates(db_factory):
    with db_factory() as db:
        feed = Feed(title="Journal", url="https://journal.test/rss")
        mirror = Feed(title="Mirror", url="https://mirror.test/rss")
        db.add_all([feed, mirror])
        db.commit()
        original = ParsedEntry(
            guid="paper", title="Paper", summary="Abstract", content=None,
            url="https://journal.test/paper", published_at=datetime(2026, 10, 8),
        )
        entry, _ = upsert_entry(db, feed, original)
        incoming = replace(
            original, published_at=datetime(2026, 10, 9), updated_at=datetime(2026, 10, 10)
        )
        same, action = upsert_entry(db, mirror, incoming)
        assert same.id == entry.id
        assert action == "updated"
        assert entry.published_at == original.published_at
        assert entry.source_updated_at == incoming.updated_at
        assert upsert_entry(db, mirror, replace(incoming, updated_at=datetime(2026, 10, 11)))[1] == "unchanged"
        assert entry.source_updated_at == incoming.updated_at


def test_upsert_locks_cleanup_snapshot_before_entry_insert_and_update(db_factory):
    """Keep sync's SQL lock order aligned with automatic classification."""

    with db_factory() as db:
        feed = Feed(title="Ordered", url="https://ordered.test/rss")
        db.add(feed)
        db.commit()
        engine = db.get_bind()
        statements: list[tuple[str, object]] = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(
                (" ".join(statement.upper().split()), _parameters)
            )

        def assert_cleanup_lock_precedes(entry_prefix: str) -> None:
            cleanup_lock_index = next(
                index
                for index, (statement, parameters) in enumerate(statements)
                if statement.startswith("UPDATE APP_SETTINGS SET")
                and "auto_tag_cleanup_snapshot_lock" in repr(parameters)
            )
            entry_dml_index = next(
                index
                for index, (statement, _parameters) in enumerate(statements)
                if statement.startswith(entry_prefix)
            )
            first_work_or_entry_index = next(
                index
                for index, (statement, _parameters) in enumerate(statements)
                if (
                    " FROM WORKS " in f" {statement} "
                    or statement.startswith("INSERT INTO WORKS")
                    or " FROM ENTRIES " in f" {statement} "
                    or statement.startswith("INSERT INTO ENTRIES")
                    or statement.startswith("UPDATE ENTRIES")
                )
            )
            assert cleanup_lock_index < first_work_or_entry_index <= entry_dml_index

        sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
        try:
            original = parsed("2607.54321v1", 1)
            entry, action = upsert_entry(db, feed, original)
            assert action == "created"
            assert_cleanup_lock_precedes("INSERT INTO ENTRIES")
            db.commit()

            statements.clear()
            revised = replace(original, title="Revised paper title")
            same_entry, action = upsert_entry(db, feed, revised)
            assert same_entry.id == entry.id
            assert action == "updated"
            assert_cleanup_lock_precedes("UPDATE ENTRIES")
            db.commit()
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)


def test_doi_first_then_arxiv_promotes_same_work(db_factory):
    with db_factory() as db:
        feed = Feed(title="Journal", url="https://journal.test/rss")
        arxiv = Feed(title="arXiv", url="https://arxiv.test/rss")
        db.add_all([feed, arxiv])
        db.commit()
        journal = ParsedEntry(
            guid="doi-item",
            title="Journal version",
            summary="S",
            content=None,
            url="https://journal.test/paper",
            doi="10.1000/shared",
        )
        first, _ = upsert_entry(db, feed, journal)
        second, _ = upsert_entry(db, arxiv, parsed("2607.12345v1", 1))
        db.commit()
        assert first.work_id == second.work_id
        assert first.id == second.id
        assert db.scalar(select(func.count()).select_from(Work)) == 1
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        assert db.scalar(select(func.count()).select_from(EntryFeed)) == 2
        assert second.version_key == "v1"


def test_arxiv_first_then_doi_feed_reuses_the_visible_entry(db_factory):
    with db_factory() as db:
        arxiv = Feed(title="arXiv", url="https://arxiv.test/rss")
        journal = Feed(title="Journal", url="https://journal.test/rss")
        db.add_all([arxiv, journal])
        db.commit()
        first, _ = upsert_entry(db, arxiv, parsed("2607.12345v1", 1))
        journal_item = ParsedEntry(
            guid="journal-doi-item",
            title="Different journal title",
            summary="Different journal summary",
            content=None,
            url="https://journal.test/paper",
            doi="10.1000/shared",
        )
        second, _ = upsert_entry(db, journal, journal_item)
        db.commit()
        assert second.id == first.id
        assert second.title == "Paper version 1"
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        assert db.scalar(select(func.count()).select_from(EntryFeed)) == 2
        translation = db.scalar(select(Translation).where(Translation.entry_id == first.id))
        assert translation is not None
        assert translation.source_hash == first.source_hash


def test_repolling_secondary_doi_feed_does_not_replace_arxiv_metadata(db_factory):
    with db_factory() as db:
        arxiv = Feed(title="arXiv", url="https://arxiv.test/rss")
        journal = Feed(title="Journal", url="https://journal.test/rss")
        db.add_all([arxiv, journal])
        db.commit()
        first, _ = upsert_entry(db, arxiv, parsed("2607.12345v1", 1))
        arxiv_hash = first.source_hash
        journal_item = ParsedEntry(
            guid="journal-doi-item",
            title="Different journal title",
            summary="Different journal summary",
            content=None,
            url="https://journal.test/paper",
            doi="10.1000/shared",
        )
        upsert_entry(db, journal, journal_item)
        db.commit()

        same, action = upsert_entry(db, journal, journal_item)
        db.commit()

        assert same.id == first.id
        assert action == "unchanged"
        assert same.title == "Paper version 1"
        assert same.source_hash == arxiv_hash
        assert same.translation is not None
        assert same.translation.source_hash == arxiv_hash


def test_late_doi_bridge_merges_existing_works_and_preserves_versions(db_factory):
    with db_factory() as db:
        arxiv = Feed(title="arXiv", url="https://arxiv.test/rss")
        journal = Feed(title="Journal", url="https://journal.test/rss")
        db.add_all([arxiv, journal])
        db.commit()

        arxiv_v1 = parsed("2607.12345v1", 1, doi=None)
        first, _ = upsert_entry(db, arxiv, arxiv_v1)
        journal_item = ParsedEntry(
            guid="journal-doi-item",
            title="Journal title",
            summary="Journal summary",
            content=None,
            url="https://journal.test/paper",
            doi="10.1000/shared",
        )
        journal_entry, _ = upsert_entry(db, journal, journal_item)
        moved_translation = Translation(
            entry_id=journal_entry.id,
            source_hash=journal_entry.source_hash,
            language="fr",
            provider="manual",
            title="Titre",
            summary="Résumé",
            status="complete",
        )
        db.add(moved_translation)
        db.commit()
        assert first.work_id != journal_entry.work_id
        assert db.scalar(select(func.count()).select_from(Work)) == 2

        replacement, action = upsert_entry(
            db,
            arxiv,
            parsed("2607.12345v2", 2, doi="10.1000/shared"),
        )
        db.commit()

        assert action == "created"
        assert replacement.version_key == "v2"
        assert db.scalar(select(func.count()).select_from(Work)) == 1
        assert db.scalar(select(func.count()).select_from(Entry)) == 2
        assert db.scalar(select(func.count()).select_from(EntryFeed)) == 3
        assert db.scalar(select(func.count()).select_from(Translation)) == 3

        original = db.scalar(
            select(Entry).where(Entry.work_id == replacement.work_id, Entry.version_key == "v1")
        )
        assert original is not None
        assert original.title == "Paper version 1"
        db.refresh(moved_translation)
        assert moved_translation.entry_id == original.id
        assert moved_translation.source_hash == original.source_hash
        assert moved_translation.status == "pending"
        assert moved_translation.title is None
        assert moved_translation.summary is None
        journal_again, journal_action = upsert_entry(db, journal, journal_item)
        db.commit()
        assert journal_again.id == original.id
        assert journal_action == "unchanged"
        assert journal_again.title == "Paper version 1"


def test_duplicate_entry_merge_preserves_auto_tag_governance_state(db_factory):
    with db_factory() as db:
        source_work = Work(dedup_key="governance-source")
        target_work = Work(dedup_key="governance-target")
        db.add_all([source_work, target_work])
        db.flush()
        source = Entry(
            work_id=source_work.id,
            version_key="default",
            title="Duplicate source",
            summary="Source summary",
            url="https://source.test/paper",
            source_hash="source-hash",
        )
        target = Entry(
            work_id=target_work.id,
            version_key="default",
            title="Canonical target",
            summary="Target summary",
            url="https://target.test/paper",
            source_hash="target-hash",
        )
        provenance_tag = Tag(
            name="Quantum Computing",
            normalized_name="quantum computing",
        )
        suppressed_tag = Tag(
            name="Machine Learning",
            normalized_name="machine learning",
        )
        shared_proposal = TagProposal(
            name="Quantum Networks",
            normalized_name="quantum networks",
            status="active",
        )
        moved_proposal = TagProposal(
            name="Fault Tolerance",
            normalized_name="fault tolerance",
            status="active",
        )
        db.add_all(
            [
                source,
                target,
                provenance_tag,
                suppressed_tag,
                shared_proposal,
                moved_proposal,
            ]
        )
        db.flush()

        source_link = EntryTag(entry_id=source.id, tag_id=provenance_tag.id)
        target_link = EntryTag(entry_id=target.id, tag_id=provenance_tag.id)
        suppressed_link = EntryTag(entry_id=target.id, tag_id=suppressed_tag.id)
        db.add_all([source_link, target_link, suppressed_link])
        db.flush()
        db.add_all(
            [
                EntryTagSource(entry_tag_id=source_link.id, source="manual"),
                EntryTagSource(
                    entry_tag_id=source_link.id,
                    source="auto",
                    confidence=0.93,
                    policy_version="source-policy",
                ),
                EntryTagSource(entry_tag_id=source_link.id, source="legacy"),
                EntryTagSource(entry_tag_id=target_link.id, source="manual"),
                EntryTagSource(
                    entry_tag_id=target_link.id,
                    source="auto",
                    confidence=0.81,
                    policy_version="target-policy",
                ),
                EntryTagSource(entry_tag_id=suppressed_link.id, source="manual"),
                EntryTagSource(
                    entry_tag_id=suppressed_link.id,
                    source="auto",
                    confidence=0.88,
                    policy_version="target-policy",
                ),
                AutoTagSuppression(
                    entry_id=source.id,
                    tag_id=suppressed_tag.id,
                    reason="removed by user",
                ),
                AutoTagRecord(
                    entry_id=source.id,
                    source_hash=source.source_hash,
                    status="complete",
                    attempts=2,
                    tag_ids=[provenance_tag.id],
                ),
                AutoTagRecord(
                    entry_id=target.id,
                    source_hash="stale-target-hash",
                    status="failed",
                    attempts=5,
                    last_error="stale failure",
                    next_retry_at=utcnow() + timedelta(hours=1),
                    tag_ids=[suppressed_tag.id],
                ),
                TagProposalSupport(
                    proposal_id=shared_proposal.id,
                    entry_id=source.id,
                    work_id=source_work.id,
                    source_hash=source.source_hash,
                    confidence=0.96,
                ),
                TagProposalSupport(
                    proposal_id=shared_proposal.id,
                    entry_id=target.id,
                    work_id=target_work.id,
                    source_hash="stale-target-hash",
                    confidence=0.82,
                ),
                TagProposalSupport(
                    proposal_id=moved_proposal.id,
                    entry_id=source.id,
                    work_id=source_work.id,
                    source_hash=source.source_hash,
                    confidence=0.91,
                ),
            ]
        )
        db.flush()

        _merge_entry_into(db, source, target)
        db.commit()

        assert db.get(Entry, source.id) is None
        merged_link = db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == target.id,
                EntryTag.tag_id == provenance_tag.id,
            )
        )
        assert merged_link is not None
        merged_sources = list(
            db.scalars(
                select(EntryTagSource)
                .where(EntryTagSource.entry_tag_id == merged_link.id)
                .order_by(EntryTagSource.source)
            )
        )
        assert [row.source for row in merged_sources] == ["auto", "legacy", "manual"]
        merged_auto = next(row for row in merged_sources if row.source == "auto")
        assert merged_auto.confidence == pytest.approx(0.93)
        assert merged_auto.policy_version == "source-policy"

        suppression = db.scalar(
            select(AutoTagSuppression).where(
                AutoTagSuppression.entry_id == target.id,
                AutoTagSuppression.tag_id == suppressed_tag.id,
            )
        )
        assert suppression is not None
        suppressed_sources = list(
            db.scalars(
                select(EntryTagSource)
                .join(EntryTag, EntryTagSource.entry_tag_id == EntryTag.id)
                .where(
                    EntryTag.entry_id == target.id,
                    EntryTag.tag_id == suppressed_tag.id,
                )
            )
        )
        assert [row.source for row in suppressed_sources] == ["manual"]

        record = db.scalar(
            select(AutoTagRecord).where(AutoTagRecord.entry_id == target.id)
        )
        assert record is not None
        assert record.source_hash == target.source_hash
        assert record.status == "pending"
        assert record.attempts == 0
        assert record.last_error is None
        assert record.next_retry_at is None
        assert record.tag_ids == [provenance_tag.id]
        assert db.scalar(select(func.count()).select_from(AutoTagRecord)) == 1

        supports = list(
            db.scalars(
                select(TagProposalSupport)
                .where(TagProposalSupport.entry_id == target.id)
                .order_by(TagProposalSupport.proposal_id)
            )
        )
        # Neither the source nor the pre-existing target evidence was produced
        # for the surviving content hash, so both are discarded. The pending
        # AutoTagRecord above will rebuild evidence from the target content.
        assert supports == []


def test_work_merge_remaps_proposal_support_before_source_work_is_deleted(db_factory):
    with db_factory() as db:
        source_work = Work(dedup_key="proposal-support-source")
        target_work = Work(dedup_key="proposal-support-target")
        proposal = TagProposal(
            name="Quantum Sensing",
            normalized_name="quantum sensing",
            status="active",
        )
        db.add_all([source_work, target_work, proposal])
        db.flush()
        entry = Entry(
            work_id=source_work.id,
            version_key="v2",
            title="Second version",
            summary="Summary",
            url="https://example.test/v2",
            source_hash="version-two-hash",
        )
        db.add(entry)
        db.flush()
        support = TagProposalSupport(
            proposal_id=proposal.id,
            entry_id=entry.id,
            work_id=source_work.id,
            source_hash=entry.source_hash,
            confidence=0.9,
        )
        db.add(support)
        db.flush()
        support_id = support.id

        _merge_work_into(db, source_work, target_work)
        db.commit()

        assert db.get(Work, source_work.id) is None
        db.refresh(entry)
        assert entry.work_id == target_work.id
        remapped_support = db.get(TagProposalSupport, support_id)
        assert remapped_support is not None
        assert remapped_support.entry_id == entry.id
        assert remapped_support.work_id == target_work.id
        assert remapped_support.source_hash == entry.source_hash


def test_sync_uses_conditional_headers_and_304_is_idempotent(db_factory, settings):
    calls = []
    rss = b"""<rss version="2.0"><channel><title>Feed</title>
      <item><guid>x</guid><title>One</title><link>https://example.test/one</link>
      <description>Summary</description></item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                content=rss,
                headers={"content-type": "application/rss+xml", "etag": '"v1"', "last-modified": "Fri, 24 Jul 2026 12:00:00 GMT"},
            )
        assert request.headers["if-none-match"] == '"v1"'
        assert "if-modified-since" in request.headers
        return httpx.Response(304)

    with db_factory() as db, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        feed = Feed(title="Feed", url="https://example.test/rss")
        db.add(feed)
        db.commit()
        first = sync_feed(db, feed, settings, client=client)
        second = sync_feed(db, feed, settings, client=client)
        assert first.status == "success"
        assert first.created_count == 1
        assert second.status == "not_modified"
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        assert db.scalar(select(func.count()).select_from(Translation)) == 1


def test_sync_batch_acquires_one_cleanup_lock_before_entry_writes(
    db_factory,
    settings,
):
    rss = b"""<rss version="2.0"><channel><title>Feed</title>
      <item><guid>one</guid><title>One</title><link>https://example.test/one</link>
      <description>First</description></item>
      <item><guid>two</guid><title>Two</title><link>https://example.test/two</link>
      <description>Second</description></item></channel></rss>"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=rss,
            headers={"content-type": "application/rss+xml"},
        )

    with db_factory() as db, httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as client:
        feed = Feed(title="Batch", url="https://example.test/rss")
        db.add(feed)
        db.commit()
        engine = db.get_bind()
        statements: list[tuple[str, object]] = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(
                (" ".join(statement.upper().split()), parameters)
            )

        sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
        try:
            run = sync_feed(db, feed, settings, client=client)
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)

        cleanup_lock_indexes = [
            index
            for index, (statement, parameters) in enumerate(statements)
            if statement.startswith("UPDATE APP_SETTINGS SET")
            and "auto_tag_cleanup_snapshot_lock" in repr(parameters)
        ]
        entry_insert_indexes = [
            index
            for index, (statement, _parameters) in enumerate(statements)
            if statement.startswith("INSERT INTO ENTRIES")
        ]
        assert run.status == "success"
        assert run.created_count == 2
        assert len(cleanup_lock_indexes) == 1
        assert len(entry_insert_indexes) == 2
        assert cleanup_lock_indexes[0] < min(entry_insert_indexes)


def test_feed_failure_is_recorded_without_corrupting_successful_feed(db_factory, settings):
    good_rss = b"<rss version='2.0'><channel><title>G</title></channel></rss>"
    with db_factory() as db:
        bad = Feed(title="Bad", url="https://bad.test/rss")
        good = Feed(title="Good", url="https://good.test/rss")
        db.add_all([bad, good])
        db.commit()
        with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503))) as client:
            failed = sync_feed(db, bad, settings, client=client)
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=good_rss, headers={"content-type": "application/rss+xml"})
            )
        ) as client:
            succeeded = sync_feed(db, good, settings, client=client)
        assert failed.status == "failed"
        assert bad.error_count == 1
        assert bad.next_fetch_at is not None
        assert succeeded.status == "success"
        assert good.error_count == 0


def test_deleting_feed_during_sync_finishes_its_sync_run(db_factory, settings):
    feed_id = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        with db_factory() as deleting_db:
            deleting_feed = deleting_db.get(Feed, feed_id)
            assert deleting_feed is not None
            deleting_db.delete(deleting_feed)
            deleting_db.commit()
        return httpx.Response(304)

    with db_factory() as db:
        feed = Feed(title="Deleted in flight", url="https://deleted.test/rss")
        db.add(feed)
        db.commit()
        feed_id = feed.id
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            run = sync_feed(db, feed, settings, client=client)

        assert db.get(Feed, feed_id) is None
        assert run.status == "failed"
        assert run.feed_id is None
        assert run.finished_at is not None
        assert run.error == "Feed was deleted while synchronization was running"
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.finished_at is not None


def test_route_setup_failure_finishes_sync_run(
    db_factory, settings, monkeypatch
):
    def fail_route(*_args, **_kwargs):
        raise RuntimeError("proxy secret is unavailable")

    monkeypatch.setattr("backend.app.sync.http_route_for_feed", fail_route)
    with db_factory() as db:
        feed = Feed(title="Route failure", url="https://route-failure.test/rss")
        db.add(feed)
        db.commit()
        run = sync_feed(db, feed, settings)

        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.error == "proxy secret is unavailable"
        db.refresh(feed)
        assert feed.error_count == 1
        assert feed.last_error == "proxy secret is unavailable"


def test_feed_discovery_uses_the_configured_global_network_route(
    db_factory, settings, monkeypatch
):
    html = b"""<html><head>
      <link rel='alternate' type='application/rss+xml' href='/rss.xml' title='Safe feed'>
      <link rel='alternate' type='application/rss+xml' href='javascript:alert(1)' title='Bad'>
    </head></html>"""
    captured: dict[str, object] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=html,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    def fake_client(*_args, **kwargs):
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend.app.sync.httpx.Client", fake_client)
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda _hostname, _port: ("93.184.216.34",),
    )
    with db_factory() as db:
        db.add(
            NetworkProxyConfig(
                id=1,
                enabled=False,
                url="",
                global_mode="system",
                translation_service_modes={},
            )
        )
        db.commit()
        found = discover_feeds("https://reader.example/page", settings, db)

    assert captured["trust_env"] is True
    assert found == [
        {
            "url": "https://reader.example/rss.xml",
            "title": "Safe feed",
            "site_url": "https://reader.example/page",
        }
    ]


def test_sync_due_feeds_force_syncs_all_enabled_feeds_ignoring_due(db_factory, settings, monkeypatch):
    rss = b"<rss version='2.0'><channel><title>F</title></channel></rss>"

    def handler(_request):
        return httpx.Response(200, content=rss, headers={"content-type": "application/rss+xml"})

    real_client = httpx.Client

    def fake_client(*_args, **_kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend.app.sync.httpx.Client", fake_client)
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda _hostname, _port: ("93.184.216.34",),
    )
    with db_factory() as db:
        future = utcnow() + timedelta(hours=10)
        first = Feed(title="One", url="https://one.test/rss", next_fetch_at=future)
        second = Feed(title="Two", url="https://two.test/rss", next_fetch_at=future)
        disabled = Feed(title="Disabled", url="https://disabled.test/rss", enabled=False, next_fetch_at=future)
        db.add_all([first, second, disabled])
        db.commit()
        runs = sync_due_feeds(db, settings, force=True)
        assert {run.feed_id for run in runs} == {first.id, second.id}
        for feed in (first, second):
            db.refresh(feed)
            assert feed.last_checked_at is not None
        assert all(run.status == "success" for run in runs)
