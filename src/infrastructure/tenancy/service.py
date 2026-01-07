"""Tenant service for schema provisioning and management."""

import hashlib
import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import Base, engine
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)


class TenantService:
    """Service for managing tenants and their database schemas."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.tenant_repo = TenantRepository(db)
    
    async def create_tenant(
        self,
        name: str,
        subdomain: str,
        owner_id: str = None,
        plan: str = "free"
    ):
        """Create a new tenant with its own database schema.
        
        Args:
            name: Display name for the store
            subdomain: Unique subdomain (e.g., 'mystore' for mystore.octyrafiy.com)
            owner_id: UUID of the user who owns this store
            plan: Subscription plan (free, pro, enterprise)
        
        Returns:
            Created Tenant object
        
        Raises:
            ValueError: If subdomain is invalid or already exists
        """
        # Validate subdomain format
        if not self._validate_subdomain(subdomain):
            raise ValueError(
                f"Invalid subdomain '{subdomain}'. Must be 3-63 characters, "
                "lowercase alphanumeric with hyphens, cannot start/end with hyphen."
            )
        
        # Check for existing subdomain
        existing = await self.tenant_repo.get_by_subdomain(subdomain)
        if existing:
            raise ValueError(f"Subdomain '{subdomain}' already exists")
        
        # Generate safe schema name
        schema_name = self._generate_schema_name(subdomain)
        
        try:
            # Provision schema FIRST (before creating tenant record)
            await self._provision_schema(schema_name)
            
            # Create tenant record in public schema
            tenant = await self.tenant_repo.create(
                name=name,
                subdomain=subdomain,
                schema_name=schema_name,
                owner_id=owner_id,
                plan=plan,
                is_active=True
            )
            
            logger.info(f"Created tenant '{subdomain}' with schema '{schema_name}'")
            return tenant
            
        except Exception as e:
            # Rollback: drop schema if tenant creation fails
            logger.error(f"Failed to create tenant '{subdomain}': {e}")
            await self._drop_schema(schema_name)
            raise
    
    def _validate_subdomain(self, subdomain: str) -> bool:
        """Validate subdomain format (RFC 1123 compliant)."""
        if not subdomain or len(subdomain) < 3 or len(subdomain) > 63:
            return False
        # Must be lowercase alphanumeric with hyphens, no start/end with hyphen
        pattern = r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'
        return bool(re.match(pattern, subdomain.lower()))
    
    def _generate_schema_name(self, subdomain: str) -> str:
        """Generate a safe PostgreSQL schema name."""
        # Replace hyphens with underscores (hyphens not allowed in unquoted identifiers)
        safe_subdomain = subdomain.lower().replace("-", "_")
        # Add hash for uniqueness and collision avoidance
        schema_hash = hashlib.md5(subdomain.encode()).hexdigest()[:8]
        return f"tenant_{safe_subdomain}_{schema_hash}"
    
    async def _provision_schema(self, schema_name: str):
        """Create and initialize a tenant schema with all required tables.
        
        Uses the engine directly for DDL operations to avoid transaction issues.
        """
        # Validate schema name to prevent SQL injection
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema_name):
            raise ValueError(f"Invalid schema name: {schema_name}")
        
        async with engine.begin() as conn:
            # Create schema
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
            
            # Set search_path and create tables
            await conn.execute(text(f"SET search_path TO {schema_name}"))
            
            # Create all tables defined in Base.metadata
            # Note: This creates tables for ALL models. In production, you may want
            # to filter to only tenant-specific models.
            await conn.run_sync(Base.metadata.create_all)
            
            # Reset search_path
            await conn.execute(text("SET search_path TO public"))
        
        logger.info(f"Provisioned schema '{schema_name}' with tables")
    
    async def _drop_schema(self, schema_name: str):
        """Drop a schema (used for rollback on failure)."""
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema_name):
            return  # Don't attempt to drop invalid schema names
        
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            logger.info(f"Dropped schema '{schema_name}'")
        except Exception as e:
            logger.error(f"Failed to drop schema '{schema_name}': {e}")
