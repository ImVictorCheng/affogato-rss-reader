"""Add the application-wide default proxy route.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("network_proxy_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "global_mode",
                sa.String(length=20),
                nullable=False,
                server_default="direct",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("network_proxy_config") as batch_op:
        batch_op.drop_column("global_mode")
