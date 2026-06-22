"""Admin model views for SQLAdmin."""

from enum import Enum as _Enum
from typing import Any

from sqladmin import ModelView
from sqladmin.filters import (
    AllUniqueStringValuesFilter,
    BooleanFilter,
    OperationColumnFilter,
    StaticValuesFilter,
)
from wtforms import PasswordField, validators

from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models import (
    CategoryModel,
    CustomerModel,
    OrderModel,
    ProductModel,
    StoreModel,
    TenantModel,
    UserModel,
)
from src.infrastructure.external_services.password_service import password_service


def _enum_choices(enum_cls: type[_Enum]) -> list[tuple[str, str]]:
    """Build ``StaticValuesFilter`` choices for a status enum.

    The option *value* is ``member.value`` (e.g. ``"pending"``). These columns
    are mapped as ``Enum(Status)`` without ``values_callable``, so the DB stores
    member **names** (``"PENDING"``); SQLAlchemy's Enum bind processor maps the
    value back to the stored name on comparison, so ``value`` is the correct
    thing to round-trip through the querystring.
    """
    return [
        (member.value, member.name.replace("_", " ").title()) for member in enum_cls
    ]


class TenantAdmin(ModelView, model=TenantModel):
    """Admin view for Tenant model."""

    column_list = [
        TenantModel.id,
        TenantModel.name,
        TenantModel.subdomain,
        TenantModel.plan,
        TenantModel.is_active,
        TenantModel.created_at,
    ]
    column_searchable_list = [TenantModel.name, TenantModel.subdomain]
    column_sortable_list = [
        TenantModel.name,
        TenantModel.subdomain,
        TenantModel.created_at,
        TenantModel.is_active,
    ]
    column_default_sort = ("created_at", True)
    form_excluded_columns = [TenantModel.created_at, TenantModel.updated_at]
    column_filters = [
        BooleanFilter(TenantModel.is_active),
        AllUniqueStringValuesFilter(TenantModel.plan),
    ]
    name = "Tenant"
    name_plural = "Tenants"
    category = "Identity"
    icon = "fa-solid fa-building"


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
    column_default_sort = ("created_at", True)
    form_excluded_columns = [
        UserModel.hashed_password,
        UserModel.created_at,
        UserModel.updated_at,
    ]
    column_filters = [
        AllUniqueStringValuesFilter(UserModel.role),
        AllUniqueStringValuesFilter(UserModel.status),
    ]
    name = "User"
    name_plural = "Users"
    category = "Identity"
    icon = "fa-solid fa-user"

    form_args = {
        "email": {"validators": [validators.Email(), validators.DataRequired()]},
        "first_name": {"validators": [validators.DataRequired()]},
        "last_name": {"validators": [validators.DataRequired()]},
    }

    form_extra_fields = {
        "password": PasswordField(
            "Password",
            validators=[
                validators.Optional(),
                validators.Length(
                    min=8, message="Password must be at least 8 characters"
                ),
            ],
            description="Leave blank to keep existing password",
        ),
        "confirm_password": PasswordField(
            "Confirm Password",
            validators=[
                validators.Optional(),
                validators.EqualTo("password", message="Passwords must match"),
            ],
        ),
    }

    async def on_model_change(
        self, data: dict[str, Any], model: UserModel, is_created: bool, request: Any
    ) -> None:
        password = data.get("password")
        if is_created and not password:
            raise ValueError("Password is required when creating a new user")
        if password:
            model.hashed_password = password_service.hash_password(password)
        data.pop("password", None)
        data.pop("confirm_password", None)


class StoreAdmin(ModelView, model=StoreModel):
    """Admin view for Store model."""

    column_list = [
        StoreModel.id,
        StoreModel.name,
        StoreModel.slug,
        StoreModel.status,
        StoreModel.default_currency,
        StoreModel.contact_email,
        StoreModel.created_at,
    ]
    column_searchable_list = [
        StoreModel.name,
        StoreModel.slug,
        StoreModel.contact_email,
    ]
    column_sortable_list = [StoreModel.name, StoreModel.created_at, StoreModel.status]
    column_default_sort = ("created_at", True)
    form_excluded_columns = [StoreModel.created_at, StoreModel.updated_at]
    column_filters = [
        AllUniqueStringValuesFilter(StoreModel.status),
        AllUniqueStringValuesFilter(StoreModel.default_currency),
    ]
    name = "Store"
    name_plural = "Stores"
    category = "Catalog"
    icon = "fa-solid fa-store"


