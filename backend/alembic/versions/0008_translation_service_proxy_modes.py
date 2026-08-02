"""Add per-service proxy modes for translation providers.

Revision ID: 0008_translation_proxy
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_translation_proxy"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "network_proxy_config",
        sa.Column(
            "translation_service_modes",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("network_proxy_config", "translation_service_modes")
