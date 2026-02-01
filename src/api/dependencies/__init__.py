"""API dependencies module."""

from src.api.dependencies.auth import (
    get_current_store,
    get_current_user_id,
    get_current_user_role,
    require_admin,
    require_roles,
    require_store_owner,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_customer_address_repository,
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_store_repository,
    get_user_repository,
)
from src.api.dependencies.services import (
    get_ai_service,
    get_email_service,
    get_password_service,
    get_payment_service,
    get_storage_service,
    get_token_service,
)

__all__ = [
    # Database
    "get_db",
    # Auth
    "get_current_store",
    "get_current_user_id",
    "get_current_user_role",
    "require_roles",
    "require_store_owner",
    "require_admin",
    # Repositories
    "get_user_repository",
    "get_store_repository",
    "get_product_repository",
    "get_customer_repository",
    "get_customer_address_repository",
    "get_order_repository",
    # Services
    "get_password_service",
    "get_token_service",
    "get_email_service",
    "get_payment_service",
    "get_storage_service",
    "get_ai_service",
]

