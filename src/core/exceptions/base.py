"""Core domain exceptions."""


class DomainException(Exception):
    """Base exception for domain errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(self.message)


class EntityNotFoundError(DomainException):
    """Raised when an entity is not found."""

    def __init__(self, entity_name: str, entity_id: str | None = None) -> None:
        message = f"{entity_name} not found"
        if entity_id:
            message = f"{entity_name} with id '{entity_id}' not found"
        super().__init__(message, code="ENTITY_NOT_FOUND")


class EntityAlreadyExistsError(DomainException):
    """Raised when trying to create an entity that already exists."""

    def __init__(self, entity_name: str, field: str, value: str) -> None:
        message = f"{entity_name} with {field} '{value}' already exists"
        super().__init__(message, code="ENTITY_ALREADY_EXISTS")


class ValidationError(DomainException):
    """Raised when validation fails."""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message, code="VALIDATION_ERROR")


class AuthenticationError(DomainException):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message, code="AUTHENTICATION_ERROR")


class AuthorizationError(DomainException):
    """Raised when user is not authorized to perform an action."""

    def __init__(self, message: str = "Not authorized to perform this action") -> None:
        super().__init__(message, code="AUTHORIZATION_ERROR")


class InvalidCredentialsError(AuthenticationError):
    """Raised when credentials are invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password")


class TokenExpiredError(AuthenticationError):
    """Raised when a token has expired."""

    def __init__(self) -> None:
        super().__init__("Token has expired")


class InvalidTokenError(AuthenticationError):
    """Raised when a token is invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid token")


class AccountLockedError(AuthenticationError):
    """Raised when an account is temporarily locked after too many failed logins."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Account temporarily locked due to too many failed attempts. "
            f"Try again in {retry_after} seconds."
        )


class InsufficientStockError(DomainException):
    """Raised when there is insufficient stock."""

    def __init__(self, product_name: str, available: int, requested: int) -> None:
        message = f"Insufficient stock for '{product_name}': {available} available, {requested} requested"
        super().__init__(message, code="INSUFFICIENT_STOCK")


class PaymentError(DomainException):
    """Raised when a payment operation fails."""

    def __init__(self, message: str = "Payment failed") -> None:
        super().__init__(message, code="PAYMENT_ERROR")


class BusinessRuleViolationError(DomainException):
    """Raised when a business rule is violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="BUSINESS_RULE_VIOLATION")


class ExternalServiceError(DomainException):
    """Raised when an external service fails."""

    def __init__(self, service_name: str, message: str) -> None:
        full_message = f"{service_name} error: {message}"
        super().__init__(full_message, code="EXTERNAL_SERVICE_ERROR")
