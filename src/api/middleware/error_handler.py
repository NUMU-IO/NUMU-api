"""Error handling middleware.

Provides a global error handler and per-exception-type handlers that
return safe, structured JSON responses. In production, internal details
are suppressed to prevent information disclosure (OWASP A01/A09).
"""

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import settings
from src.core.exceptions import (
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


async def error_handler_middleware(request: Request, call_next: Callable):
    """Global error handling middleware."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "An unexpected error occurred",
                "error_code": "INTERNAL_SERVER_ERROR",
            },
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers for the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        """Override FastAPI default to prevent verbose field-level detail leak."""
        if settings.debug:
            # In debug, return full details for developer convenience
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "success": False,
                    "message": "Request validation failed",
                    "error_code": "VALIDATION_ERROR",
                    "details": exc.errors(),
                },
            )
        # Production: generic message, no field-level detail
        logger.warning("Request validation failed: %s", exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "message": "Request validation failed",
                "error_code": "VALIDATION_ERROR",
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Override default HTTPException handler to ensure consistent format."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "message": str(exc.detail),
                "error_code": "HTTP_ERROR",
            },
        )

    @app.exception_handler(EntityNotFoundError)
    async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "ENTITY_NOT_FOUND",
            },
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "VALIDATION_ERROR",
            },
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "AUTHENTICATION_ERROR",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(TokenExpiredError)
    async def token_expired_handler(request: Request, exc: TokenExpiredError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "TOKEN_EXPIRED",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidTokenError)
    async def invalid_token_handler(request: Request, exc: InvalidTokenError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "INVALID_TOKEN",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(request: Request, exc: AuthorizationError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "AUTHORIZATION_ERROR",
            },
        )

    @app.exception_handler(PaymentError)
    async def payment_error_handler(request: Request, exc: PaymentError):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "PAYMENT_ERROR",
            },
        )

    @app.exception_handler(ExternalServiceError)
    async def storage_error_handler(request: Request, exc: ExternalServiceError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "External service operation failed",
                "error_code": "EXTERNAL_SERVICE_ERROR",
            },
        )

    @app.exception_handler(DomainException)
    async def domain_error_handler(request: Request, exc: DomainException):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "DOMAIN_ERROR",
            },
        )
