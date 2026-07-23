"""Add the platform capability registry (ADR-0 / ADR-6).

The control plane for what may extend NUMU, and — the part ADR-6 needed — who
may hold each capability. Platform-global, so no tenant_id and no RLS: the
registry describes what MAY exist, while per-store activation stays with the
existing install/enable/disable center.

Additive and empty on creation: nothing reads it until capabilities are
registered, so this is safe to apply ahead of the code that populates it.

Revision ID: platform_capabilities_20260720
Revises: theme_certification_20260719
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "platform_capabilities_20260720"
down_revision = "theme_certification_20260719"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_capabilities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "lifecycle_state",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "data_classification",
            sa.String(length=32),
            server_default="tenant_scoped",
            nullable=False,
        ),
        sa.Column(
            "min_tier", sa.String(length=20), server_default="partner", nullable=False
        ),
        sa.Column(
            "unavailable_behavior",
            sa.String(length=20),
            server_default="fail_open",
            nullable=False,
        ),
        sa.Column("active_version", sa.String(length=32), nullable=True),
        sa.Column(
            "supported_versions",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "placements", postgresql.JSONB(), server_default="[]", nullable=False
        ),
        sa.Column(
            "dependencies", postgresql.JSONB(), server_default="[]", nullable=False
        ),
        sa.Column(
            "eligibility", postgresql.JSONB(), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_platform_capabilities_slug"),
        schema="public",
    )
    op.create_index(
        "ix_platform_capabilities_slug",
        "platform_capabilities",
        ["slug"],
        schema="public",
    )
    op.create_index(
        "ix_platform_capabilities_kind",
        "platform_capabilities",
        ["kind"],
        schema="public",
    )
    op.create_index(
        "ix_platform_capabilities_lifecycle_state",
        "platform_capabilities",
        ["lifecycle_state"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_capabilities_lifecycle_state",
        table_name="platform_capabilities",
        schema="public",
    )
    op.drop_index(
        "ix_platform_capabilities_kind",
        table_name="platform_capabilities",
        schema="public",
    )
    op.drop_index(
        "ix_platform_capabilities_slug",
        table_name="platform_capabilities",
        schema="public",
    )
    op.drop_table("platform_capabilities", schema="public")
