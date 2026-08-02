from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Return a naive UTC timestamp for SQLite without using deprecated utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(80), default="owner")
    password_hash: Mapped[str | None] = mapped_column(String(512))
    activation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    primary_domain: Mapped[str | None] = mapped_column(String(120))
    theme: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_personalized: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_provider: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LLMConnection(Base):
    __tablename__ = "llm_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(200))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(String(32))
    proxy_mode: Mapped[str] = mapped_column(String(20), default="direct", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    bindings: Mapped[list["LLMFeatureBinding"]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
    )


class LLMFeatureBinding(Base):
    __tablename__ = "llm_feature_bindings"
    feature_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("llm_connections.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    connection: Mapped[LLMConnection] = relationship(back_populates="bindings")


class NetworkProxyConfig(Base):
    __tablename__ = "network_proxy_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(1000), default="")
    username: Mapped[str | None] = mapped_column(String(255))
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    password_hint: Mapped[str | None] = mapped_column(String(32))
    global_mode: Mapped[str] = mapped_column(String(20), default="direct")
    translation_service_modes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    sort_mode: Mapped[str] = mapped_column(String(20), default="alpha")
    sort_direction: Mapped[str] = mapped_column(String(10), default="asc")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Feed(Base):
    __tablename__ = "feeds"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text, unique=True)
    site_url: Mapped[str | None] = mapped_column(Text)
    folder: Mapped[str | None] = mapped_column(String(160), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    proxy_mode: Mapped[str] = mapped_column(String(20), default="direct", index=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=45)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Domain(Base):
    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FeedDomain(Base):
    __tablename__ = "feed_domains"
    __table_args__ = (UniqueConstraint("feed_id", "domain_id", name="uq_feed_domain"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)


class Work(Base):
    __tablename__ = "works"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    arxiv_base_id: Mapped[str | None] = mapped_column(String(80), index=True)
    doi: Mapped[str | None] = mapped_column(String(500), index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    entries: Mapped[list["Entry"]] = relationship(back_populates="work")


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("work_id", "version_key", name="uq_entry_work_version"),
        Index("ix_entries_published", "published_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), index=True)
    version_key: Mapped[str] = mapped_column(String(120), default="default")
    guid: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), index=True)
    arxiv_version: Mapped[int | None] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(String(500), index=True)
    announce_type: Mapped[str | None] = mapped_column(String(40), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    work: Mapped[Work] = relationship(back_populates="entries")
    feed_links: Mapped[list["EntryFeed"]] = relationship(back_populates="entry", cascade="all, delete-orphan")
    translations: Mapped[list["Translation"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )

    @property
    def translation(self) -> "Translation | None":
        """Return the first translation for compatibility with single-target callers."""
        return self.translations[0] if self.translations else None


class EntryFeed(Base):
    __tablename__ = "entry_feeds"
    __table_args__ = (UniqueConstraint("entry_id", "feed_id", name="uq_entry_feed"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    source_guid: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    entry: Mapped[Entry] = relationship(back_populates="feed_links")
    feed: Mapped[Feed] = relationship()


class EntryDomain(Base):
    __tablename__ = "entry_domains"
    __table_args__ = (UniqueConstraint("entry_id", "domain_id", name="uq_entry_domain"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)


class ReadingState(Base):
    __tablename__ = "reading_states"
    __table_args__ = (UniqueConstraint("owner_id", "entry_id", name="uq_owner_entry_state"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    later: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Translation(Base):
    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("entry_id", "language", "provider", name="uq_entry_translation_target"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    provider: Mapped[str] = mapped_column(String(50), default="translation-chain")
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    entry: Mapped[Entry] = relationship(back_populates="translations")


class TranslationCache(Base):
    __tablename__ = "translation_cache"
    __table_args__ = (UniqueConstraint("text_hash", "language", "provider", name="uq_translation_cache"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(50))
    translated_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    color: Mapped[str | None] = mapped_column(String(20))


class EntryTag(Base):
    __tablename__ = "entry_tags"
    __table_args__ = (UniqueConstraint("entry_id", "tag_id", name="uq_entry_tag"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


class FeedTag(Base):
    __tablename__ = "feed_tags"
    __table_args__ = (UniqueConstraint("feed_id", "tag_id", name="uq_feed_tag"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class BriefGenerationCheckpoint(Base):
    __tablename__ = "brief_generation_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stage",
            "prompt_hash",
            name="uq_brief_generation_checkpoint",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(30))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class BriefSchedule(Base):
    __tablename__ = "brief_schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    period: Mapped[str] = mapped_column(String(20), index=True)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    cutoff_time: Mapped[str] = mapped_column(String(5), default="09:00")
    weekday: Mapped[int | None] = mapped_column(Integer)
    month_day: Mapped[int | None] = mapped_column(Integer)
    year_month: Mapped[int | None] = mapped_column(Integer)
    domain_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    feed_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    tag_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    domain_match: Mapped[str] = mapped_column(String(8), default="any")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "start_at", "end_at", name="uq_brief_schedule_window"),
        UniqueConstraint("idempotency_key", name="uq_brief_idempotency_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("brief_schedules.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    period: Mapped[str] = mapped_column(String(20), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    title: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BriefItem(Base):
    __tablename__ = "brief_items"
    __table_args__ = (UniqueConstraint("brief_id", "entry_id", name="uq_brief_entry"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
