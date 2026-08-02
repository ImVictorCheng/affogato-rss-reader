from __future__ import annotations

from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import (
    AppSetting,
    BriefItem,
    Entry,
    EntryDomain,
    EntryFeed,
    EntryTag,
    Feed,
    ReadingState,
    SyncRun,
    Translation,
    Work,
    utcnow,
)
from .translation import TRANSLATION_RECORD_PROVIDER, get_translation_record
from .parsing import ParsedEntry, canonicalize_url, parse_feed, parse_untrusted_html
from .network_proxy import http_route_for_feed

FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/xml",
    "text/xml",
}


def validate_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    return url


def discover_feeds(url: str, settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    validate_http_url(url)
    with httpx.Client(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "AffogatoRSSReader/0.1 (+self-hosted)"},
        trust_env=False,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";")[0].lower()
    if content_type in FEED_TYPES or response.content.lstrip().startswith((b"<?xml", b"<rss", b"<feed")):
        metadata, _ = parse_feed(response.content, content_type)
        return [{"url": str(response.url), "title": metadata["title"] or str(response.url), "site_url": metadata["site_url"] or url}]
    soup = parse_untrusted_html(response.text)
    found: list[dict] = []
    seen: set[str] = set()
    for link in soup.find_all("link", href=True):
        rel = {str(v).lower() for v in (link.get("rel") or [])}
        media_type = str(link.get("type") or "").lower()
        if "alternate" not in rel or media_type not in FEED_TYPES:
            continue
        feed_url = canonicalize_url(urljoin(str(response.url), link["href"]))
        if feed_url in seen:
            continue
        seen.add(feed_url)
        found.append({"url": feed_url, "title": link.get("title") or feed_url, "site_url": str(response.url)})
    return found


def _equivalent_entry(db: Session, work_id: int, version_key: str) -> Entry | None:
    entry = db.scalar(
        select(Entry).where(Entry.work_id == work_id, Entry.version_key == version_key)
    )
    if entry is not None:
        return entry
    counterpart = {"default": "v1", "v1": "default"}.get(version_key)
    if counterpart:
        return db.scalar(
            select(Entry).where(Entry.work_id == work_id, Entry.version_key == counterpart)
        )
    return None


def _merge_entry_into(db: Session, source: Entry, target: Entry) -> None:
    """Move user, source, and brief relationships while collapsing a duplicate."""
    for link in list(db.scalars(select(EntryFeed).where(EntryFeed.entry_id == source.id))):
        existing = db.scalar(
            select(EntryFeed).where(
                EntryFeed.entry_id == target.id,
                EntryFeed.feed_id == link.feed_id,
            )
        )
        if existing:
            db.delete(link)
        else:
            link.entry_id = target.id

    for state in list(db.scalars(select(ReadingState).where(ReadingState.entry_id == source.id))):
        existing = db.scalar(
            select(ReadingState).where(
                ReadingState.entry_id == target.id,
                ReadingState.owner_id == state.owner_id,
            )
        )
        if existing:
            existing.read = existing.read or state.read
            existing.starred = existing.starred or state.starred
            existing.later = existing.later or state.later
            existing.archived = existing.archived or state.archived
            existing.updated_at = max(existing.updated_at, state.updated_at)
            db.delete(state)
        else:
            state.entry_id = target.id

    for source_translation in list(
        db.scalars(select(Translation).where(Translation.entry_id == source.id))
    ):
        target_translation = db.scalar(
            select(Translation).where(
                Translation.entry_id == target.id,
                Translation.language == source_translation.language,
                Translation.provider == source_translation.provider,
            )
        )
        if target_translation:
            # Removing through the relationship lets delete-orphan update the
            # in-memory collection before the source entry is deleted below.
            # Calling db.delete() here leaves the deleted row in
            # source.translations, so deleting the source can schedule the same
            # translation for deletion a second time.
            source.translations.remove(source_translation)
        else:
            # Assign through the relationship so both parent collections stay
            # consistent before the source entry's delete cascade runs.
            source_translation.entry = target
            if source_translation.source_hash != target.source_hash:
                source_translation.source_hash = target.source_hash
                source_translation.status = "pending"
                source_translation.title = None
                source_translation.summary = None
                source_translation.last_error = None
                source_translation.next_retry_at = None

    for entry_domain in list(
        db.scalars(select(EntryDomain).where(EntryDomain.entry_id == source.id))
    ):
        existing = db.scalar(
            select(EntryDomain).where(
                EntryDomain.entry_id == target.id,
                EntryDomain.domain_id == entry_domain.domain_id,
            )
        )
        if existing:
            db.delete(entry_domain)
        else:
            entry_domain.entry_id = target.id

    for entry_tag in list(db.scalars(select(EntryTag).where(EntryTag.entry_id == source.id))):
        existing = db.scalar(
            select(EntryTag).where(
                EntryTag.entry_id == target.id,
                EntryTag.tag_id == entry_tag.tag_id,
            )
        )
        if existing:
            db.delete(entry_tag)
        else:
            entry_tag.entry_id = target.id

    for item in list(db.scalars(select(BriefItem).where(BriefItem.entry_id == source.id))):
        existing = db.scalar(
            select(BriefItem).where(
                BriefItem.brief_id == item.brief_id,
                BriefItem.entry_id == target.id,
            )
        )
        if existing:
            existing.position = min(existing.position, item.position)
            db.delete(item)
        else:
            item.entry_id = target.id

    db.flush()
    db.delete(source)
    db.flush()


