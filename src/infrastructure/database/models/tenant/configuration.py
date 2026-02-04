"""Database models for service configuration and credential management.

This module defines the models for:
- ConfigurationRequest: Merchant requests for credential setup
- ServiceCredential: Encrypted storage of service credentials
- CredentialAuditLog: Audit trail for all credential operations

SECURITY NOTE:
- Credentials are stored encrypted using AES-256
- API keys are NEVER stored in plaintext
- All operations are logged for audit purposes
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID as PyUUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ServiceType(StrEnum):
    """Types of external services that require credentials."""

    PAYMENT_GATEWAY = "payment_gateway"
    SHIPPING_CARRIER = "shipping_carrier"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class ServiceName(StrEnum):
    """Specific service providers."""

    # Payment Gateways
    FAWRY = "fawry"
    PAYMOB = "paymob"
    VODAFONE_CASH = "vodafone_cash"
    BANK_TRANSFER = "bank_transfer"
    STRIPE = "stripe"
    TAP = "tap"

    # Shipping Carriers
    ARAMEX = "aramex"
    BOSTA = "bosta"
    MYLERZ = "mylerz"

    # Communication
    WHATSAPP_BUSINESS = "whatsapp_business"
    TWILIO = "twilio"


class RequestStatus(StrEnum):
    """Status of a configuration request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RequestPriority(StrEnum):
    """Priority level for configuration requests."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ConfigurationRequest(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Model for merchant configuration requests.

    When a merchant needs to configure a service (payment gateway, shipping, etc.),
    they create a request which is then handled by an administrator.
    """

    __tablename__ = "configuration_requests"
    __table_args__ = (
        Index("idx_config_requests_tenant_status", "tenant_id", "status"),
        Index("idx_config_requests_assigned", "assigned_to"),
        {"schema": "tenant"},
    )

    # Who requested
    requested_by: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # What needs to be configured
    service_type: Mapped[ServiceType] = mapped_column(
        SQLEnum(ServiceType, name="service_type_enum", create_type=False),
        nullable=False,
    )
    service_name: Mapped[ServiceName] = mapped_column(
        SQLEnum(ServiceName, name="service_name_enum", create_type=False),
        nullable=False,
    )

    # Request status
    status: Mapped[RequestStatus] = mapped_column(
        SQLEnum(RequestStatus, name="request_status_enum", create_type=False),
        default=RequestStatus.PENDING,
        nullable=False,
    )
    priority: Mapped[RequestPriority] = mapped_column(
        SQLEnum(RequestPriority, name="request_priority_enum", create_type=False),
        default=RequestPriority.NORMAL,
        nullable=False,
    )

    # Optional notes from merchant
    merchant_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Admin handling
    assigned_to: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Completion timestamp
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<ConfigurationRequest(id={self.id}, service={self.service_name}, status={self.status})>"


class ServiceCredential(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Model for encrypted service credentials.

    SECURITY:
    - credentials_encrypted contains AES-256 encrypted JSON
    - encryption_key_id references the key in the secrets manager
    - Credentials are NEVER decrypted in the application layer except when needed
    """

    __tablename__ = "service_credentials"
    __table_args__ = (
        Index(
            "idx_service_credentials_tenant_service",
            "tenant_id",
            "service_type",
            "service_name",
            unique=True,
        ),
        {"schema": "tenant"},
    )

    # Service identification
    service_type: Mapped[ServiceType] = mapped_column(
        SQLEnum(ServiceType, name="service_type_enum", create_type=False),
        nullable=False,
    )
    service_name: Mapped[ServiceName] = mapped_column(
        SQLEnum(ServiceName, name="service_name_enum", create_type=False),
        nullable=False,
    )

    # Encrypted credentials (AES-256)
    credentials_encrypted: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )

    # Reference to encryption key in secrets manager
    encryption_key_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Status flags
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Who configured this
    configured_by: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.admins.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Metadata (non-sensitive info like account name, masked values for display)
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    def __repr__(self) -> str:
        return f"<ServiceCredential(id={self.id}, service={self.service_name}, active={self.is_active})>"


class CredentialAuditLog(Base, UUIDMixin, TenantMixin):
    """Audit log for all credential-related operations.

    This provides a complete audit trail for compliance and security monitoring.
    """

    __tablename__ = "credential_audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index("idx_audit_logs_action", "action"),
        {"schema": "tenant"},
    )

    # Who performed the action
    admin_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # What action was performed
    action: Mapped[str] = mapped_column(String(50), nullable=False)

    # Which service
    service_type: Mapped[ServiceType] = mapped_column(
        SQLEnum(ServiceType, name="service_type_enum", create_type=False),
        nullable=False,
    )
    service_name: Mapped[ServiceName] = mapped_column(
        SQLEnum(ServiceName, name="service_name_enum", create_type=False),
        nullable=False,
    )

    # Context
    ip_address: Mapped[str | None] = mapped_column(
        String(45), nullable=True
    )  # IPv6 compatible
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<CredentialAuditLog(id={self.id}, action={self.action}, service={self.service_name})>"


# Audit action constants
class AuditAction:
    """Constants for audit log actions."""

    REQUEST_CREATED = "request_created"
    REQUEST_ASSIGNED = "request_assigned"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_REJECTED = "request_rejected"
    REQUEST_CANCELLED = "request_cancelled"

    CREDENTIALS_CONFIGURED = "credentials_configured"
    CREDENTIALS_UPDATED = "credentials_updated"
    CREDENTIALS_VALIDATED = "credentials_validated"
    CREDENTIALS_VALIDATION_FAILED = "credentials_validation_failed"
    CREDENTIALS_ENABLED = "credentials_enabled"
    CREDENTIALS_DISABLED = "credentials_disabled"
    CREDENTIALS_REVOKED = "credentials_revoked"

    SERVICE_ENABLED = "service_enabled"
    SERVICE_DISABLED = "service_disabled"
