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

# Context variable to store current user ID for marketplace user-scoped RLS.
# Distinct from tenant_id because marketplace tables (purchases, reviews)
# are NOT tenant-scoped — they belong to a user across all the stores
# they own. Set by the auth middleware after JWT validation; cleared
# at request end. Consumed by `app.current_user` Postgres session var.
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


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


def set_user_id(user_id: UUID | str | None) -> None:
    """Set the current user ID for marketplace user-scoped RLS.

    Should be set by the auth middleware after JWT verification.
    Marketplace RLS policies (purchases, reviews) compare the row's
    `user_id` against this value. Public/anonymous requests pass None,
    which is fine for read-only marketplace catalog queries (the
    reviews SELECT policy is `USING (true)`; the purchases SELECT
    policy correctly returns zero rows for anonymous callers).
    """
    if user_id is None:
        _user_id.set(None)
    else:
        _user_id.set(str(user_id))


def get_user_id() -> str | None:
    """Get the current user ID for marketplace RLS."""
    return _user_id.get()


def reset_user_id() -> None:
    _user_id.set(None)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


# Create async engine with configurable pool settings.
#
# Process-role-aware sizing: the Celery worker container sets
# PROCESS_ROLE=celery so background jobs (analytics rollups, health
# scores, shipment syncs) get their own smaller pool instead of
# competing with the API for the same connections. The API container
# keeps the default (PROCESS_ROLE=api).
#
# `pool_use_lifo=True` → reuse the most-recently-returned connection, so
# idle connections farther back in the pool get a chance to expire via
# `pool_recycle`; LIFO also tends to keep Postgres's own backend cache warm.
#
# `connect_args["server_settings"]["statement_timeout"]` → asyncpg sends
# this at connection setup. Any single statement taking longer than the
# configured ms is killed by Postgres with SIGTERM, releasing the
# connection immediately. Critical for keeping analytics queries from
# starving the pool.
_is_celery = settings.process_role.lower() == "celery"
_pool_size = settings.celery_db_pool_size if _is_celery else settings.db_pool_size
_max_overflow = (
    settings.celery_db_max_overflow if _is_celery else settings.db_max_overflow
)

# Celery tasks call `_run_async()` multiple times, each creating its own
# fresh event loop. asyncpg connections are pinned to the loop that
# opened them — a pooled connection from a closed loop blows up on
# reuse with "'NoneType' object has no attribute 'send'" the moment a
# subsequent task tries to ping it. NullPool sidesteps this by opening
# a fresh connection per session and never holding any across loops.
# The trade-off is a few extra ms per task to reconnect, which is
# negligible compared to the marketplace build pipeline's overall cost.
_engine_kwargs: dict = {
    "echo": settings.debug,
    "connect_args": {
        "server_settings": {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            # Kill connections whose client has gone away (e.g. uvicorn
            # worker recycled mid-request) instead of letting Postgres
            # wait for a TCP FIN that may never arrive.
            "idle_in_transaction_session_timeout": "60000",
            # Tag the Postgres backend with which role opened it — shows
            # up in pg_stat_activity.application_name so we can tell API
            # vs Celery connections apart when diagnosing pool issues.
            "application_name": f"numu-{settings.process_role}",
        },
    },
}
if _is_celery:
    from sqlalchemy.pool import NullPool

    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(
        pool_pre_ping=True,
        pool_use_lifo=True,
        pool_size=_pool_size,
        max_overflow=_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )

engine = create_async_engine(settings.database_url, **_engine_kwargs)


# ---------------------------------------------------------------------------
# Connection pool monitoring
# ---------------------------------------------------------------------------


@event.listens_for(Pool, "checkout")
def _pool_checkout(dbapi_conn, connection_record, connection_proxy) -> None:  # type: ignore[misc]
    """Log + emit gauges when a connection is checked out of the pool."""
    pool = connection_proxy._pool
    if not hasattr(pool, "size"):
        return  # NullPool (used by alembic) doesn't support pool stats
    size = pool.size()
    checked_out = pool.checkedout()
    overflow = pool.overflow()
    logger.debug(
        "db_pool_checkout",
        extra={
            "pool_size": size,
            "checked_out": checked_out,
            "overflow": overflow,
        },
    )
    # Step 16 — update Prometheus gauges from the same hook. Importing
    # inside the listener keeps a circular-import risk off the module
    # load path (the metrics module pulls in prometheus_client, which
    # is fine, but this function fires for alembic too and the lazy
    # import keeps that path cold).
    try:
        from src.infrastructure.observability.prometheus_metrics import (
            db_connections_in_use,
            db_connections_overflow,
            db_connections_pool_size,
        )

        db_connections_in_use.set(checked_out)
        db_connections_pool_size.set(size)
        db_connections_overflow.set(overflow)
    except Exception:  # noqa: BLE001 — metrics must never break pool flow
        pass


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
        # Set the search path for the session. Include `public` as a fallback
        # so shared objects (enum types like `service_type_enum`, public
        # tables like `users`/`tenants`) resolve when referenced unqualified
        # — while the tenant schema still takes precedence for tenant tables.
        schema = get_tenant_schema()
        safe_schema = _validate_schema_name(schema)
        await session.execute(text(f"SET search_path TO {safe_schema}, public"))

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

        # Set the user context for marketplace user-scoped RLS. The
        # marketplace policies fall back to admin_bypass for sessions
        # without a user (cron jobs, admin reads); strict policies are
        # only triggered when the auth middleware populated the
        # contextvar. Validate UUID format identical to tenant_id —
        # never interpolate the raw contextvar value.
        user_id = get_user_id()
        if user_id:
            try:
                UUID(user_id)
                await session.execute(
                    text("SELECT set_config('app.current_user', :user_id, true)"),
                    {"user_id": user_id},
                )
            except ValueError:
                logger.warning(
                    f"Invalid user_id format: {user_id}, skipping user RLS context"
                )
        else:
            await session.execute(
                text("SELECT set_config('app.current_user', '', true)")
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