def _merge_work_into(db: Session, source: Work, target: Work) -> None:
    for entry in list(db.scalars(select(Entry).where(Entry.work_id == source.id).order_by(Entry.id))):
        equivalent = _equivalent_entry(db, target.id, entry.version_key)
        if equivalent:
            _merge_entry_into(db, entry, equivalent)
        else:
            entry.work_id = target.id
            db.flush()
    db.delete(source)
    db.flush()


def _get_or_create_work(db: Session, parsed: ParsedEntry) -> Work:
    candidates = [Work.dedup_key == parsed.dedup_key]
    if parsed.arxiv_base_id:
        candidates.append(Work.arxiv_base_id == parsed.arxiv_base_id)
    if parsed.doi:
        candidates.append(Work.doi == parsed.doi)
    canonical = canonicalize_url(parsed.url)
    if canonical:
        candidates.append(Work.canonical_url == canonical)
    works = list(db.scalars(select(Work).where(or_(*candidates)).order_by(Work.id)))
    if works:
        if parsed.arxiv_base_id:
            work = next(
                (row for row in works if row.arxiv_base_id == parsed.arxiv_base_id),
                works[0],
            )
        elif parsed.doi:
            work = next((row for row in works if row.doi == parsed.doi), works[0])
        elif canonical:
            work = next(
                (row for row in works if row.canonical_url == canonical),
                works[0],
            )
        else:
            work = works[0]
        for duplicate in works:
            if duplicate.id != work.id:
                _merge_work_into(db, duplicate, work)
        if parsed.arxiv_base_id and not work.arxiv_base_id:
            work.arxiv_base_id = parsed.arxiv_base_id
            # Promote the stable identity when richer metadata arrives later
            # (for example, a DOI feed is seen before the arXiv feed).
            work.dedup_key = parsed.dedup_key
        if parsed.doi and not work.doi:
            work.doi = parsed.doi
        if canonical and not work.canonical_url:
            work.canonical_url = canonical
        return work
    work = Work(
        dedup_key=parsed.dedup_key,
        arxiv_base_id=parsed.arxiv_base_id,
        doi=parsed.doi,
        canonical_url=canonicalize_url(parsed.url),
    )
    db.add(work)
    db.flush()
    return work


