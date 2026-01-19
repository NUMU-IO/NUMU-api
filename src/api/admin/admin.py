"""SQLAdmin setup and configuration."""

from fastapi import FastAPI
from sqladmin import Admin

from src.config import settings
from src.infrastructure.database.connection import engine
from src.api.admin.auth import AdminAuth
from src.api.admin.views import TenantAdmin, UserAdmin


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
        authentication_backend=AdminAuth(secret_key=settings.jwt_secret_key),
        title="NUMU Admin",
        base_url="/admin",
    )
    
    # Register model views (public schema only)
    admin.add_view(TenantAdmin)
    admin.add_view(UserAdmin)
    
    return admin
