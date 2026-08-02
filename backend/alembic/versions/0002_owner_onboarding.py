"""Add owner onboarding and visual personalization.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "owners",
        sa.Column(
            "onboarding_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "owners",
        sa.Column("selected_domains", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "owners", sa.Column("primary_domain", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "owners",
        sa.Column("theme", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "owners",
        sa.Column(
            "ai_personalized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "owners", sa.Column("ai_provider", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("owners", "ai_provider")
    op.drop_column("owners", "ai_personalized")
    op.drop_column("owners", "theme")
    op.drop_column("owners", "primary_domain")
    op.drop_column("owners", "selected_domains")
    op.drop_column("owners", "onboarding_completed")
