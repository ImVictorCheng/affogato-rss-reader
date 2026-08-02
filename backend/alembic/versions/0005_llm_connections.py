"""Add reusable encrypted LLM connections.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_hint", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "llm_feature_bindings",
        sa.Column("feature_key", sa.String(length=120), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["llm_connections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("feature_key"),
    )
    op.create_index(
        "ix_llm_feature_bindings_connection_id",
        "llm_feature_bindings",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_feature_bindings_connection_id",
        table_name="llm_feature_bindings",
    )
    op.drop_table("llm_feature_bindings")
    op.drop_table("llm_connections")
