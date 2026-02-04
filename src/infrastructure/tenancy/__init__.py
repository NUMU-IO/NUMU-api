"""Tenant management infrastructure.

This module handles multi-tenancy at the infrastructure layer:
- Tenant repository for database operations
- Tenant service for business logic
- Schema provisioning and management
- Row-Level Security (RLS) helpers for database-level isolation
"""

from src.infrastructure.tenancy.repository import TenantRepository
from src.infrastructure.tenancy.rls import (
    RLSBypassContext,
    RLSContext,
    clear_tenant_context,
    disable_rls_bypass,
    enable_rls_bypass,
    get_current_tenant_context,
    is_rls_bypassed,
    set_tenant_context,
)
from src.infrastructure.tenancy.service import TenantService

__all__ = [
    "TenantRepository",
    "TenantService",
    # RLS helpers
    "set_tenant_context",
    "clear_tenant_context",
    "get_current_tenant_context",
    "enable_rls_bypass",
    "disable_rls_bypass",
    "is_rls_bypassed",
    "RLSContext",
    "RLSBypassContext",
]
