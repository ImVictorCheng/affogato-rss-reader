"""Add persistent source sorting preferences and manual positions.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "folders",
        sa.Column(
            "sort_mode",
            sa.String(length=20),
            nullable=False,
            server_default="alpha",
        ),
    )
    op.add_column(
        "folders",
        sa.Column(
            "sort_direction",
            sa.String(length=10),
            nullable=False,
            server_default="asc",
        ),
    )
    op.add_column(
        "feeds",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_feeds_position", "feeds", ["position"])
    op.execute(
        sa.text(
            """
            UPDATE feeds
            SET position = (
                SELECT count(*) - 1
                FROM feeds AS ordered
                WHERE coalesce(ordered.folder, '') = coalesce(feeds.folder, '')
                  AND (
                    lower(ordered.title) < lower(feeds.title)
                    OR (
                      lower(ordered.title) = lower(feeds.title)
                      AND ordered.id <= feeds.id
                    )
                  )
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_feeds_position", table_name="feeds")
    op.drop_column("feeds", "position")
    op.drop_column("folders", "sort_direction")
    op.drop_column("folders", "sort_mode")
