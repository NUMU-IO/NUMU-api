"""Add configuration request, service credential, and audit log tables.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-04

These tables support:
- ConfigurationRequest: Merchant requests for credential setup
- ServiceCredential: Encrypted storage of service credentials (AES-256)
- CredentialAuditLog: Audit trail for all credential operations
"""

from alembic import op

# revision identifiers
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _create_enum_if_not_exists(conn, name: str, values: list[str]) -> None:
    """Create a PostgreSQL enum type if it doesn't already exist."""
    values_sql = ", ".join(f"'{v}'" for v in values)
    conn.exec_driver_sql(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN
                CREATE TYPE public.{name} AS ENUM ({values_sql});
            END IF;
        END $$;
    """)


def upgrade() -> None:
    conn = op.get_bind()

    # Create enum types (idempotent)
    _create_enum_if_not_exists(conn, "service_type_enum", [
        "payment_gateway", "shipping_carrier", "whatsapp", "sms", "email",
    ])
    _create_enum_if_not_exists(conn, "service_name_enum", [
        "fawry", "paymob", "vodafone_cash", "bank_transfer", "stripe", "tap",
        "aramex", "bosta", "mylerz", "whatsapp_business", "twilio",
    ])
    _create_enum_if_not_exists(conn, "request_status_enum", [
        "pending", "in_progress", "completed", "rejected", "cancelled",
    ])
    _create_enum_if_not_exists(conn, "request_priority_enum", [
        "low", "normal", "high", "urgent",
    ])

    # --- configuration_requests (raw SQL to avoid SQLAlchemy enum auto-creation) ---
    if conn.exec_driver_sql("SELECT to_regclass('public.configuration_requests')").scalar() is None:
        conn.exec_driver_sql("""
            CREATE TABLE public.configuration_requests (
                id UUID NOT NULL PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
                requested_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
                service_type public.service_type_enum NOT NULL,
                service_name public.service_name_enum NOT NULL,
                status public.request_status_enum NOT NULL DEFAULT 'pending',
                priority public.request_priority_enum NOT NULL DEFAULT 'normal',
                merchant_notes TEXT,
                assigned_to UUID REFERENCES public.users(id) ON DELETE SET NULL,
                admin_notes TEXT,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_config_requests_tenant_status "
        "ON public.configuration_requests (tenant_id, status)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_config_requests_assigned "
        "ON public.configuration_requests (assigned_to)"
    )

    # --- service_credentials ---
    if conn.exec_driver_sql("SELECT to_regclass('public.service_credentials')").scalar() is None:
        conn.exec_driver_sql("""
            CREATE TABLE public.service_credentials (
                id UUID NOT NULL PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
                service_type public.service_type_enum NOT NULL,
                service_name public.service_name_enum NOT NULL,
                credentials_encrypted BYTEA NOT NULL,
                encryption_key_id VARCHAR(100) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_validated BOOLEAN NOT NULL DEFAULT FALSE,
                last_validated_at TIMESTAMPTZ,
                configured_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
                metadata JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_service_credentials_tenant_service "
        "ON public.service_credentials (tenant_id, service_type, service_name)"
    )

    # --- credential_audit_logs ---
    if conn.exec_driver_sql("SELECT to_regclass('public.credential_audit_logs')").scalar() is None:
        conn.exec_driver_sql("""
            CREATE TABLE public.credential_audit_logs (
                id UUID NOT NULL PRIMARY KEY,
                tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
                admin_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
                user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
                action VARCHAR(50) NOT NULL,
                service_type public.service_type_enum NOT NULL,
                service_name public.service_name_enum NOT NULL,
                ip_address VARCHAR(45),
                user_agent TEXT,
                details JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created "
        "ON public.credential_audit_logs (tenant_id, created_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_action "
        "ON public.credential_audit_logs (action)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DROP TABLE IF EXISTS public.credential_audit_logs")
    conn.exec_driver_sql("DROP TABLE IF EXISTS public.service_credentials")
    conn.exec_driver_sql("DROP TABLE IF EXISTS public.configuration_requests")
    for name in ["request_priority_enum", "request_status_enum", "service_name_enum", "service_type_enum"]:
        conn.exec_driver_sql(f"DROP TYPE IF EXISTS public.{name}")