class CategoryAdmin(ModelView, model=CategoryModel):
    """Admin view for Category model."""

    column_list = [
        CategoryModel.id,
        CategoryModel.name,
        CategoryModel.slug,
        CategoryModel.is_active,
        CategoryModel.position,
        CategoryModel.created_at,
    ]
    column_searchable_list = [CategoryModel.name, CategoryModel.slug]
    column_sortable_list = [
        CategoryModel.name,
        CategoryModel.position,
        CategoryModel.created_at,
    ]
    column_default_sort = ("position", False)
    form_excluded_columns = [CategoryModel.created_at, CategoryModel.updated_at]
    column_filters = [BooleanFilter(CategoryModel.is_active)]
    name = "Category"
    name_plural = "Categories"
    category = "Catalog"
    icon = "fa-solid fa-folder"


class ProductAdmin(ModelView, model=ProductModel):
    """Admin view for Product model."""

    column_list = [
        ProductModel.id,
        ProductModel.name,
        ProductModel.slug,
        ProductModel.sku,
        ProductModel.price_amount,
        ProductModel.quantity,
        ProductModel.status,
        ProductModel.created_at,
    ]
    column_searchable_list = [ProductModel.name, ProductModel.slug, ProductModel.sku]
    column_sortable_list = [
        ProductModel.name,
        ProductModel.price_amount,
        ProductModel.quantity,
        ProductModel.created_at,
    ]
    column_default_sort = ("created_at", True)
    form_excluded_columns = [ProductModel.created_at, ProductModel.updated_at]
    column_filters = [
        StaticValuesFilter(ProductModel.status, values=_enum_choices(ProductStatus)),
        OperationColumnFilter(ProductModel.quantity),  # e.g. quantity < 5 (low stock)
        OperationColumnFilter(ProductModel.price_amount),  # minor units
    ]
    name = "Product"
    name_plural = "Products"
    category = "Catalog"
    icon = "fa-solid fa-box"


class CustomerAdmin(ModelView, model=CustomerModel):
    """Admin view for Customer model."""

    column_list = [
        CustomerModel.id,
        CustomerModel.email,
        CustomerModel.first_name,
        CustomerModel.last_name,
        CustomerModel.total_orders,
        CustomerModel.total_spent,
        CustomerModel.created_at,
    ]
    column_searchable_list = [
        CustomerModel.email,
        CustomerModel.first_name,
        CustomerModel.last_name,
    ]
    column_sortable_list = [
        CustomerModel.email,
        CustomerModel.total_orders,
        CustomerModel.total_spent,
        CustomerModel.created_at,
    ]
    column_default_sort = ("created_at", True)
    form_excluded_columns = [CustomerModel.created_at, CustomerModel.updated_at]
    column_filters = [
        OperationColumnFilter(CustomerModel.total_spent),  # minor units (VIPs)
        OperationColumnFilter(CustomerModel.total_orders),
    ]
    name = "Customer"
    name_plural = "Customers"
    category = "Commerce"
    icon = "fa-solid fa-users"


class OrderAdmin(ModelView, model=OrderModel):
    """Admin view for Order model."""

    column_list = [
        OrderModel.id,
        OrderModel.order_number,
        OrderModel.status,
        OrderModel.payment_status,
        OrderModel.fulfillment_status,
        OrderModel.total,
        OrderModel.currency,
        OrderModel.created_at,
    ]
    column_searchable_list = [OrderModel.order_number]
    column_sortable_list = [
        OrderModel.order_number,
        OrderModel.total,
        OrderModel.status,
        OrderModel.created_at,
    ]
    column_default_sort = ("created_at", True)
    form_excluded_columns = [OrderModel.created_at, OrderModel.updated_at]
    column_filters = [
        StaticValuesFilter(OrderModel.status, values=_enum_choices(OrderStatus)),
        StaticValuesFilter(
            OrderModel.payment_status, values=_enum_choices(PaymentStatus)
        ),
        StaticValuesFilter(
            OrderModel.fulfillment_status, values=_enum_choices(FulfillmentStatus)
        ),
        OperationColumnFilter(OrderModel.total),  # minor units
        AllUniqueStringValuesFilter(OrderModel.currency),
    ]
    name = "Order"
    name_plural = "Orders"
    category = "Commerce"
    icon = "fa-solid fa-shopping-cart"
