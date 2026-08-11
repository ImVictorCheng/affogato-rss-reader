"""Add an optional start time to brief schedules.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("brief_schedules") as batch_op:
        batch_op.add_column(
            sa.Column("start_time", sa.String(length=5), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("brief_schedules") as batch_op:
        batch_op.drop_column("start_time")
