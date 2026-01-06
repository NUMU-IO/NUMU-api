"""Error handling middleware."""

import logging
from typing import Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    DomainError,
    EntityNotFoundError,
    InvalidTokenError,
    PaymentError,
    StorageError,
    TokenExpiredError,
    ValidationError,
)

logger = logging.getLogger(__name__)


async def error_handler_middleware(request: Request, call_next: Callable):
    """Global error handling middleware."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception(f"Unhandled error: {e}")
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

    @app.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "Storage operation failed",
                "error_code": "STORAGE_ERROR",
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "message": str(exc),
                "error_code": "DOMAIN_ERROR",
            },
        )
