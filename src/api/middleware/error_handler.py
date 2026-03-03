"""Error handling middleware.

Provides a global error handler and per-exception-type handlers that
return safe, structured JSON responses.  In production, internal details
are suppressed to prevent information disclosure (OWASP A01/A09).

Every error response follows a consistent envelope:
    {
        "success": false,
        "error": {
            "code": "ENTITY_NOT_FOUND",
            "message": "Human-readable message",
            "details": { ... }            // optional, never in production
        }
    }
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import settings
from src.core.exceptions import (
    AccountLockedError,
    AuthenticationError,
    AuthorizationError,
    DomainException,
    EntityNotFoundError,
    ExternalServiceError,
    InvalidTokenError,
    PaymentError,
    TokenExpiredError,
    ValidationError,
)

logger = logging.getLogger(__name__)


# ── Standardised error body builder ──────────────────────────


def _error_body(
    code: str,
    message: str,
    details: Any = None,
) -> dict:
    """Build a consistent error response dict.

    ``details`` is only included when non-None, and in production it is
    always stripped for safety (except for whitelisted codes like
    ACCOUNT_LOCKED where the client needs ``retry_after``).
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}


_DETAILS_ALLOWED_IN_PROD = {"ACCOUNT_LOCKED", "RATE_LIMIT_EXCEEDED"}


def _safe_error_body(
    code: str,
    message: str,
    details: Any = None,
) -> dict:
    """Same as ``_error_body`` but suppresses ``details`` in production
    unless the code is whitelisted."""
    if not settings.debug and code not in _DETAILS_ALLOWED_IN_PROD:
        details = None
    return _error_body(code, message, details)


async def error_handler_middleware(request: Request, call_next: Callable):
    """Global error handling middleware."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "INTERNAL_SERVER_ERROR", "An unexpected error occurred"
            ),
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers for the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        """Override FastAPI default to prevent verbose field-level detail leak."""
        logger.warning("Request validation failed: %s", exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_safe_error_body(
                "VALIDATION_ERROR",
                "Request validation failed",
                exc.errors(),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Override default HTTPException handler to ensure consistent format."""
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(EntityNotFoundError)
    async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_error_body("ENTITY_NOT_FOUND", str(exc)),
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("VALIDATION_ERROR", str(exc)),
        )

    # Must be registered before AuthenticationError so it takes priority
    @app.exception_handler(AccountLockedError)
    async def account_locked_handler(request: Request, exc: AccountLockedError):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_safe_error_body(
                "ACCOUNT_LOCKED",
                str(exc),
                {"retry_after": exc.retry_after},
            ),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("AUTHENTICATION_ERROR", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(TokenExpiredError)
    async def token_expired_handler(request: Request, exc: TokenExpiredError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("TOKEN_EXPIRED", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidTokenError)
    async def invalid_token_handler(request: Request, exc: InvalidTokenError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("INVALID_TOKEN", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(request: Request, exc: AuthorizationError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_error_body("AUTHORIZATION_ERROR", str(exc)),
        )

    @app.exception_handler(PaymentError)
    async def payment_error_handler(request: Request, exc: PaymentError):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content=_error_body("PAYMENT_ERROR", str(exc)),
        )

    @app.exception_handler(ExternalServiceError)
    async def storage_error_handler(request: Request, exc: ExternalServiceError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "EXTERNAL_SERVICE_ERROR", "External service operation failed"
            ),
        )

    @app.exception_handler(DomainException)
    async def domain_error_handler(request: Request, exc: DomainException):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_error_body("DOMAIN_ERROR", str(exc)),
        )
