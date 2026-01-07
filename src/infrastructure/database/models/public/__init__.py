"""Public schema database models.

These models live in the 'public' PostgreSQL schema and are shared across all tenants.
They include:
- Tenant: The tenant registry table
- UserModel: User accounts (if using global SSO)
"""

from src.infrastructure.database.models.public.tenant import Tenant
from src.infrastructure.database.models.public.user import UserModel

__all__ = [
    "Tenant",
    "UserModel",
]
