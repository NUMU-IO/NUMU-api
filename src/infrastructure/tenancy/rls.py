"""Row-Level Security (RLS) helpers for tenant isolation.

This module provides functions to manage PostgreSQL RLS context for
multi-tenant data isolation at the database level.

Usage:
    from src.infrastructure.tenancy.rls import set_tenant_context, clear_tenant_context

    # Set tenant context before queries
    await set_tenant_context(session, tenant_id)

    # Clear context when done (optional, but recommended)
    await clear_tenant_context(session)

    # For admin operations that bypass RLS
    await enable_rls_bypass(session)
    await disable_rls_bypass(session)
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | str) -> None:
    """Set the current tenant context for RLS policies.

    This function sets the PostgreSQL session variable `app.current_tenant`
    which is used by RLS policies to filter data. Must be called before
    any database operations that require tenant isolation.

    Args:
        session: The SQLAlchemy async session
        tenant_id: The UUID of the current tenant

    Example:
        async with get_db_session() as session:
            await set_tenant_context(session, tenant_id)
            # All queries now filtered by tenant_id
            products = await session.execute(select(Product))
    """
    tenant_id_str = str(tenant_id) if isinstance(tenant_id, UUID) else tenant_id

    # Validate UUID format to prevent injection
    try:
        UUID(tenant_id_str)
    except ValueError:
        raise ValueError(f"Invalid tenant_id format: {tenant_id_str}")

    # Set the session variable using the database function
    # The third parameter 'true' makes it local to the current transaction
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": tenant_id_str}
    )
    logger.debug(f"Set tenant context to: {tenant_id_str}")


async def clear_tenant_context(session: AsyncSession) -> None:
    """Clear the current tenant context.

    This resets the `app.current_tenant` session variable, which will
    cause RLS policies to block all access (since no tenant matches NULL).
    Useful for cleanup between requests.

    Args:
        session: The SQLAlchemy async session
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant', '', true)")
    )
    logger.debug("Cleared tenant context")


async def get_current_tenant_context(session: AsyncSession) -> UUID | None:
    """Get the currently set tenant context.

    Args:
        session: The SQLAlchemy async session

    Returns:
        The current tenant UUID if set, None otherwise
    """
    result = await session.execute(
        text("SELECT current_setting('app.current_tenant', true)")
    )
    value = result.scalar()

    if not value:
        return None

    try:
        return UUID(value)
    except ValueError:
        return None


async def enable_rls_bypass(session: AsyncSession) -> None:
    """Enable RLS bypass for admin/service operations.

    WARNING: This allows access to ALL tenant data. Use with extreme caution
    and only for legitimate admin operations like:
    - System-wide reporting
    - Data migrations
    - Admin dashboard queries
    - Background jobs that span tenants

    The bypass should be enabled for the minimum necessary scope and
    disabled immediately after the operation.

    Args:
        session: The SQLAlchemy async session

    Example:
        async with get_db_session() as session:
            await enable_rls_bypass(session)
            try:
                # Can access all tenant data
                all_stores = await session.execute(select(Store))
            finally:
                await disable_rls_bypass(session)
    """
    await session.execute(
        text("SELECT set_config('app.rls_bypass', 'true', true)")
    )
    logger.warning("RLS bypass ENABLED - all tenant data accessible")


async def disable_rls_bypass(session: AsyncSession) -> None:
    """Disable RLS bypass and restore normal tenant isolation.

    Should always be called after enable_rls_bypass() operations complete.

    Args:
        session: The SQLAlchemy async session
    """
    await session.execute(
        text("SELECT set_config('app.rls_bypass', 'false', true)")
    )
    logger.debug("RLS bypass disabled")


async def is_rls_bypassed(session: AsyncSession) -> bool:
    """Check if RLS bypass is currently enabled.

    Args:
        session: The SQLAlchemy async session

    Returns:
        True if bypass is enabled, False otherwise
    """
    result = await session.execute(
        text("SELECT current_setting('app.rls_bypass', true)")
    )
    value = result.scalar()
    return value == 'true'


class RLSContext:
    """Context manager for tenant RLS context.

    Provides a clean way to set and automatically clear tenant context.

    Usage:
        async with RLSContext(session, tenant_id):
            # Queries are filtered by tenant_id
            products = await session.execute(select(Product))
        # Context automatically cleared
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID | str):
        self.session = session
        self.tenant_id = tenant_id

    async def __aenter__(self):
        await set_tenant_context(self.session, self.tenant_id)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await clear_tenant_context(self.session)
        return False


class RLSBypassContext:
    """Context manager for RLS bypass operations.

    Provides a safe way to temporarily bypass RLS for admin operations.

    Usage:
        async with RLSBypassContext(session):
            # Can access all tenant data
            stats = await get_cross_tenant_statistics(session)
        # Bypass automatically disabled
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def __aenter__(self):
        await enable_rls_bypass(self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await disable_rls_bypass(self.session)
        return False
