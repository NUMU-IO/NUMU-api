"""Create recovery flow aggregate tables (backend-021).

Adds four tables in support of the COD-to-prepaid recovery engine:

- ``recovery_flows`` — aggregate root.
- ``recovery_steps`` — child of recovery_flows (CASCADE delete).
- ``recovery_monthly_rollups`` — per-store, per-month write-through aggregate.
- ``recovery_rollup_ledger`` — append-only dedup gate for rollup mutations.

Plus two new enum types:
- ``recovery_flow_state_enum``
- ``recovery_rollup_ledger_event_type_enum``

See ``specs/backend-021-recovery-flow-aggregate/spec.md`` for the contract
this migration implements; spec 009 CL-006 for the rollup-ledger rationale.

Revision ID: recovery_flow_20260511
Revises: phase_6_platform_20260509
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "recovery_flow_20260511"
down_revision: str | None = "phase_6_platform_20260509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Enum type names + values (kept here so downgrade can reference them).
RECOVERY_FLOW_STATE_VALUES = (
    "pending_step_1",
    "pending_step_2",
    "pending_step_3",
    "succeeded",
    "succeeded_deposit",
    "abandoned",
    "abandoned_partial",
    "abandoned_by_merchant",
    "terminated_uninstall",
    "blocked_no_gateway",
    "blocked_no_template",
)

ROLLUP_LEDGER_EVENT_TYPE_VALUES = (
    "succeeded",
    "succeeded_deposit",
    "balance_captured",
    "refunded",
    "refund_reversed",
)


def upgrade() -> None:
    # ---- Enum types ----
    op.execute(
        "CREATE TYPE recovery_flow_state_enum AS ENUM ("
        + ", ".join(f"'{v}'" for v in RECOVERY_FLOW_STATE_VALUES)
        + ")"
    )
    op.execute(
        "CREATE TYPE recovery_rollup_ledger_event_type_enum AS ENUM ("
        + ", ".join(f"'{v}'" for v in ROLLUP_LEDGER_EVENT_TYPE_VALUES)
        + ")"
    )

    # ---- recovery_flows ----
    op.create_table(
        "recovery_flows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("shopify_order_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "state",
            postgresql.ENUM(
                *RECOVERY_FLOW_STATE_VALUES,
                name="recovery_flow_state_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "cadence",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "current_step_index",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "payment_link_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("recovered_amount_cents", sa.Integer, nullable=True),
        sa.Column("recovered_via_rail", sa.String(32), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
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
            "shopify_order_id",
            name="uq_recovery_flow_per_order",
        ),
        schema="public",
    )
    op.create_index(
        "ix_recovery_flow_store_state_created",
        "recovery_flows",
        ["store_id", "state", "created_at"],
        schema="public",
    )

    # ---- recovery_steps ----
    op.create_table(
        "recovery_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "flow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.recovery_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("template_key", sa.String(128), nullable=False),
        sa.Column(
            "channel",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'whatsapp'"),
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_reason", sa.Text, nullable=True),
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
            "flow_id",
            "step_index",
            name="uq_recovery_step_per_flow_index",
        ),
        schema="public",
    )
    op.create_index(
        "ix_recovery_step_flow_id",
        "recovery_steps",
        ["flow_id"],
        schema="public",
    )

    # ---- recovery_monthly_rollups ----
    op.create_table(
        "recovery_monthly_rollups",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "month_key",
            sa.Date,
            primary_key=True,
            comment="First day of store-local calendar month per constitution v1.2.0 FR-011",
        ),
        sa.Column(
            "recovered_cents",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "recovered_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
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
        schema="public",
    )
    op.create_index(
        "ix_recovery_rollup_updated_at",
        "recovery_monthly_rollups",
        ["updated_at"],
        schema="public",
    )

    # ---- recovery_rollup_ledger (idempotency gate) ----
    op.create_table(
        "recovery_rollup_ledger",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "shopify_order_id",
            sa.String(64),
            primary_key=True,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                *ROLLUP_LEDGER_EVENT_TYPE_VALUES,
                name="recovery_rollup_ledger_event_type_enum",
                create_type=False,
            ),
            primary_key=True,
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("applied_amount_cents", sa.Integer, nullable=False),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("recovery_rollup_ledger", schema="public")
    op.drop_index(
        "ix_recovery_rollup_updated_at",
        table_name="recovery_monthly_rollups",
        schema="public",
    )
    op.drop_table("recovery_monthly_rollups", schema="public")
    op.drop_index(
        "ix_recovery_step_flow_id",
        table_name="recovery_steps",
        schema="public",
    )
    op.drop_table("recovery_steps", schema="public")
    op.drop_index(
        "ix_recovery_flow_store_state_created",
        table_name="recovery_flows",
        schema="public",
    )
    op.drop_table("recovery_flows", schema="public")
    op.execute("DROP TYPE IF EXISTS recovery_rollup_ledger_event_type_enum")
    op.execute("DROP TYPE IF EXISTS recovery_flow_state_enum")
