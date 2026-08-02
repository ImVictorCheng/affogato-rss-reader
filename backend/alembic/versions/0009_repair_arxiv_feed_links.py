"""Repair arXiv feed links lost by earlier SQLite table rebuilds.

Revision ID: 0009
Revises: 0008_translation_proxy
"""

from __future__ import annotations

from alembic import op


revision = "0009"
down_revision = "0008_translation_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT OR IGNORE INTO entry_feeds (
            entry_id,
            feed_id,
            source_guid,
            first_seen_at
        )
        SELECT
            entries.id,
            feeds.id,
            entries.guid,
            CURRENT_TIMESTAMP
        FROM feeds
        JOIN entries
        JOIN json_each(entries.categories) AS category
        WHERE (
                lower(feeds.url) LIKE 'https://export.arxiv.org/rss/%'
                OR lower(feeds.url) LIKE 'http://export.arxiv.org/rss/%'
                OR lower(feeds.url) LIKE 'https://arxiv.org/rss/%'
                OR lower(feeds.url) LIKE 'http://arxiv.org/rss/%'
            )
          AND category.value = rtrim(
                substr(feeds.url, instr(lower(feeds.url), '/rss/') + 5),
                '/'
            )
        """
    )
    op.execute(
        """
        UPDATE feeds
        SET etag = NULL,
            last_modified = NULL,
            next_fetch_at = CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1
            FROM entry_feeds
            WHERE entry_feeds.feed_id = feeds.id
        )
        """
    )


def downgrade() -> None:
    # Repaired source relationships are valid application data and must not be
    # removed when rolling back the schema revision.
    pass