def _find_entry_for_work(db: Session, work: Work, parsed: ParsedEntry) -> tuple[Entry | None, bool]:
    """Find the exact version, or the canonical cross-source equivalent.

    A journal item identified by DOI has no arXiv version and therefore uses
    ``default``. If the same work later appears as arXiv v1 (or in the reverse
    order), both sources must point at one visible entry. Later arXiv
    replacements remain separate entries.
    """
    entry = db.scalar(
        select(Entry).where(Entry.work_id == work.id, Entry.version_key == parsed.version_key)
    )
    if entry is not None:
        return entry, False
    if parsed.version_key == "v1":
        entry = db.scalar(
            select(Entry).where(Entry.work_id == work.id, Entry.version_key == "default").limit(1)
        )
        return entry, entry is not None
    if parsed.version_key == "default":
        entry = db.scalar(
            select(Entry)
            .where(Entry.work_id == work.id)
            .order_by(Entry.arxiv_version.asc(), Entry.id.asc())
            .limit(1)
        )
        return entry, entry is not None
    return None, False


def _source_may_update_entry(
    db: Session,
    entry: Entry,
    parsed: ParsedEntry,
    existing_link: EntryFeed | None,
    cross_source_fallback: bool,
) -> bool:
    """Keep merged entries from oscillating between source-specific metadata."""
    if cross_source_fallback and parsed.arxiv_base_id is not None:
        return True
    if existing_link is None:
        return False
    if entry.arxiv_id:
        # Once richer arXiv metadata has promoted a merged entry, a journal
        # mirror must not replace its title/abstract on every polling cycle.
        return parsed.arxiv_base_id is not None
    first_link_id = db.scalar(
        select(EntryFeed.id)
        .where(EntryFeed.entry_id == entry.id)
        .order_by(EntryFeed.id)
        .limit(1)
    )
    return first_link_id == existing_link.id


def upsert_entry(db: Session, feed: Feed, parsed: ParsedEntry, translation_enabled: bool = True) -> tuple[Entry, str]:
    work = _get_or_create_work(db, parsed)
    entry, cross_source_fallback = _find_entry_for_work(db, work, parsed)
    existing_link = None
    if entry is not None:
        existing_link = db.scalar(
            select(EntryFeed).where(EntryFeed.entry_id == entry.id, EntryFeed.feed_id == feed.id)
        )
    action = "unchanged"
    if entry is None:
        entry = Entry(
            work_id=work.id,
            version_key=parsed.version_key,
            guid=parsed.guid,
            title=parsed.title,
            summary=parsed.summary,
            content=parsed.content,
            url=parsed.url,
            authors=parsed.authors,
            categories=parsed.categories,
            arxiv_id=parsed.arxiv_id,
            arxiv_version=parsed.arxiv_version,
            doi=parsed.doi,
            announce_type=parsed.announce_type,
            published_at=parsed.published_at,
            source_updated_at=parsed.updated_at,
            source_hash=parsed.source_hash,
        )
        db.add(entry)
        db.flush()
        action = "created"
    elif entry.source_hash != parsed.source_hash and _source_may_update_entry(
        db,
        entry,
        parsed,
        existing_link,
        cross_source_fallback,
    ):
        if cross_source_fallback and parsed.version_key == "v1":
            entry.version_key = "v1"
        entry.guid = parsed.guid
        entry.title = parsed.title
        entry.summary = parsed.summary
        entry.content = parsed.content
        entry.url = parsed.url
        entry.authors = parsed.authors
        entry.categories = parsed.categories
        entry.arxiv_id = parsed.arxiv_id or entry.arxiv_id
        entry.arxiv_version = parsed.arxiv_version or entry.arxiv_version
        entry.doi = parsed.doi
        entry.announce_type = parsed.announce_type
        entry.published_at = parsed.published_at or entry.published_at
        entry.source_updated_at = parsed.updated_at
        entry.source_hash = parsed.source_hash
        action = "updated"
    link = existing_link or db.scalar(
        select(EntryFeed).where(EntryFeed.entry_id == entry.id, EntryFeed.feed_id == feed.id)
    )
    if link is None:
        db.add(EntryFeed(entry_id=entry.id, feed_id=feed.id, source_guid=parsed.guid))
    target_row = db.get(AppSetting, "translation_target")
    target_language = target_row.value if target_row else "zh-CN"
    translation = get_translation_record(db, entry.id, target_language)
    if translation_enabled and (translation is None or translation.source_hash != entry.source_hash):
        if translation is None:
            entry.translations.append(
                Translation(
                    source_hash=entry.source_hash,
                    status="pending",
                    language=target_language,
                    provider=TRANSLATION_RECORD_PROVIDER,
                )
            )
        else:
            translation.source_hash = entry.source_hash
            translation.status = "pending"
            translation.last_error = None
            translation.next_retry_at = None
    # Keep this service operation internally consistent for callers that invoke
    # it repeatedly in one transaction (cross-source and arXiv version merges).
    db.flush()
    return entry, action


