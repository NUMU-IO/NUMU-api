"""Test fixtures for configuration-related tests.

This module provides reusable fixtures for testing:
- Payment gateway validators
- Shipping carrier validators
- Communication service validators
- Configure credentials use case
"""

import pytest
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession


# =============================================================================
# UUID Fixtures
# =============================================================================

@pytest.fixture
def tenant_id() -> UUID:
    """Generate a consistent tenant ID for tests."""
    return UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def admin_id() -> UUID:
    """Generate a consistent admin ID for tests."""
    return UUID("87654321-4321-8765-4321-876543218765")


@pytest.fixture
def request_id() -> UUID:
    """Generate a consistent request ID for tests."""
    return UUID("11111111-2222-3333-4444-555555555555")


# =============================================================================
# Payment Gateway Credential Fixtures
# =============================================================================

@pytest.fixture
def valid_fawry_credentials() -> dict[str, Any]:
    """Valid Fawry payment gateway credentials."""
    return {
        "merchant_code": "FWY123456789",
        "security_key": "sk_test_fawry_abcdef123456789",
        "environment": "sandbox",
    }


@pytest.fixture
def invalid_fawry_credentials() -> dict[str, Any]:
    """Invalid Fawry credentials (missing required field)."""
    return {
        "merchant_code": "FWY123456789",
        # Missing security_key
        "environment": "sandbox",
    }


@pytest.fixture
def valid_paymob_credentials() -> dict[str, Any]:
    """Valid Paymob payment gateway credentials."""
    return {
        "api_key": "ZXlKaGJHY2lPaUpJVXpVeE1pSXNJblI1Y0NJNklrcFhWQ0o5",
        "integration_id": "123456",
        "iframe_id": "789012",
        "hmac_secret": "hmac_secret_key_12345",
        "environment": "sandbox",
    }


@pytest.fixture
def invalid_paymob_credentials() -> dict[str, Any]:
    """Invalid Paymob credentials (invalid API key format)."""
    return {
        "api_key": "invalid_key",  # Should be base64 encoded
        "integration_id": "123456",
        "iframe_id": "789012",
        "environment": "sandbox",
    }


@pytest.fixture
def valid_vodafone_cash_credentials() -> dict[str, Any]:
    """Valid Vodafone Cash credentials."""
    return {
        "merchant_id": "VF123456",
        "api_key": "vf_api_key_abcdef123456",
        "pin": "1234",
        "environment": "sandbox",
    }


@pytest.fixture
def valid_stripe_credentials() -> dict[str, Any]:
    """Valid Stripe credentials."""
    return {
        "secret_key": "sk_test_51ABC123DEF456GHI789JKL",
        "publishable_key": "pk_test_51ABC123DEF456GHI789JKL",
        "webhook_secret": "whsec_abcdef123456789",
        "environment": "sandbox",
    }


@pytest.fixture
def invalid_stripe_credentials() -> dict[str, Any]:
    """Invalid Stripe credentials (wrong key format)."""
    return {
        "secret_key": "invalid_key",  # Should start with sk_
        "publishable_key": "pk_test_51ABC123DEF456GHI789JKL",
        "environment": "sandbox",
    }


@pytest.fixture
def valid_tap_credentials() -> dict[str, Any]:
    """Valid Tap Payments credentials."""
    return {
        "secret_key": "sk_test_XYZ123abc456def789",
        "public_key": "pk_test_XYZ123abc456def789",
        "merchant_id": "TAP123456",
        "environment": "sandbox",
    }


# =============================================================================
# Database Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def mock_configuration_request(tenant_id: UUID, request_id: UUID):
    """Create a mock ConfigurationRequest object."""
    from src.infrastructure.database.models.tenant.configuration import (
        ConfigurationRequest,
        ServiceType,
        ServiceName,
        RequestStatus,
        RequestPriority,
    )
    
    request = MagicMock(spec=ConfigurationRequest)
    request.id = request_id
    request.tenant_id = tenant_id
    request.service_type = ServiceType.PAYMENT_GATEWAY
    request.service_name = ServiceName.FAWRY
    request.status = RequestStatus.PENDING
    request.priority = RequestPriority.NORMAL
    request.created_at = datetime.utcnow()
    request.updated_at = datetime.utcnow()
    request.completed_at = None
    request.admin_notes = None
    return request


@pytest.fixture
def mock_service_credential(tenant_id: UUID, admin_id: UUID):
    """Create a mock ServiceCredential object."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceType,
        ServiceName,
    )
    
    credential = MagicMock(spec=ServiceCredential)
    credential.id = uuid4()
    credential.tenant_id = tenant_id
    credential.service_type = ServiceType.PAYMENT_GATEWAY
    credential.service_name = ServiceName.FAWRY
    credential.encrypted_credentials = b"encrypted_data"
    credential.is_validated = True
    credential.is_active = True
    credential.last_validated_at = datetime.utcnow()
    credential.configured_by = admin_id
    credential.created_at = datetime.utcnow()
    credential.updated_at = datetime.utcnow()
    credential.metadata = {"display_info": {"merchant_code": "FWY***789"}}
    return credential


# =============================================================================
# Validator Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_validation_result_success():
    """Create a successful validation result."""
    from src.infrastructure.external_services.gateway_validators.base import (
        ValidationResult,
        ValidationStatus,
    )
    
    return ValidationResult(
        is_valid=True,
        status=ValidationStatus.VALID,
        message="Credentials validated successfully",
        details={"account_status": "active", "merchant_name": "Test Merchant"},
    )


@pytest.fixture
def mock_validation_result_failure():
    """Create a failed validation result."""
    from src.infrastructure.external_services.gateway_validators.base import (
        ValidationResult,
        ValidationStatus,
    )
    
    return ValidationResult(
        is_valid=False,
        status=ValidationStatus.INVALID,
        message="Invalid API key",
        details={"error_code": "AUTH_001"},
    )


@pytest.fixture
def mock_validator_factory(mock_validation_result_success):
    """Create a mock validator factory."""
    from src.infrastructure.external_services.gateway_validators import (
        GatewayValidatorFactory,
    )
    
    factory = MagicMock(spec=GatewayValidatorFactory)
    
    mock_validator = AsyncMock()
    mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
    mock_validator.get_display_info = MagicMock(return_value={"merchant_code": "FWY***789"})
    
    factory.get_validator = MagicMock(return_value=mock_validator)
    factory.is_supported = MagicMock(return_value=True)
    
    return factory


# =============================================================================
# Secrets Manager Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_secrets_manager():
    """Create a mock secrets manager."""
    from src.infrastructure.external_services.secrets import SecretsManager
    
    manager = MagicMock(spec=SecretsManager)
    manager.encrypt_credentials = MagicMock(return_value=b"encrypted_credentials_data")
    manager.decrypt_credentials = MagicMock(return_value={"merchant_code": "FWY123456789"})
    
    return manager


# =============================================================================
# HTTP Response Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_httpx_response_success():
    """Create a successful HTTP response mock."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "status": "success",
        "data": {"merchant_name": "Test Merchant", "account_status": "active"},
    }
    return response


@pytest.fixture
def mock_httpx_response_unauthorized():
    """Create an unauthorized HTTP response mock."""
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {
        "status": "error",
        "message": "Invalid API key",
    }
    return response


@pytest.fixture
def mock_httpx_response_error():
    """Create an error HTTP response mock."""
    response = MagicMock()
    response.status_code = 500
    response.json.return_value = {
        "status": "error",
        "message": "Internal server error",
    }
    return response
