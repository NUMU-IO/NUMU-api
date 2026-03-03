"""Centralized audit logging service.

Provides a single entry point for recording all security-relevant events:
  - Auth events (login, logout, password change, token refresh, failed attempts)
  - Payment events (webhook received, status transitions, refunds)
  - Order status changes (including old_value → new_value)
  - Admin actions (user management, permission changes)
  - Data access events (export, bulk operations)

Usage:
    audit = AuditService(db_session)
    await audit.log(
        event_type="auth.login",
        user_id=user.id,
        action="login",
        resource_type="user",
        resource_id=str(user.id),
        ip_address=request.client.host,
        details={"method": "password"},
    )
"""

from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.audit import AuditLogModel

logger = logging.getLogger(__name__)


class Severity(StrEnum):
    """Audit log severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventType(StrEnum):
    """Well-known audit event types."""

    # Auth events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    AUTH_REGISTER = "auth.register"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    AUTH_PASSWORD_RESET = "auth.password_reset"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_EMAIL_VERIFY = "auth.email_verify"
    AUTH_LOCKOUT = "auth.lockout"

    # Payment events
    PAYMENT_WEBHOOK = "payment.webhook"
    PAYMENT_STATUS_CHANGE = "payment.status_change"
    PAYMENT_REFUND = "payment.refund"
    PAYMENT_FAILED = "payment.failed"

    # Order events
    ORDER_STATUS_CHANGE = "order.status_change"
    ORDER_CREATE = "order.create"
    ORDER_CANCEL = "order.cancel"

    # Admin events
    ADMIN_USER_UPDATE = "admin.user_update"
    ADMIN_PERMISSION_CHANGE = "admin.permission_change"
    ADMIN_STORE_UPDATE = "admin.store_update"
    ADMIN_CONFIG_CHANGE = "admin.config_change"

    # Data events
    DATA_EXPORT = "data.export"
    DATA_IMPORT = "data.import"
    DATA_BULK_DELETE = "data.bulk_delete"

    # Coupon events
    COUPON_APPLY = "coupon.apply"
    COUPON_CREATE = "coupon.create"
    COUPON_UPDATE = "coupon.update"

    # Product events
    PRODUCT_CREATE = "product.create"
    PRODUCT_UPDATE = "product.update"
    PRODUCT_DELETE = "product.delete"
    PRODUCT_IMAGE_UPLOAD = "product.image_upload"


class AuditService:
    """Centralized audit logging service.

    Wraps ``AuditLogModel`` creation with a clean API and automatic
    defaults. The caller is responsible for flushing/committing the
    session — the service only adds the row to the session.
    """

    __slots__ = ("_db",)

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        *,
        event_type: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        user_id: UUID | None = None,
        customer_id: UUID | None = None,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        severity: str = Severity.INFO,
        old_value: dict | None = None,
        new_value: dict | None = None,
        details: dict | None = None,
    ) -> AuditLogModel:
        """Record an audit log entry.

        Parameters
        ----------
        event_type : str
            A dotted event name, e.g. ``auth.login`` or ``order.status_change``.
        action : str
            The operation performed: ``create``, ``update``, ``delete``, ``login``, etc.
        old_value / new_value : dict, optional
            For state-change events, capture the before/after snapshot.
        details : dict, optional
            Arbitrary JSON metadata.  ``old_value`` / ``new_value`` are merged
            into this dict under keys ``old_value`` and ``new_value`` if provided.
        """
        merged_details: dict = details.copy() if details else {}
        if old_value is not None:
            merged_details["old_value"] = old_value
        if new_value is not None:
            merged_details["new_value"] = new_value

        entry = AuditLogModel(
            event_type=event_type,
            severity=severity,
            user_id=user_id,
            customer_id=customer_id,
            store_id=store_id,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            ip_address=ip_address,
            user_agent=user_agent,
            details=merged_details,
        )
        self._db.add(entry)

        logger.info(
            "audit | %s | %s | resource=%s/%s | user=%s | ip=%s",
            event_type,
            action,
            resource_type,
            resource_id,
            user_id,
            ip_address,
        )

        return entry

    # ------------------------------------------------------------------ #
    # Convenience helpers for common events
    # ------------------------------------------------------------------ #

    async def log_auth(
        self,
        *,
        event_type: str,
        user_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        tenant_id: UUID | None = None,
        severity: str = Severity.INFO,
        details: dict | None = None,
    ) -> AuditLogModel:
        """Record an authentication event."""
        return await self.log(
            event_type=event_type,
            action=event_type.split(".")[-1],
            resource_type="user",
            resource_id=str(user_id) if user_id else None,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            tenant_id=tenant_id,
            severity=severity,
            details=details,
        )

    async def log_order_status_change(
        self,
        *,
        order_id: UUID,
        store_id: UUID,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        old_status: str,
        new_status: str,
        ip_address: str | None = None,
        details: dict | None = None,
    ) -> AuditLogModel:
        """Record an order status transition."""
        return await self.log(
            event_type=EventType.ORDER_STATUS_CHANGE,
            action="status_change",
            resource_type="order",
            resource_id=str(order_id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            old_value={"status": old_status},
            new_value={"status": new_status},
            details=details,
        )

    async def log_payment_event(
        self,
        *,
        event_type: str,
        order_id: UUID | None = None,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        severity: str = Severity.INFO,
        details: dict | None = None,
    ) -> AuditLogModel:
        """Record a payment-related event."""
        return await self.log(
            event_type=event_type,
            action=event_type.split(".")[-1],
            resource_type="order",
            resource_id=str(order_id) if order_id else None,
            store_id=store_id,
            tenant_id=tenant_id,
            severity=severity,
            details=details,
        )
