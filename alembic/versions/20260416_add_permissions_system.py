"""Add permissions system tables.

Revision ID: add_perms_sys_001
Revises: wa0413b2c3d4
Create Date: 2026-04-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "add_perms_sys_001"
down_revision: str | None = "wa0413b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("qualifier", sa.String(50), nullable=True),
        sa.Column(
            "scope_type",
            sa.Enum(
                "ALL",
                "OWN",
                "ASSIGNED",
                "RESOURCE",
                name="permissionscopetype",
                schema="public",
            ),
            nullable=False,
            server_default="ALL",
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "dependencies",
            sa.dialects.postgresql.ARRAY(sa.String(100)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "risk_level",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                "CRITICAL",
                name="permissionrisklevel",
                schema="public",
            ),
            nullable=False,
            server_default="LOW",
        ),
        sa.Column("is_app", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "plugin_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
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
    op.create_index("ix_permissions_code", "permissions", ["code"], schema="public")
    op.create_index("ix_permissions_domain", "permissions", ["domain"], schema="public")

    op.create_table(
        "roles",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "cloned_from_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("deleted_at", sa.Boolean(), nullable=False, server_default="false"),
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
        "ix_roles_tenant_slug",
        "roles",
        ["tenant_id", "slug"],
        unique=True,
        schema="public",
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"], schema="public")

    op.create_table(
        "role_permissions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scope_qualifier",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "granted_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
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
        "ix_role_permissions_role_id", "role_permissions", ["role_id"], schema="public"
    )
    op.create_index(
        "ix_role_permissions_permission_id",
        "role_permissions",
        ["permission_id"],
        schema="public",
    )

    op.create_table(
        "tenant_memberships",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "INVITED",
                "ACTIVE",
                "SUSPENDED",
                "REVOKED",
                name="membershipstatus",
                schema="public",
            ),
            nullable=False,
            server_default="INVITED",
        ),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "invited_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "permission_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "two_factor_required", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_tenant_memberships_user_tenant",
        "tenant_memberships",
        ["user_id", "tenant_id"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_tenant_memberships_tenant_status",
        "tenant_memberships",
        ["tenant_id", "status"],
        schema="public",
    )
    op.create_index(
        "ix_tenant_memberships_user_id",
        "tenant_memberships",
        ["user_id"],
        schema="public",
    )

    op.create_table(
        "membership_roles",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "membership_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
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
        "ix_membership_roles_membership_id",
        "membership_roles",
        ["membership_id"],
        schema="public",
    )
    op.create_index(
        "ix_membership_roles_role_id", "membership_roles", ["role_id"], schema="public"
    )

    op.create_table(
        "membership_permission_overrides",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "membership_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "effect",
            sa.Enum("ALLOW", "DENY", name="overrideeffect", schema="public"),
            nullable=False,
        ),
        sa.Column(
            "scope_qualifier",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "granted_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
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
        "ix_membership_overrides_membership",
        "membership_permission_overrides",
        ["membership_id", "permission_id"],
        schema="public",
    )

    op.create_table(
        "staff_invitations",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "pre_assigned_role_ids",
            sa.dialects.postgresql.ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "invited_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text, nullable=True),
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
        "ix_staff_invitations_tenant_email",
        "staff_invitations",
        ["tenant_id", "email"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_staff_invitations_token_hash",
        "staff_invitations",
        ["token_hash"],
        schema="public",
    )

    op.create_table(
        "staff_sessions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("jti", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "membership_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("device_label", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_staff_sessions_jti", "staff_sessions", ["jti"], unique=True, schema="public"
    )
    op.create_index(
        "ix_staff_sessions_user_id", "staff_sessions", ["user_id"], schema="public"
    )

    op.create_table(
        "temporary_access_grants",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "membership_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_temporary_grants_membership",
        "temporary_access_grants",
        ["membership_id"],
        schema="public",
    )
    op.create_index(
        "ix_temporary_grants_valid_until",
        "temporary_access_grants",
        ["valid_until"],
        schema="public",
    )

    op.create_table(
        "access_requests",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requested_role_ids",
            sa.dialects.postgresql.ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "requested_permissions",
            sa.dialects.postgresql.ARRAY(sa.String(100)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "DENIED",
                "EXPIRED",
                "CANCELLED",
                name="accessrequeststatus",
                schema="public",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "reviewer_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_access_requests_tenant", "access_requests", ["tenant_id"], schema="public"
    )
    op.create_index(
        "ix_access_requests_requester",
        "access_requests",
        ["requester_user_id"],
        schema="public",
    )
    op.create_index(
        "ix_access_requests_status", "access_requests", ["status"], schema="public"
    )

    op.create_table(
        "staff_access_policies",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "membership_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "ip_allowlist",
            sa.dialects.postgresql.ARRAY(sa.String(50)),
            nullable=True,
        ),
        sa.Column(
            "working_hours",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "environment",
            sa.Enum(
                "ALL",
                "PROD_ONLY",
                "STAGING_ONLY",
                name="accesspolicyenv",
                schema="public",
            ),
            nullable=False,
            server_default="ALL",
        ),
        sa.Column("enforce_2fa", sa.Boolean(), nullable=False, server_default="false"),
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
        "ix_staff_access_policies_membership",
        "staff_access_policies",
        ["membership_id"],
        unique=True,
        schema="public",
    )

    op.create_table(
        "permission_change_logs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "actor_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "target_type",
            sa.Enum(
                "ROLE",
                "MEMBERSHIP",
                "OVERRIDE",
                "INVITATION",
                name="permissionchangetargettype",
                schema="public",
            ),
            nullable=False,
        ),
        sa.Column(
            "target_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "action",
            sa.Enum(
                "CREATED",
                "UPDATED",
                "DELETED",
                "ROLE_ASSIGNED",
                "ROLE_REVOKED",
                "PERM_ADDED",
                "PERM_REMOVED",
                "OVERRIDE_SET",
                "OVERRIDE_CLEARED",
                "OWNERSHIP_TRANSFERRED",
                name="permissionchangeaction",
                schema="public",
            ),
            nullable=False,
        ),
        sa.Column("before", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("after", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("diff", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_permission_change_logs_tenant",
        "permission_change_logs",
        ["tenant_id", "created_at"],
        schema="public",
    )
    op.create_index(
        "ix_permission_change_logs_target",
        "permission_change_logs",
        ["target_type", "target_id"],
        schema="public",
    )
    op.create_index(
        "ix_permission_change_logs_actor",
        "permission_change_logs",
        ["actor_user_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("permission_change_logs", schema="public")
    op.drop_table("staff_access_policies", schema="public")
    op.drop_table("access_requests", schema="public")
    op.drop_table("temporary_access_grants", schema="public")
    op.drop_table("staff_sessions", schema="public")
    op.drop_table("staff_invitations", schema="public")
    op.drop_table("membership_permission_overrides", schema="public")
    op.drop_table("membership_roles", schema="public")
    op.drop_table("tenant_memberships", schema="public")
    op.drop_table("role_permissions", schema="public")
    op.drop_table("roles", schema="public")
    op.drop_table("permissions", schema="public")

    op.execute("DROP TYPE IF EXISTS permissionchangetargettype")
    op.execute("DROP TYPE IF EXISTS permissionchangeaction")
    op.execute("DROP TYPE IF EXISTS accessrequeststatus")
    op.execute("DROP TYPE IF EXISTS accesspolicyenv")
    op.execute("DROP TYPE IF EXISTS overrideeffect")
    op.execute("DROP TYPE IF EXISTS membershipstatus")
    op.execute("DROP TYPE IF EXISTS permissionscopetype")
    op.execute("DROP TYPE IF EXISTS permissionrisklevel")
