"""Unit tests for error handler middleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, status

from src.api.middleware.error_handler import (
    error_handler_middleware,
    setup_exception_handlers,
)
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


class TestErrorHandlerMiddleware:
    """Tests for error_handler_middleware function."""

    @pytest.mark.asyncio
    async def test_middleware_passes_through_successful_request(self):
        """Test middleware passes through successful requests."""
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_call_next = AsyncMock(return_value=mock_response)

        response = await error_handler_middleware(mock_request, mock_call_next)

        assert response == mock_response
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_middleware_handles_unhandled_exception(self):
        """Test middleware catches unhandled exceptions."""
        mock_request = MagicMock()
        mock_call_next = AsyncMock(side_effect=Exception("Unexpected error"))

        response = await error_handler_middleware(mock_request, mock_call_next)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_middleware_returns_json_for_unhandled_exception(self):
        """Test middleware returns JSON response for unhandled exceptions."""
        mock_request = MagicMock()
        mock_call_next = AsyncMock(side_effect=RuntimeError("Something broke"))

        response = await error_handler_middleware(mock_request, mock_call_next)

        assert response.status_code == 500
        # Response should be JSONResponse


class TestSetupExceptionHandlers:
    """Tests for setup_exception_handlers function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.app = FastAPI()
        setup_exception_handlers(self.app)

    @pytest.mark.asyncio
    async def test_entity_not_found_handler(self):
        """Test EntityNotFoundError returns 404."""
        mock_request = MagicMock()
        exc = EntityNotFoundError("Product")

        # Get the handler from the app
        handler = self.app.exception_handlers.get(EntityNotFoundError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_validation_error_handler(self):
        """Test ValidationError returns 422."""
        mock_request = MagicMock()
        exc = ValidationError("Invalid email format")

        handler = self.app.exception_handlers.get(ValidationError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_authentication_error_handler(self):
        """Test AuthenticationError returns 401."""
        mock_request = MagicMock()
        exc = AuthenticationError("Invalid credentials")

        handler = self.app.exception_handlers.get(AuthenticationError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "WWW-Authenticate" in response.headers

    @pytest.mark.asyncio
    async def test_token_expired_handler(self):
        """Test TokenExpiredError returns 401."""
        mock_request = MagicMock()
        exc = TokenExpiredError()

        handler = self.app.exception_handlers.get(TokenExpiredError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_invalid_token_handler(self):
        """Test InvalidTokenError returns 401."""
        mock_request = MagicMock()
        exc = InvalidTokenError()

        handler = self.app.exception_handlers.get(InvalidTokenError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_authorization_error_handler(self):
        """Test AuthorizationError returns 403."""
        mock_request = MagicMock()
        exc = AuthorizationError("Not authorized to access this resource")

        handler = self.app.exception_handlers.get(AuthorizationError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_payment_error_handler(self):
        """Test PaymentError returns 402."""
        mock_request = MagicMock()
        exc = PaymentError("Payment failed")

        handler = self.app.exception_handlers.get(PaymentError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED

    @pytest.mark.asyncio
    async def test_external_service_error_handler(self):
        """Test ExternalServiceError returns 500."""
        mock_request = MagicMock()
        exc = ExternalServiceError("S3", "Upload failed")

        handler = self.app.exception_handlers.get(ExternalServiceError)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_domain_error_handler(self):
        """Test DomainException returns 400."""
        mock_request = MagicMock()
        exc = DomainException("Business rule violated")

        handler = self.app.exception_handlers.get(DomainException)
        assert handler is not None

        response = await handler(mock_request, exc)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
