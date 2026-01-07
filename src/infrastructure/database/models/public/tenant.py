"""Tenant database model (public schema)."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from src.infrastructure.database.connection import Base


def utc_now():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Tenant(Base):
    """Tenant model representing a store/merchant in the platform.
    
    This model lives in the 'public' PostgreSQL schema and is used
    to track all tenants and their corresponding database schemas.
    """
    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    subdomain = Column(String(63), unique=True, index=True, nullable=False)
    schema_name = Column(String(100), unique=True, nullable=False)
    owner_id = Column(UUID(as_uuid=True), nullable=True)  # FK to users table
    plan = Column(String(50), default="free")  # free, pro, enterprise
    is_active = Column(Boolean, default=True)
    settings = Column(String, nullable=True)  # JSON string for tenant-specific settings
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, subdomain={self.subdomain})>"
