"""Database connection and session management."""

import re

from collections.abc import AsyncGenerator
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

# Context variable to store current tenant schema
_tenant_schema: ContextVar[str] = ContextVar("tenant_schema", default="public")


def set_tenant_schema(schema_name: str) -> None:
    """Set the current tenant schema."""
    _tenant_schema.set(schema_name)


def get_tenant_schema() -> str:
    """Get the current tenant schema."""
    return _tenant_schema.get()


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

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


def _validate_schema_name(schema: str) -> str:
    """Validate schema name to prevent SQL injection."""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema):
        raise ValueError(f"Invalid schema name: {schema}")
    return schema


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session dependency with schema switching."""
    async with AsyncSessionLocal() as session:
        # Set the search path for the session
        schema = get_tenant_schema()
        safe_schema = _validate_schema_name(schema)
        await session.execute(text(f"SET search_path TO {safe_schema}"))
        
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
