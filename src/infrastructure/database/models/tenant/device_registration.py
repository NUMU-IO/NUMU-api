"""DeviceRegistration database model.

ONE table serves BOTH push clients:

  * ``webpush`` — the merchant-hub PWA (VAPID / RFC 8291)
  * ``expo``    — the numu-merchant-app React Native client

That is deliberate, not incidental. ``numu-merchant-app`` has been calling
``POST /auth/me/push-token`` with ``provider: "expo"`` since before this table
existed; the endpoint simply 404'd and the app swallowed the error. Building a
separate mobile table would have meant two schemas, two fan-outs and two places
to prune dead endpoints, for one product behaviour.

Tenant-scoped (RLS) like every other merchant-owned row, and additionally
scoped to a ``user_id`` so a staff member is only notified about stores they can
actually access.

``tenant_id`` is NULLABLE, and NULL means the device belongs to the PLATFORM
rather than to a store — a NUMU operator's admin backoffice, which has no
tenant. See the RLS note on the column.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class DeviceRegistrationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A single device/browser subscribed to push for one user."""

    __tablename__ = "device_registrations"
    __table_args__ = (
        # Re-registering the same browser/device must UPDATE, never duplicate.
        # Browsers re-issue the same endpoint on every subscribe() call, so
        # without this a merchant accumulates a row per page load.
        UniqueConstraint("endpoint", name="uq_device_registrations_endpoint"),
        Index(
            "idx_device_registrations_tenant_user_active",
            "tenant_id",
            "user_id",
            "revoked_at",
        ),
        # Platform (staff) devices, which the composite index above cannot
        # serve because a NULL leading column is not a selective prefix.
        Index(
            "idx_device_registrations_platform_active",
            "revoked_at",
            postgresql_where=text("tenant_id IS NULL"),
        ),
        {"schema": "public"},
    )

    # NULL = a platform operator's device, which belongs to no store. The RLS
    # policy exposes those rows only when no `app.current_tenant` is set, so a
    # merchant session can never reach a staff device and vice versa.
    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # "webpush" | "expo"
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # "web" | "ios" | "android"
    platform: Mapped[str] = mapped_column(String(16), nullable=False)

    # Web Push endpoint URL, or the Expo push token. Long: FCM/Mozilla
    # endpoints routinely exceed 300 chars.
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)

    # RFC 8291 encryption material. NULL for Expo, which does its own transport.
    p256dh: Mapped[str | None] = mapped_column(String(256), nullable=True)
    auth: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Drives the notification language. A merchant using the AR dashboard must
    # not get English push.
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Expo-only: custom notification sound registered by the mobile app.
    sound: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Consecutive delivery failures. A hard 404/410 revokes immediately; this
    # tracks soft failures so a flaky endpoint can be aged out.
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Soft revoke — kept for auditability rather than deleted outright.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DeviceRegistration {self.provider}/{self.platform} "
            f"user={self.user_id} revoked={self.revoked_at is not None}>"
        )
