"""Add governed automatic tagging data structures.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_tag_name(value: str) -> str | None:
    """Normalize legacy names without guessing whether collisions are aliases."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    collapsed = " ".join(normalized.split())
    # Legacy names may contain compatibility characters whose NFKC expansion
    # is much longer than the original VARCHAR(120) value. Keep the tag and
    # leave its optional normalized key unset instead of relying on SQLite's
    # unenforced VARCHAR length or failing a PostgreSQL upgrade.
    return collapsed if collapsed and len(collapsed) <= 240 else None


def _set_app_setting(
    bind: sa.engine.Connection,
    settings: sa.TableClause,
    key: str,
    value: str,
    now: datetime,
) -> None:
    exists = bind.execute(
        sa.select(settings.c.key).where(settings.c.key == key)
    ).scalar_one_or_none()
    if exists is None:
        bind.execute(
            settings.insert().values(key=key, value=value, updated_at=now)
        )
        return
    bind.execute(
        settings.update()
        .where(settings.c.key == key)
        .values(value=value, updated_at=now)
    )


def upgrade() -> None:
    bind = op.get_bind()
    created_at_default = sa.text("CURRENT_TIMESTAMP")
    if bind.dialect.name == "sqlite":
        # SQLite cannot ADD a column with CURRENT_TIMESTAMP. A migration-time
        # literal fills legacy rows; new ORM tags supply their own timestamp.
        # Never rebuild this referenced table: DROP would cascade through
        # entry_tags and feed_tags while application foreign keys are enabled.
        created_at_default = sa.text(f"'{_utcnow().isoformat(sep=' ')}'")
    with op.batch_alter_table("tags", recreate="never") as batch_op:
        batch_op.add_column(
            sa.Column("normalized_name", sa.String(length=240), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "description",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin",
                sa.String(length=30),
                nullable=False,
                server_default="manual",
            )
        )
        batch_op.add_column(
            sa.Column(
                "auto_assignable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=created_at_default,
            )
        )
        batch_op.create_index(
            "ix_tags_normalized_name", ["normalized_name"], unique=False
        )
        batch_op.create_index("ix_tags_origin", ["origin"], unique=False)
        batch_op.create_index(
            "ix_tags_auto_assignable", ["auto_assignable"], unique=False
        )

    with op.batch_alter_table("auto_tag_records") as batch_op:
        batch_op.add_column(
            sa.Column("policy_version", sa.String(length=120), nullable=True)
        )

    op.create_table(
        "tag_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("normalized_alias", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "normalized_alias", name="uq_tag_alias_normalized"
        ),
    )
    op.create_index("ix_tag_aliases_tag_id", "tag_aliases", ["tag_id"])

    op.create_table(
        "tag_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "support_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "promoted_tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "normalized_name", name="uq_tag_proposal_normalized"
        ),
    )
    op.create_index(
        "ix_tag_proposals_status", "tag_proposals", ["status"]
    )
    op.create_index(
        "ix_tag_proposals_support_count",
        "tag_proposals",
        ["support_count"],
    )
    op.create_index(
        "ix_tag_proposals_promoted_tag_id",
        "tag_proposals",
        ["promoted_tag_id"],
    )

    op.create_table(
        "tag_proposal_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Integer(),
            sa.ForeignKey("tag_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("normalized_alias", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "normalized_alias", name="uq_tag_proposal_alias_normalized"
        ),
    )
    op.create_index(
        "ix_tag_proposal_aliases_proposal_id",
        "tag_proposal_aliases",
        ["proposal_id"],
    )

    op.create_table(
        "tag_proposal_supports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Integer(),
            sa.ForeignKey("tag_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            sa.Integer(),
            sa.ForeignKey("works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_tag_proposal_support_confidence",
        ),
        sa.UniqueConstraint(
            "proposal_id",
            "entry_id",
            name="uq_tag_proposal_support_entry",
        ),
    )
    op.create_index(
        "ix_tag_proposal_supports_proposal_id",
        "tag_proposal_supports",
        ["proposal_id"],
    )
    op.create_index(
        "ix_tag_proposal_supports_entry_id",
        "tag_proposal_supports",
        ["entry_id"],
    )
    op.create_index(
        "ix_tag_proposal_supports_work_id",
        "tag_proposal_supports",
        ["work_id"],
    )
    op.create_index(
        "ix_tag_proposal_supports_source_hash",
        "tag_proposal_supports",
        ["source_hash"],
    )

    op.create_table(
        "entry_tag_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_tag_id",
            sa.Integer(),
            sa.ForeignKey("entry_tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("policy_version", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_entry_tag_source_confidence",
        ),
        sa.UniqueConstraint(
            "entry_tag_id", "source", name="uq_entry_tag_source"
        ),
    )
    op.create_index(
        "ix_entry_tag_sources_entry_tag_id",
        "entry_tag_sources",
        ["entry_tag_id"],
    )
    op.create_index(
        "ix_entry_tag_sources_source", "entry_tag_sources", ["source"]
    )
    op.create_index(
        "ix_entry_tag_sources_policy_version",
        "entry_tag_sources",
        ["policy_version"],
    )

    op.create_table(
        "auto_tag_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "entry_id", "tag_id", name="uq_auto_tag_suppression"
        ),
    )
    op.create_index(
        "ix_auto_tag_suppressions_entry_id",
        "auto_tag_suppressions",
        ["entry_id"],
    )
    op.create_index(
        "ix_auto_tag_suppressions_tag_id",
        "auto_tag_suppressions",
        ["tag_id"],
    )

    op.create_table(
        "auto_tag_previews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("entry_ids", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_auto_tag_previews_status", "auto_tag_previews", ["status"]
    )
    op.create_index(
        "ix_auto_tag_previews_created_at",
        "auto_tag_previews",
        ["created_at"],
    )

    tags = sa.table(
        "tags",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String(length=120)),
        sa.column("normalized_name", sa.String(length=240)),
        sa.column("origin", sa.String(length=30)),
    )
    for tag_id, name in bind.execute(sa.select(tags.c.id, tags.c.name)):
        bind.execute(
            tags.update()
            .where(tags.c.id == tag_id)
            .values(
                normalized_name=_normalize_tag_name(name),
                origin="legacy",
            )
        )

    entry_tags = sa.table(
        "entry_tags",
        sa.column("id", sa.Integer()),
    )
    entry_tag_sources = sa.table(
        "entry_tag_sources",
        sa.column("entry_tag_id", sa.Integer()),
        sa.column("source", sa.String(length=30)),
        sa.column("confidence", sa.Float()),
        sa.column("policy_version", sa.String(length=120)),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    now = _utcnow()
    bind.execute(
        entry_tag_sources.insert().from_select(
            [
                "entry_tag_id",
                "source",
                "confidence",
                "policy_version",
                "created_at",
                "updated_at",
            ],
            sa.select(
                entry_tags.c.id,
                sa.literal("legacy"),
                sa.null(),
                sa.null(),
                sa.literal(now),
                sa.literal(now),
            ),
        )
    )

    settings = sa.table(
        "app_settings",
        sa.column("key", sa.String(length=120)),
        sa.column("value", sa.Text()),
        sa.column("updated_at", sa.DateTime()),
    )
    create_new = bind.execute(
        sa.select(settings.c.value).where(
            settings.c.key == "auto_tag_create_new"
        )
    ).scalar_one_or_none()
    _set_app_setting(
        bind,
        settings,
        "auto_tag_cleanup_snapshot_lock",
        "0",
        now,
    )
    _set_app_setting(
        bind,
        settings,
        "auto_tag_topic_namespace_lock",
        "0",
        now,
    )
    if create_new is not None and create_new.strip().casefold() == "true":
        _set_app_setting(
            bind,
            settings,
            "auto_tag_preview_required",
            "true",
            now,
        )
        _set_app_setting(bind, settings, "auto_tag_enabled", "false", now)


def downgrade() -> None:
    bind = op.get_bind()
    settings = sa.table(
        "app_settings",
        sa.column("key", sa.String(length=120)),
    )
    bind.execute(
        settings.delete().where(
            settings.c.key.in_(
                (
                    "auto_tag_cleanup_snapshot_lock",
                    "auto_tag_cleanup_review_token",
                    "auto_tag_preview_required",
                    "auto_tag_topic_namespace_lock",
                )
            )
        )
    )

    op.drop_index(
        "ix_auto_tag_previews_created_at", table_name="auto_tag_previews"
    )
    op.drop_index(
        "ix_auto_tag_previews_status", table_name="auto_tag_previews"
    )
    op.drop_table("auto_tag_previews")

    op.drop_index(
        "ix_auto_tag_suppressions_tag_id",
        table_name="auto_tag_suppressions",
    )
    op.drop_index(
        "ix_auto_tag_suppressions_entry_id",
        table_name="auto_tag_suppressions",
    )
    op.drop_table("auto_tag_suppressions")

    op.drop_index(
        "ix_entry_tag_sources_policy_version",
        table_name="entry_tag_sources",
    )
    op.drop_index(
        "ix_entry_tag_sources_source", table_name="entry_tag_sources"
    )
    op.drop_index(
        "ix_entry_tag_sources_entry_tag_id",
        table_name="entry_tag_sources",
    )
    op.drop_table("entry_tag_sources")

    op.drop_index(
        "ix_tag_proposal_supports_source_hash",
        table_name="tag_proposal_supports",
    )
    op.drop_index(
        "ix_tag_proposal_supports_work_id",
        table_name="tag_proposal_supports",
    )
    op.drop_index(
        "ix_tag_proposal_supports_entry_id",
        table_name="tag_proposal_supports",
    )
    op.drop_index(
        "ix_tag_proposal_supports_proposal_id",
        table_name="tag_proposal_supports",
    )
    op.drop_table("tag_proposal_supports")

    op.drop_index(
        "ix_tag_proposal_aliases_proposal_id",
        table_name="tag_proposal_aliases",
    )
    op.drop_table("tag_proposal_aliases")

    op.drop_index(
        "ix_tag_proposals_promoted_tag_id", table_name="tag_proposals"
    )
    op.drop_index(
        "ix_tag_proposals_support_count", table_name="tag_proposals"
    )
    op.drop_index("ix_tag_proposals_status", table_name="tag_proposals")
    op.drop_table("tag_proposals")

    op.drop_index("ix_tag_aliases_tag_id", table_name="tag_aliases")
    op.drop_table("tag_aliases")

    with op.batch_alter_table("auto_tag_records") as batch_op:
        batch_op.drop_column("policy_version")

    with op.batch_alter_table("tags") as batch_op:
        batch_op.drop_index("ix_tags_auto_assignable")
        batch_op.drop_index("ix_tags_origin")
        batch_op.drop_index("ix_tags_normalized_name")
        batch_op.drop_column("created_at")
        batch_op.drop_column("auto_assignable")
        batch_op.drop_column("origin")
        batch_op.drop_column("description")
        batch_op.drop_column("normalized_name")
