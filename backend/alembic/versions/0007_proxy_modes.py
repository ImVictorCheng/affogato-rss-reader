"""Replace boolean proxy bindings with explicit routing modes.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feeds",
        sa.Column(
            "proxy_mode",
            sa.String(length=20),
            nullable=False,
            server_default="direct",
        ),
    )
    op.execute("UPDATE feeds SET proxy_mode = 'custom' WHERE use_proxy = 1")
    op.drop_index("ix_feeds_use_proxy", table_name="feeds")
    op.drop_column("feeds", "use_proxy")
    op.create_index("ix_feeds_proxy_mode", "feeds", ["proxy_mode"])

    op.add_column(
        "llm_connections",
        sa.Column(
            "proxy_mode",
            sa.String(length=20),
            nullable=False,
            server_default="direct",
        ),
    )
    op.execute(
        "UPDATE llm_connections SET proxy_mode = 'custom' WHERE use_proxy = 1"
    )
    op.drop_index("ix_llm_connections_use_proxy", table_name="llm_connections")
    op.drop_column("llm_connections", "use_proxy")
    op.create_index(
        "ix_llm_connections_proxy_mode", "llm_connections", ["proxy_mode"]
    )


def downgrade() -> None:
    op.add_column(
        "llm_connections",
        sa.Column(
            "use_proxy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE llm_connections SET use_proxy = 1 WHERE proxy_mode = 'custom'"
    )
    op.drop_index("ix_llm_connections_proxy_mode", table_name="llm_connections")
    op.drop_column("llm_connections", "proxy_mode")
    op.create_index(
        "ix_llm_connections_use_proxy", "llm_connections", ["use_proxy"]
    )

    op.add_column(
        "feeds",
        sa.Column(
            "use_proxy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("UPDATE feeds SET use_proxy = 1 WHERE proxy_mode = 'custom'")
    op.drop_index("ix_feeds_proxy_mode", table_name="feeds")
    op.drop_column("feeds", "proxy_mode")
    op.create_index("ix_feeds_use_proxy", "feeds", ["use_proxy"])
