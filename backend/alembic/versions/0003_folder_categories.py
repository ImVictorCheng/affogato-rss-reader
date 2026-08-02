"""Add persistent folder categories.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_folders_position", "folders", ["position"])
    op.execute(
        sa.text(
            """
            INSERT INTO folders (name, position, created_at, updated_at)
            SELECT DISTINCT trim(folder), 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM feeds
            WHERE folder IS NOT NULL AND trim(folder) <> ''
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_folders_position", table_name="folders")
    op.drop_table("folders")
