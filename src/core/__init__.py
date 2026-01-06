"""Core domain layer module.

This module contains the core domain logic including:
- Entities: Domain objects with identity
- Value Objects: Immutable objects defined by their attributes
- Interfaces: Abstract definitions for repositories and services
- Exceptions: Domain-specific exceptions
"""

from src.core.entities import (
    BaseEntity,
    Category,
    Customer,
    FulfillmentStatus,
    Order,
    OrderLineItem,
    OrderStatus,
    PaymentStatus,
    Product,
    ProductStatus,
    ProductType,
    ShippingAddress,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    DomainException,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ExternalServiceError,
    InsufficientStockError,
    InvalidCredentialsError,
    InvalidTokenError,
    PaymentError,
    TokenExpiredError,
    ValidationError,
)
from src.core.value_objects import Address, Currency, Email, Money, PhoneNumber

__all__ = [
    # Entities
    "BaseEntity",
    "User",
    "UserRole",
    "UserStatus",
    "Store",
    "StoreStatus",
    "Product",
    "ProductStatus",
    "ProductType",
    "Category",
    "Customer",
    "Order",
    "OrderStatus",
    "PaymentStatus",
    "FulfillmentStatus",
    "OrderLineItem",
    "ShippingAddress",
    # Value Objects
    "Email",
    "PhoneNumber",
    "Money",
    "Currency",
    "Address",
    # Exceptions
    "DomainException",
    "EntityNotFoundError",
    "EntityAlreadyExistsError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "InvalidCredentialsError",
    "TokenExpiredError",
    "InvalidTokenError",
    "InsufficientStockError",
    "PaymentError",
    "ExternalServiceError",
]
