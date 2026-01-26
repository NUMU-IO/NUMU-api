"""Public API routes (legacy module).

NOTE: This module is deprecated. Routes have been reorganized:
- Auth routes -> /api/v1/routes/auth.py
- Health routes -> /api/v1/routes/health.py
- Tenant routes -> /api/v1/routes/tenants.py
- Customer routes -> /api/v1/routes/storefront/
"""

# This module is kept for backwards compatibility but routes have moved
# to the new structure in the parent routes module.

__all__: list[str] = []
