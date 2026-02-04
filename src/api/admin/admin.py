"""SQLAdmin setup and configuration."""

from fastapi import FastAPI
from sqladmin import Admin

from src.api.admin.auth import AdminAuth
from src.api.admin.views import (
    CategoryAdmin,
    CustomerAdmin,
    OrderAdmin,
    ProductAdmin,
    StoreAdmin,
    TenantAdmin,
    UserAdmin,
)
from src.config import settings
from src.infrastructure.database.connection import engine


def setup_admin(app: FastAPI) -> Admin:
    """Setup SQLAdmin with the FastAPI application.

    Args:
        app: The FastAPI application instance.

    Returns:
        The configured Admin instance.
    """
    # Create admin instance with authentication
    admin = Admin(
        app=app,
        engine=engine,
        authentication_backend=AdminAuth(secret_key=settings.session_secret_key),
        title="NUMU Admin",
        base_url="/admin",
    )

    # Register model views
    admin.add_view(TenantAdmin)
    admin.add_view(UserAdmin)
    admin.add_view(StoreAdmin)
    admin.add_view(CategoryAdmin)
    admin.add_view(ProductAdmin)
    admin.add_view(CustomerAdmin)
    admin.add_view(OrderAdmin)

    return admin
