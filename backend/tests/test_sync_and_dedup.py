from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from backend.app.models import (
    Entry,
    EntryFeed,
    Feed,
    NetworkProxyConfig,
    SyncRun,
    Translation,
    Work,
    utcnow,
)
from backend.app.parsing import ParsedEntry
from backend.app.sync import discover_feeds, sync_due_feeds, sync_feed, upsert_entry


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
