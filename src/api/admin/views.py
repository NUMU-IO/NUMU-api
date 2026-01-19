"""Admin model views for SQLAdmin."""

from typing import Any

from sqladmin import ModelView
from wtforms import PasswordField, validators

from src.infrastructure.database.models import Tenant, UserModel
from src.infrastructure.external_services.password_service import password_service


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
    column_sortable_list = [
        Tenant.name,
        Tenant.subdomain,
        Tenant.created_at,
        Tenant.is_active,
    ]
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
    column_searchable_list = [
        UserModel.email,
        UserModel.first_name,
        UserModel.last_name,
    ]
    column_sortable_list = [
        UserModel.email,
        UserModel.created_at,
        UserModel.role,
        UserModel.status,
    ]
    column_default_sort = ("created_at", True)  # Descending

    # Hide password and timestamps in forms
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

    # Custom form configuration
    form_args = {
        "email": {"validators": [validators.Email(), validators.DataRequired()]},
        "first_name": {"validators": [validators.DataRequired()]},
        "last_name": {"validators": [validators.DataRequired()]},
    }

    # Add custom password fields
    # Note: validators.Optional() is used because the same form serves both
    # create and edit operations. Required validation for creation is handled
    # in on_model_change() to distinguish between create and update scenarios.
    form_extra_fields = {
        "password": PasswordField(
            "Password",
            validators=[
                validators.Optional(),
                validators.Length(
                    min=8, message="Password must be at least 8 characters"
                ),
            ],
            description="Leave blank to keep existing password (edit) or set a new password",
        ),
        "confirm_password": PasswordField(
            "Confirm Password",
            validators=[
                validators.Optional(),
                validators.EqualTo("password", message="Passwords must match"),
            ],
            description="Re-enter password to confirm",
        ),
    }

    async def on_model_change(
        self, data: dict[str, Any], model: UserModel, is_created: bool, request: Any
    ) -> None:
        """Hash password before saving the user model.

        Args:
            data: Form data dictionary
            model: User model instance
            is_created: True if creating new user, False if updating
            request: The request object
        """
        # Get password from form data
        password = data.get("password")

        # For new users, password is required
        if is_created and not password:
            raise ValueError("Password is required when creating a new user")

        # Hash password if provided
        if password:
            model.hashed_password = password_service.hash_password(password)

        # Remove password fields from data as they're not columns
        data.pop("password", None)
        data.pop("confirm_password", None)
