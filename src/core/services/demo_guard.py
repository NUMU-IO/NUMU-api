"""Demo-mode guard for external integrations.

Demo tenants must NOT trigger real side effects in external services
(Bosta shipping, Paymob payments, ETA invoicing, Resend emails, WhatsApp).

Usage in an adapter::

    from src.core.services.demo_guard import ensure_not_demo_sync

    class BostaShippingService:
        async def create_shipment(self, tenant, ...):
            ensure_not_demo_sync(tenant, "bosta.create_shipment")
            # ... real Bosta API call ...
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class DemoOperationBlocked(Exception):
    """Raised when a demo tenant attempts a real external side-effect."""

    def __init__(self, operation: str, tenant_id: UUID) -> None:
        self.operation = operation
        self.tenant_id = tenant_id
        super().__init__(f"Operation '{operation}' blocked for demo tenant {tenant_id}")


@dataclass
class DemoSimulatedResponse:
    """Fake response returned instead of calling a real external API."""

    success: bool = True
    simulated: bool = True
    message: str = "Simulated response for demo tenant."
    data: dict[str, Any] = field(default_factory=dict)


def is_demo_tenant_sync(tenant) -> bool:
    """Check if a loaded TenantModel is a demo."""
    if hasattr(tenant, "is_demo"):
        return tenant.is_demo
    if hasattr(tenant, "lifecycle_state"):
        return tenant.lifecycle_state == "demo"
    return False


def ensure_not_demo_sync(tenant, operation: str) -> None:
    """Synchronous guard. Use when you already have the tenant loaded."""
    if is_demo_tenant_sync(tenant):
        logger.info(
            "demo_operation_blocked",
            extra={
                "operation": operation,
                "tenant_id": str(getattr(tenant, "id", "unknown")),
            },
        )
        raise DemoOperationBlocked(
            operation=operation, tenant_id=getattr(tenant, "id", UUID(int=0))
        )


async def resolve_is_demo(tenant_id: UUID, db_session=None) -> bool:
    """Resolve whether a tenant is in demo mode by querying the database."""
    if db_session is None:
        return False
    from src.infrastructure.tenancy.repository import TenantRepository

    repo = TenantRepository(db_session)
    tenant = await repo.get_by_id(tenant_id)
    return is_demo_tenant_sync(tenant) if tenant else False


async def ensure_not_demo(tenant_id: UUID, operation: str, db_session=None) -> None:
    """Async guard. Resolves the tenant from the DB if needed."""
    if await resolve_is_demo(tenant_id, db_session):
        logger.info(
            "demo_operation_blocked",
            extra={"operation": operation, "tenant_id": str(tenant_id)},
        )
        raise DemoOperationBlocked(operation=operation, tenant_id=tenant_id)
