"""Add durable checkpoints for resumable brief generation.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brief_generation_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "stage",
            "prompt_hash",
            name="uq_brief_generation_checkpoint",
        ),
    )
    op.create_index(
        "ix_brief_generation_checkpoints_job_id",
        "brief_generation_checkpoints",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_brief_generation_checkpoints_job_id",
        table_name="brief_generation_checkpoints",
    )
    op.drop_table("brief_generation_checkpoints")
