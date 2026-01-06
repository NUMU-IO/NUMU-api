"""Core exceptions module."""

from src.core.exceptions.base import (
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

__all__ = [
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
