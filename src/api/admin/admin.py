"""SQLAdmin setup and configuration."""

from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin

from src.config import settings
from src.infrastructure.database.connection import engine
from src.api.admin.auth import AdminAuth
from src.api.admin.views import (
    TenantAdmin,
    UserAdmin,
    StoreAdmin,
    CategoryAdmin,
    ProductAdmin,
    CustomerAdmin,
    OrderAdmin,
)

# Path to custom templates
TEMPLATES_DIR = Path(__file__).parent / "templates"


def setup_admin(app: FastAPI) -> Admin:
    """Setup SQLAdmin with the FastAPI application.
    
    Args:
        app: The FastAPI application instance.
        
    Returns:
        The configured Admin instance.
    """
    # Create admin instance with authentication and custom templates
    admin = Admin(
        app=app,
        engine=engine,
        authentication_backend=AdminAuth(secret_key=settings.jwt_secret_key),
        title="NUMU Admin",
        base_url="/admin",
        templates_dir=str(TEMPLATES_DIR),
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
