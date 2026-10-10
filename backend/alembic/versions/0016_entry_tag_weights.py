"""Persist article-specific tag ordering weights.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not rebuild entry_tags: its provenance rows have cascading foreign keys.
    op.add_column("entry_tags", sa.Column("weight", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("entry_tags", "weight")