def _next_fetch(feed: Feed, failed: bool = False):
    if failed:
        delay = min(feed.poll_interval_minutes * (2 ** max(feed.error_count - 1, 0)), 24 * 60)
    else:
        delay = feed.poll_interval_minutes
    return utcnow() + timedelta(minutes=delay)


def sync_feed(
    db: Session,
    feed: Feed,
    settings: Settings | None = None,
    *,
    client: httpx.Client | None = None,
) -> SyncRun:
    settings = settings or get_settings()
    run = SyncRun(feed_id=feed.id, status="running")
    db.add(run)
    feed.last_checked_at = utcnow()
    db.commit()
    headers = {"User-Agent": "AffogatoRSSReader/0.1 (+self-hosted)"}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified
    owned_client = client is None
    route = http_route_for_feed(db, feed, settings)
    client = client or httpx.Client(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        proxy=route.proxy,
        trust_env=route.trust_env,
    )
    try:
        response = client.get(validate_http_url(feed.url), headers=headers)
        run.http_status = response.status_code
        if response.status_code == 304:
            run.status = "not_modified"
            feed.error_count = 0
            feed.last_error = None
            feed.last_success_at = utcnow()
            feed.next_fetch_at = _next_fetch(feed)
        else:
            response.raise_for_status()
            metadata, entries = parse_feed(response.content, response.headers.get("content-type"))
            run.fetched_count = len(entries)
            translation_enabled = db.get(AppSetting, "translation_enabled")
            enabled = translation_enabled is None or translation_enabled.value.lower() == "true"
            for parsed in entries:
                _entry, action = upsert_entry(db, feed, parsed, enabled)
                if action == "created":
                    run.created_count += 1
                elif action == "updated":
                    run.updated_count += 1
            if metadata.get("title") and (not feed.title or feed.title == feed.url):
                feed.title = metadata["title"]
            if metadata.get("site_url") and not feed.site_url:
                feed.site_url = metadata["site_url"]
            # A fresh 200 response replaces the stored validators. Keeping an
            # old validator after the origin stops sending it can produce
            # incorrect conditional requests on later polls.
            feed.etag = response.headers.get("etag")
            feed.last_modified = response.headers.get("last-modified")
            feed.error_count = 0
            feed.last_error = None
            feed.last_success_at = utcnow()
            feed.next_fetch_at = _next_fetch(feed)
            run.status = "success"
        run.finished_at = utcnow()
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        run = db.get(SyncRun, run.id)
        feed = db.get(Feed, feed.id)
        assert run is not None and feed is not None
        feed.error_count += 1
        feed.last_error = str(exc)[:4000]
        feed.next_fetch_at = _next_fetch(feed, failed=True)
        run.status = "failed"
        run.error = str(exc)[:4000]
        run.finished_at = utcnow()
        db.commit()
        return run
    finally:
        if owned_client:
            client.close()


def sync_due_feeds(db: Session, settings: Settings | None = None, feed_id: int | None = None) -> list[SyncRun]:
    settings = settings or get_settings()
    query = select(Feed).where(Feed.enabled.is_(True))
    if feed_id is not None:
        query = query.where(Feed.id == feed_id)
    else:
        query = query.where((Feed.next_fetch_at.is_(None)) | (Feed.next_fetch_at <= utcnow()))
    feeds = list(db.scalars(query.order_by(Feed.id)))
    return [sync_feed(db, feed, settings) for feed in feeds]
