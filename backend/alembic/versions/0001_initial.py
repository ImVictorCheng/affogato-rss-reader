"""Initial Affogato RSS Reader schema.

Revision ID: 0001
Revises:

This revision is intentionally self-contained.  Initial migrations must remain
stable as application models evolve, so it does not import the runtime metadata.
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "feeds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("site_url", sa.Text(), nullable=True),
        sa.Column("folder", sa.String(length=160), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("next_fetch_at", sa.DateTime(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
        sa.UniqueConstraint("url"),
    )
    op.create_table(
        "works",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(length=1000), nullable=False),
        sa.Column("arxiv_base_id", sa.String(length=80), nullable=True),
        sa.Column("doi", sa.String(length=500), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "brief_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("cutoff_time", sa.String(length=5), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("month_day", sa.Integer(), nullable=True),
        sa.Column("year_month", sa.Integer(), nullable=True),
        sa.Column("domain_ids", sa.JSON(), nullable=False),
        sa.Column("feed_ids", sa.JSON(), nullable=False),
        sa.Column("tag_ids", sa.JSON(), nullable=False),
        sa.Column("domain_match", sa.String(length=8), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["brief_schedules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_brief_idempotency_key"),
        sa.UniqueConstraint("schedule_id", "start_at", "end_at", name="uq_brief_schedule_window"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("version_key", sa.String(length=120), nullable=False),
        sa.Column("guid", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("arxiv_id", sa.String(length=100), nullable=True),
        sa.Column("arxiv_version", sa.Integer(), nullable=True),
        sa.Column("doi", sa.String(length=500), nullable=True),
        sa.Column("announce_type", sa.String(length=40), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "version_key", name="uq_entry_work_version"),
    )
    op.create_table(
        "entry_feeds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("source_guid", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "feed_id", name="uq_entry_feed"),
    )
    op.create_table(
        "reading_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("starred", sa.Boolean(), nullable=False),
        sa.Column("later", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "entry_id", name="uq_owner_entry_state"),
    )
    op.create_table(
        "translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "language", "provider", name="uq_entry_translation_target"),
    )
    op.create_table(
        "translation_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("text_hash", "language", "provider", name="uq_translation_cache"),
    )
    op.create_table(
        "entry_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "tag_id", name="uq_entry_tag"),
    )
    op.create_table(
        "feed_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "tag_id", name="uq_feed_tag"),
    )
    op.create_table(
        "feed_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "domain_id", name="uq_feed_domain"),
    )
    op.create_table(
        "entry_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "domain_id", name="uq_entry_domain"),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "brief_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brief_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["brief_id"], ["briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brief_id", "entry_id", name="uq_brief_entry"),
    )

    op.create_index("ix_feeds_folder", "feeds", ["folder"], unique=False)
    op.create_index("ix_feeds_enabled", "feeds", ["enabled"], unique=False)
    op.create_index("ix_feeds_next_fetch_at", "feeds", ["next_fetch_at"], unique=False)
    op.create_index("ix_works_dedup_key", "works", ["dedup_key"], unique=True)
    op.create_index("ix_works_arxiv_base_id", "works", ["arxiv_base_id"], unique=False)
    op.create_index("ix_works_doi", "works", ["doi"], unique=False)
    op.create_index("ix_jobs_kind", "jobs", ["kind"], unique=False)
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)
    op.create_index("ix_domains_position", "domains", ["position"], unique=False)
    op.create_index("ix_brief_schedules_period", "brief_schedules", ["period"], unique=False)
    op.create_index("ix_brief_schedules_enabled", "brief_schedules", ["enabled"], unique=False)
    op.create_index("ix_briefs_schedule_id", "briefs", ["schedule_id"], unique=False)
    op.create_index("ix_briefs_period", "briefs", ["period"], unique=False)
    op.create_index("ix_briefs_start_at", "briefs", ["start_at"], unique=False)
    op.create_index("ix_briefs_end_at", "briefs", ["end_at"], unique=False)
    op.create_index("ix_sessions_owner_id", "sessions", ["owner_id"], unique=False)
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)
    op.create_index("ix_entries_work_id", "entries", ["work_id"], unique=False)
    op.create_index("ix_entries_arxiv_id", "entries", ["arxiv_id"], unique=False)
    op.create_index("ix_entries_doi", "entries", ["doi"], unique=False)
    op.create_index("ix_entries_announce_type", "entries", ["announce_type"], unique=False)
    op.create_index("ix_entries_published_at", "entries", ["published_at"], unique=False)
    op.create_index("ix_entries_published", "entries", ["published_at"], unique=False)
    op.create_index("ix_entries_source_hash", "entries", ["source_hash"], unique=False)
    op.create_index("ix_entry_feeds_entry_id", "entry_feeds", ["entry_id"], unique=False)
    op.create_index("ix_entry_feeds_feed_id", "entry_feeds", ["feed_id"], unique=False)
    op.create_index("ix_reading_states_owner_id", "reading_states", ["owner_id"], unique=False)
    op.create_index("ix_reading_states_entry_id", "reading_states", ["entry_id"], unique=False)
    op.create_index("ix_reading_states_read", "reading_states", ["read"], unique=False)
    op.create_index("ix_reading_states_starred", "reading_states", ["starred"], unique=False)
    op.create_index("ix_reading_states_later", "reading_states", ["later"], unique=False)
    op.create_index("ix_reading_states_archived", "reading_states", ["archived"], unique=False)
    op.create_index("ix_translations_entry_id", "translations", ["entry_id"], unique=False)
    op.create_index("ix_translations_source_hash", "translations", ["source_hash"], unique=False)
    op.create_index("ix_translations_status", "translations", ["status"], unique=False)
    op.create_index("ix_translations_next_retry_at", "translations", ["next_retry_at"], unique=False)
    op.create_index("ix_translation_cache_text_hash", "translation_cache", ["text_hash"], unique=False)
    op.create_index("ix_entry_tags_entry_id", "entry_tags", ["entry_id"], unique=False)
    op.create_index("ix_entry_tags_tag_id", "entry_tags", ["tag_id"], unique=False)
    op.create_index("ix_feed_tags_feed_id", "feed_tags", ["feed_id"], unique=False)
    op.create_index("ix_feed_tags_tag_id", "feed_tags", ["tag_id"], unique=False)
    op.create_index("ix_feed_domains_feed_id", "feed_domains", ["feed_id"], unique=False)
    op.create_index("ix_feed_domains_domain_id", "feed_domains", ["domain_id"], unique=False)
    op.create_index("ix_entry_domains_entry_id", "entry_domains", ["entry_id"], unique=False)
    op.create_index("ix_entry_domains_domain_id", "entry_domains", ["domain_id"], unique=False)
    op.create_index("ix_sync_runs_feed_id", "sync_runs", ["feed_id"], unique=False)
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"], unique=False)
    op.create_index("ix_brief_items_brief_id", "brief_items", ["brief_id"], unique=False)
    op.create_index("ix_brief_items_entry_id", "brief_items", ["entry_id"], unique=False)

    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            """CREATE VIRTUAL TABLE entries_fts USING fts5(
                title, summary, authors, categories, arxiv_id, doi,
                content='entries', content_rowid='id'
            )"""
        )
        op.execute(
            """CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(rowid,title,summary,authors,categories,arxiv_id,doi)
                VALUES(new.id,new.title,new.summary,new.authors,new.categories,new.arxiv_id,new.doi);
            END"""
        )
        op.execute(
            """CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts,rowid,title,summary,authors,categories,arxiv_id,doi)
                VALUES('delete',old.id,old.title,old.summary,old.authors,old.categories,old.arxiv_id,old.doi);
            END"""
        )
        op.execute(
            """CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts,rowid,title,summary,authors,categories,arxiv_id,doi)
                VALUES('delete',old.id,old.title,old.summary,old.authors,old.categories,old.arxiv_id,old.doi);
                INSERT INTO entries_fts(rowid,title,summary,authors,categories,arxiv_id,doi)
                VALUES(new.id,new.title,new.summary,new.authors,new.categories,new.arxiv_id,new.doi);
            END"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS entries_au")
        op.execute("DROP TRIGGER IF EXISTS entries_ad")
        op.execute("DROP TRIGGER IF EXISTS entries_ai")
        op.execute("DROP TABLE IF EXISTS entries_fts")

    op.drop_index("ix_brief_items_entry_id", table_name="brief_items")
    op.drop_index("ix_brief_items_brief_id", table_name="brief_items")
    op.drop_index("ix_sync_runs_status", table_name="sync_runs")
    op.drop_index("ix_sync_runs_feed_id", table_name="sync_runs")
    op.drop_index("ix_feed_tags_tag_id", table_name="feed_tags")
    op.drop_index("ix_feed_tags_feed_id", table_name="feed_tags")
    op.drop_index("ix_entry_domains_domain_id", table_name="entry_domains")
    op.drop_index("ix_entry_domains_entry_id", table_name="entry_domains")
    op.drop_index("ix_feed_domains_domain_id", table_name="feed_domains")
    op.drop_index("ix_feed_domains_feed_id", table_name="feed_domains")
    op.drop_index("ix_entry_tags_tag_id", table_name="entry_tags")
    op.drop_index("ix_entry_tags_entry_id", table_name="entry_tags")
    op.drop_index("ix_translation_cache_text_hash", table_name="translation_cache")
    op.drop_index("ix_translations_next_retry_at", table_name="translations")
    op.drop_index("ix_translations_status", table_name="translations")
    op.drop_index("ix_translations_source_hash", table_name="translations")
    op.drop_index("ix_translations_entry_id", table_name="translations")
    op.drop_index("ix_reading_states_archived", table_name="reading_states")
    op.drop_index("ix_reading_states_later", table_name="reading_states")
    op.drop_index("ix_reading_states_starred", table_name="reading_states")
    op.drop_index("ix_reading_states_read", table_name="reading_states")
    op.drop_index("ix_reading_states_entry_id", table_name="reading_states")
    op.drop_index("ix_reading_states_owner_id", table_name="reading_states")
    op.drop_index("ix_entry_feeds_feed_id", table_name="entry_feeds")
    op.drop_index("ix_entry_feeds_entry_id", table_name="entry_feeds")
    op.drop_index("ix_entries_source_hash", table_name="entries")
    op.drop_index("ix_entries_published", table_name="entries")
    op.drop_index("ix_entries_published_at", table_name="entries")
    op.drop_index("ix_entries_announce_type", table_name="entries")
    op.drop_index("ix_entries_doi", table_name="entries")
    op.drop_index("ix_entries_arxiv_id", table_name="entries")
    op.drop_index("ix_entries_work_id", table_name="entries")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_index("ix_sessions_owner_id", table_name="sessions")
    op.drop_index("ix_briefs_end_at", table_name="briefs")
    op.drop_index("ix_briefs_start_at", table_name="briefs")
    op.drop_index("ix_briefs_period", table_name="briefs")
    op.drop_index("ix_briefs_schedule_id", table_name="briefs")
    op.drop_index("ix_brief_schedules_enabled", table_name="brief_schedules")
    op.drop_index("ix_brief_schedules_period", table_name="brief_schedules")
    op.drop_index("ix_domains_position", table_name="domains")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_kind", table_name="jobs")
    op.drop_index("ix_works_doi", table_name="works")
    op.drop_index("ix_works_arxiv_base_id", table_name="works")
    op.drop_index("ix_works_dedup_key", table_name="works")
    op.drop_index("ix_feeds_next_fetch_at", table_name="feeds")
    op.drop_index("ix_feeds_enabled", table_name="feeds")
    op.drop_index("ix_feeds_folder", table_name="feeds")

    op.drop_table("brief_items")
    op.drop_table("sync_runs")
    op.drop_table("feed_tags")
    op.drop_table("entry_domains")
    op.drop_table("feed_domains")
    op.drop_table("entry_tags")
    op.drop_table("translation_cache")
    op.drop_table("translations")
    op.drop_table("reading_states")
    op.drop_table("entry_feeds")
    op.drop_table("entries")
    op.drop_table("sessions")
    op.drop_table("briefs")
    op.drop_table("brief_schedules")
    op.drop_table("domains")
    op.drop_table("jobs")
    op.drop_table("tags")
    op.drop_table("works")
    op.drop_table("feeds")
    op.drop_table("app_settings")
    op.drop_table("owners")
