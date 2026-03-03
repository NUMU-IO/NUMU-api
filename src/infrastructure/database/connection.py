"""Database connection and session management.

This module provides:
- Async SQLAlchemy engine and session factory
- Tenant schema switching via search_path
- Row-Level Security (RLS) context management
- Session lifecycle management with automatic tenant isolation
- Connection pool monitoring via SQLAlchemy events
"""

import logging
import re
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool

from src.config import settings

logger = logging.getLogger(__name__)

# Context variable to store current tenant schema
_tenant_schema: ContextVar[str] = ContextVar("tenant_schema", default="public")

# Context variable to store current tenant ID for RLS
_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def set_tenant_schema(schema_name: str) -> None:
    """Set the current tenant schema."""
    _tenant_schema.set(schema_name)


def get_tenant_schema() -> str:
    """Get the current tenant schema."""
    return _tenant_schema.get()


def set_tenant_id(tenant_id: UUID | str | None) -> None:
    """Set the current tenant ID for RLS policies.

    This should be called alongside set_tenant_schema() when
    processing tenant requests. The tenant ID is used by PostgreSQL
    RLS policies to filter data at the database level.

    Args:
        tenant_id: The UUID of the current tenant, or None to clear
    """
    if tenant_id is None:
        _tenant_id.set(None)
    else:
        _tenant_id.set(str(tenant_id))


def get_tenant_id() -> str | None:
    """Get the current tenant ID for RLS policies.

    Returns:
        The tenant ID string if set, None otherwise
    """
    return _tenant_id.get()


def reset_tenant_id() -> None:
    """Reset tenant ID to None (for cleanup)."""
    _tenant_id.set(None)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


# Create async engine with configurable pool settings
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
)


# ---------------------------------------------------------------------------
# Connection pool monitoring
# ---------------------------------------------------------------------------


@event.listens_for(Pool, "checkout")
def _pool_checkout(dbapi_conn, connection_record, connection_proxy) -> None:  # type: ignore[misc]
    """Log when a connection is checked out of the pool."""
    logger.debug(
        "db_pool_checkout",
        extra={
            "pool_size": connection_proxy._pool.size(),
            "checked_out": connection_proxy._pool.checkedout(),
            "overflow": connection_proxy._pool.overflow(),
        },
    )


@event.listens_for(Pool, "connect")
def _pool_connect(dbapi_conn, connection_record) -> None:  # type: ignore[misc]
    """Log when a new physical connection is created."""
    logger.info("db_pool_new_connection")


# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def reset_tenant_schema() -> None:
    """Reset tenant schema to public (for cleanup)."""
    _tenant_schema.set("public")


def reset_tenant_context() -> None:
    """Reset all tenant context (schema and ID) for cleanup.

    This should be called at the end of each request to ensure
    clean state for the next request.
    """
    _tenant_schema.set("public")
    _tenant_id.set(None)


def _validate_schema_name(schema: str) -> str:
    """Validate schema name to prevent SQL injection."""
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", schema):
        raise ValueError(f"Invalid schema name: {schema}")
    return schema


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session dependency with schema switching and RLS context.

    This function:
    1. Sets the PostgreSQL search_path to the tenant's schema
    2. Sets the app.current_tenant session variable for RLS policies
    3. Manages transaction lifecycle (commit on success, rollback on error)

    The RLS context ensures that even if application code doesn't filter
    by tenant_id, the database will enforce tenant isolation.
    """
    async with AsyncSessionLocal() as session:
        # Set the search path for the session
        schema = get_tenant_schema()
        safe_schema = _validate_schema_name(schema)
        await session.execute(text(f"SET search_path TO {safe_schema}"))

        # Set the tenant context for RLS policies
        tenant_id = get_tenant_id()
        if tenant_id:
            # Validate UUID format to prevent injection
            try:
                UUID(tenant_id)
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
                logger.debug(f"Set RLS context for tenant: {tenant_id}")
            except ValueError:
                logger.warning(
                    f"Invalid tenant_id format: {tenant_id}, skipping RLS context"
                )
        else:
            # Clear any existing tenant context when no tenant is set
            await session.execute(
                text("SELECT set_config('app.current_tenant', '', true)")
            )

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_admin_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session with RLS bypass for admin operations.

    WARNING: This session bypasses Row-Level Security and can access
    ALL tenant data. Use only for legitimate admin operations like:
    - System-wide reporting
    - Data migrations
    - Admin dashboard queries
    - Background jobs that span tenants

    The session operates in the public schema with RLS bypass enabled.
    """
    async with AsyncSessionLocal() as session:
        # Set search path to public for admin operations
        await session.execute(text("SET search_path TO public"))

        # Enable RLS bypass
        await session.execute(text("SELECT set_config('app.rls_bypass', 'true', true)"))
        logger.warning("Admin session created with RLS bypass enabled")

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # Disable bypass before closing
            await session.execute(
                text("SELECT set_config('app.rls_bypass', 'false', true)")
            )
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
