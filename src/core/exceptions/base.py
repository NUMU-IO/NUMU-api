"""Core domain exceptions."""


class DomainException(Exception):
    """Base exception for domain errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(self.message)


class EntityNotFoundError(DomainException):
    """Raised when an entity is not found."""

    def __init__(
        self,
        entity_name: str,
        entity_id: str | None = None,
        identifier_name: str = "id",
    ) -> None:
        message = f"{entity_name} not found"
        if entity_id:
            message = f"{entity_name} with {identifier_name} '{entity_id}' not found"
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


class PlanLimitExceededError(DomainException):
    """Raised when an action would exceed the tenant's plan limits."""

    def __init__(
        self,
        resource: str,
        limit: int,
        current: int,
        plan: str,
        upgrade_to: str | None = None,
        *,
        feature: str | None = None,
        resets_at: str | None = None,
        available_via: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.resource = resource
        self.limit = limit
        self.current = current
        self.plan = plan
        self.feature = feature or resource
        self.resets_at = resets_at
        self.available_via = list(available_via)
        self.upgrade_to = upgrade_to or next(iter(self.available_via), None)
        message = (
            f"Plan limit reached: your {plan} plan allows {limit} {resource} "
            f"(currently at {current}). Upgrade to continue."
        )
        super().__init__(message, code="PLAN_LIMIT_EXCEEDED")


class FeatureNotAvailableError(DomainException):
    """Not entitled: the plan lacks it, an add-on lapsed, or an override blocks it."""

    def __init__(
        self, feature: str, *, reason: str | None, available_via: list[str]
    ) -> None:
        self.feature = feature
        self.reason = reason
        self.available_via = available_via
        self.upgrade_required = bool(available_via)
        super().__init__(
            f"Your plan does not include {feature}.", code="FEATURE_NOT_AVAILABLE"
        )


class FeatureDisabledError(DomainException):
    """Switched off platform-wide by the kill switch. Not the merchant's doing."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(
            f"{feature} is temporarily unavailable.",
            code="FEATURE_TEMPORARILY_DISABLED",
        )


class FeatureNotReleasedError(DomainException):
    """The release flag is off for this tenant: behave as if the route is absent."""

    def __init__(self, flag: str) -> None:
        self.flag = flag
        super().__init__("Not found", code="FEATURE_NOT_RELEASED")


class ExternalServiceError(DomainException):
    """Raised when an external service fails."""

    def __init__(self, service_name: str, message: str) -> None:
        full_message = f"{service_name} error: {message}"
        super().__init__(full_message, code="EXTERNAL_SERVICE_ERROR")
