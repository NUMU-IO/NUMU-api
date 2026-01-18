"""Admin model views for SQLAdmin."""

from sqladmin import ModelView

from src.infrastructure.database.models import Tenant, UserModel


class TenantAdmin(ModelView, model=Tenant):
    """Admin view for Tenant model."""
    
    column_list = [
        Tenant.id,
        Tenant.name,
        Tenant.subdomain,
        Tenant.schema_name,
        Tenant.plan,
        Tenant.is_active,
        Tenant.created_at,
    ]
    column_searchable_list = [Tenant.name, Tenant.subdomain]
    column_sortable_list = [Tenant.name, Tenant.subdomain, Tenant.created_at, Tenant.is_active]
    column_default_sort = ("created_at", True)  # Descending
    
    # Form configuration
    form_excluded_columns = [Tenant.created_at, Tenant.updated_at]
    
    # Display settings
    name = "Tenant"
    name_plural = "Tenants"
    icon = "fa-solid fa-building"
    
    # Permissions
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True


class UserAdmin(ModelView, model=UserModel):
    """Admin view for User model."""
    
    column_list = [
        UserModel.id,
        UserModel.email,
        UserModel.first_name,
        UserModel.last_name,
        UserModel.role,
        UserModel.status,
        UserModel.created_at,
    ]
    column_searchable_list = [UserModel.email, UserModel.first_name, UserModel.last_name]
    column_sortable_list = [UserModel.email, UserModel.created_at, UserModel.role, UserModel.status]
    column_default_sort = ("created_at", True)  # Descending
    
    # Hide password in forms (column_list already excludes it from list view)
    form_excluded_columns = [
        UserModel.hashed_password,
        UserModel.created_at,
        UserModel.updated_at,
    ]
    
    # Display settings
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"
    
    # Permissions
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
