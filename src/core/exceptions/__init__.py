"""Core exceptions module."""

from src.core.exceptions.base import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleViolationError,
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
    "BusinessRuleViolationError",
    "InvalidCredentialsError",
    "TokenExpiredError",
    "InvalidTokenError",
    "InsufficientStockError",
    "PaymentError",
    "ExternalServiceError",
]
