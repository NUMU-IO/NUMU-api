"""Add metafield_definitions + metafield_values (typed custom data foundation).

Additive: two new ``public`` tables only, no changes to existing tables, so
it's safe for production stores — they simply have no metafield rows until a
merchant defines and sets them. Replaces the untyped ``product.attributes``
JSONB pass-through with a typed, namespaced schema (definition + value).

``owner_type`` and ``type`` are plain ``VARCHAR`` columns (validation lives on
the ``MetafieldOwnerType`` / ``MetafieldType`` StrEnums at the app layer) — no
new Postgres ENUM types, so no ALTER TYPE friction later.

NOTE (multi-head): this chains off the current dev head ``tiktok_shop_20260701``.
Two other in-flight branches (``add_template_suffix_20260704`` and
``theme_error_events_20260704``) are ALSO siblings off ``tiktok_shop_20260701``,
so once those merge there will be three heads — reconcile with
``alembic merge heads`` (do NOT rebase the siblings into a line). Not
auto-applied to prod; run explicitly.

Revision ID: metafields_foundation_20260704
Revises: tiktok_shop_20260701
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "metafields_foundation_20260704"
down_revision: str | None = "tiktok_shop_20260701"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metafield_definitions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "store_id",
            "owner_type",
            "namespace",
            "key",
            name="uq_metafield_def_store_owner_ns_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_metafield_definitions_tenant_id",
        "metafield_definitions",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_metafield_definitions_store_owner",
        "metafield_definitions",
        ["store_id", "owner_type"],
        schema="public",
    )

    op.create_table(
        "metafield_values",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.metafield_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "definition_id",
            "owner_id",
            name="uq_metafield_values_definition_owner",
        ),
        schema="public",
    )
    op.create_index(
        "ix_metafield_values_tenant_id",
        "metafield_values",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_metafield_values_definition_id",
        "metafield_values",
        ["definition_id"],
        schema="public",
    )
    op.create_index(
        "ix_metafield_values_store_owner",
        "metafield_values",
        ["store_id", "owner_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metafield_values_store_owner",
        table_name="metafield_values",
        schema="public",
    )
    op.drop_index(
        "ix_metafield_values_definition_id",
        table_name="metafield_values",
        schema="public",
    )
    op.drop_index(
        "ix_metafield_values_tenant_id",
        table_name="metafield_values",
        schema="public",
    )
    op.drop_table("metafield_values", schema="public")

    op.drop_index(
        "ix_metafield_definitions_store_owner",
        table_name="metafield_definitions",
        schema="public",
    )
    op.drop_index(
        "ix_metafield_definitions_tenant_id",
        table_name="metafield_definitions",
        schema="public",
    )
    op.drop_table("metafield_definitions", schema="public")
