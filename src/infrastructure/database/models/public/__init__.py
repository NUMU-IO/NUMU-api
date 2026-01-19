"""Public schema database models.

These models live in the 'public' PostgreSQL schema and are shared across all tenants.
They include:
- TenantModel: The tenant registry table
- UserModel: User accounts (global SSO)
"""

from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel

__all__ = [
    "TenantModel",
    "UserModel",
]
