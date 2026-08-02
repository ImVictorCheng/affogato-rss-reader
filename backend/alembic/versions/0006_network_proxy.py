"""Add reusable network proxy configuration and target bindings.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "network_proxy_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("password_hint", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "feeds",
        sa.Column(
            "use_proxy", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_feeds_use_proxy", "feeds", ["use_proxy"])
    op.add_column(
        "llm_connections",
        sa.Column(
            "use_proxy", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_llm_connections_use_proxy", "llm_connections", ["use_proxy"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_connections_use_proxy", table_name="llm_connections")
    op.drop_column("llm_connections", "use_proxy")
    op.drop_index("ix_feeds_use_proxy", table_name="feeds")
    op.drop_column("feeds", "use_proxy")
    op.drop_table("network_proxy_config")
