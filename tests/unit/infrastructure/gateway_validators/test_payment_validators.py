"""Unit tests for payment gateway validators.

This module tests the payment gateway validators including:
- FawryValidator
- PaymobValidator
- VodafoneCashValidator
- StripeValidator
- TapValidator

Each validator is tested for:
- Successful credential validation
- Failed credential validation (invalid credentials)
- Missing required fields
- Network errors
- Display info generation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from src.infrastructure.external_services.gateway_validators.base import (
    ValidationResult,
    ValidationStatus,
)
from src.infrastructure.external_services.gateway_validators.payment_validators import (
    FawryValidator,
    PaymobValidator,
    VodafoneCashValidator,
    StripeValidator,
    TapValidator,
)


# =============================================================================
# FawryValidator Tests
# =============================================================================

class TestFawryValidator:
    """Test suite for FawryValidator."""
    
    @pytest.fixture
    def validator(self) -> FawryValidator:
        """Create a FawryValidator instance."""
        return FawryValidator()
    
    @pytest.mark.asyncio
    async def test_validate_success(
        self,
        validator: FawryValidator,
        valid_fawry_credentials: dict,
        mock_httpx_response_success: MagicMock,
    ):
        """Test successful Fawry credential validation."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_httpx_response_success)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_fawry_credentials)
            
            assert result.is_valid is True
            assert result.status == ValidationStatus.VALID
            assert "success" in result.message.lower() or result.details is not None
    
    @pytest.mark.asyncio
    async def test_validate_invalid_credentials(
        self,
        validator: FawryValidator,
        valid_fawry_credentials: dict,
        mock_httpx_response_unauthorized: MagicMock,
    ):
        """Test Fawry validation with invalid credentials."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_httpx_response_unauthorized)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_fawry_credentials)
            
            assert result.is_valid is False
            assert result.status == ValidationStatus.INVALID
    
    @pytest.mark.asyncio
    async def test_validate_missing_required_fields(
        self,
        validator: FawryValidator,
        invalid_fawry_credentials: dict,
    ):
        """Test Fawry validation with missing required fields."""
        result = await validator.validate(invalid_fawry_credentials)
        
        assert result.is_valid is False
        assert result.status == ValidationStatus.INVALID
        assert "security_key" in result.message.lower() or "required" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_validate_network_error(
        self,
        validator: FawryValidator,
        valid_fawry_credentials: dict,
    ):
        """Test Fawry validation when network error occurs."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_fawry_credentials)
            
            assert result.is_valid is False
            assert result.status in [ValidationStatus.ERROR, ValidationStatus.INVALID]
    
    def test_get_display_info(
        self,
        validator: FawryValidator,
        valid_fawry_credentials: dict,
    ):
        """Test Fawry display info generation."""
        display_info = validator.get_display_info(valid_fawry_credentials)
        
        assert "merchant_code" in display_info
        # Merchant code should be partially masked
        assert "***" in display_info["merchant_code"] or len(display_info["merchant_code"]) < len(valid_fawry_credentials["merchant_code"])
    
    def test_get_required_fields(self, validator: FawryValidator):
        """Test that required fields are correctly defined."""
        required = validator.get_required_fields()
        
        assert "merchant_code" in required
        assert "security_key" in required
    
    def test_get_optional_fields(self, validator: FawryValidator):
        """Test that optional fields are correctly defined."""
        optional = validator.get_optional_fields()
        
        assert "environment" in optional


# =============================================================================
# PaymobValidator Tests
# =============================================================================

class TestPaymobValidator:
    """Test suite for PaymobValidator."""
    
    @pytest.fixture
    def validator(self) -> PaymobValidator:
        """Create a PaymobValidator instance."""
        return PaymobValidator()
    
    @pytest.mark.asyncio
    async def test_validate_success(
        self,
        validator: PaymobValidator,
        valid_paymob_credentials: dict,
        mock_httpx_response_success: MagicMock,
    ):
        """Test successful Paymob credential validation."""
        # Mock the auth token response
        auth_response = MagicMock()
        auth_response.status_code = 201
        auth_response.json.return_value = {"token": "auth_token_12345"}
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=auth_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_paymob_credentials)
            
            assert result.is_valid is True
            assert result.status == ValidationStatus.VALID
    
    @pytest.mark.asyncio
    async def test_validate_invalid_api_key(
        self,
        validator: PaymobValidator,
        invalid_paymob_credentials: dict,
    ):
        """Test Paymob validation with invalid API key format."""
        result = await validator.validate(invalid_paymob_credentials)
        
        # Should fail due to invalid API key format or authentication
        assert result.is_valid is False
    
    @pytest.mark.asyncio
    async def test_validate_missing_integration_id(
        self,
        validator: PaymobValidator,
        valid_paymob_credentials: dict,
    ):
        """Test Paymob validation with missing integration_id."""
        credentials = valid_paymob_credentials.copy()
        del credentials["integration_id"]
        
        result = await validator.validate(credentials)
        
        assert result.is_valid is False
        assert result.status == ValidationStatus.INVALID
    
    def test_get_display_info(
        self,
        validator: PaymobValidator,
        valid_paymob_credentials: dict,
    ):
        """Test Paymob display info generation."""
        display_info = validator.get_display_info(valid_paymob_credentials)
        
        assert "integration_id" in display_info
        assert display_info["integration_id"] == valid_paymob_credentials["integration_id"]
    
    def test_get_required_fields(self, validator: PaymobValidator):
        """Test that required fields are correctly defined."""
        required = validator.get_required_fields()
        
        assert "api_key" in required
        assert "integration_id" in required


# =============================================================================
# VodafoneCashValidator Tests
# =============================================================================

class TestVodafoneCashValidator:
    """Test suite for VodafoneCashValidator."""
    
    @pytest.fixture
    def validator(self) -> VodafoneCashValidator:
        """Create a VodafoneCashValidator instance."""
        return VodafoneCashValidator()
    
    @pytest.mark.asyncio
    async def test_validate_success(
        self,
        validator: VodafoneCashValidator,
        valid_vodafone_cash_credentials: dict,
        mock_httpx_response_success: MagicMock,
    ):
        """Test successful Vodafone Cash credential validation."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=mock_httpx_response_success)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_vodafone_cash_credentials)
            
            assert result.is_valid is True
            assert result.status == ValidationStatus.VALID
    
    @pytest.mark.asyncio
    async def test_validate_missing_pin(
        self,
        validator: VodafoneCashValidator,
        valid_vodafone_cash_credentials: dict,
    ):
        """Test Vodafone Cash validation with missing PIN."""
        credentials = valid_vodafone_cash_credentials.copy()
        del credentials["pin"]
        
        result = await validator.validate(credentials)
        
        assert result.is_valid is False
    
    def test_get_display_info(
        self,
        validator: VodafoneCashValidator,
        valid_vodafone_cash_credentials: dict,
    ):
        """Test Vodafone Cash display info generation."""
        display_info = validator.get_display_info(valid_vodafone_cash_credentials)
        
        assert "merchant_id" in display_info
        # PIN should never be in display info
        assert "pin" not in display_info


# =============================================================================
# StripeValidator Tests
# =============================================================================

class TestStripeValidator:
    """Test suite for StripeValidator."""
    
    @pytest.fixture
    def validator(self) -> StripeValidator:
        """Create a StripeValidator instance."""
        return StripeValidator()
    
    @pytest.mark.asyncio
    async def test_validate_success(
        self,
        validator: StripeValidator,
        valid_stripe_credentials: dict,
    ):
        """Test successful Stripe credential validation."""
        # Mock Stripe API response
        stripe_response = MagicMock()
        stripe_response.status_code = 200
        stripe_response.json.return_value = {
            "id": "acct_123456",
            "object": "account",
            "business_profile": {"name": "Test Business"},
        }
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=stripe_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_stripe_credentials)
            
            assert result.is_valid is True
            assert result.status == ValidationStatus.VALID
    
    @pytest.mark.asyncio
    async def test_validate_invalid_key_format(
        self,
        validator: StripeValidator,
        invalid_stripe_credentials: dict,
    ):
        """Test Stripe validation with invalid key format."""
        result = await validator.validate(invalid_stripe_credentials)
        
        assert result.is_valid is False
        assert result.status == ValidationStatus.INVALID
    
    @pytest.mark.asyncio
    async def test_validate_api_error(
        self,
        validator: StripeValidator,
        valid_stripe_credentials: dict,
        mock_httpx_response_unauthorized: MagicMock,
    ):
        """Test Stripe validation when API returns error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_httpx_response_unauthorized)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_stripe_credentials)
            
            assert result.is_valid is False
    
    def test_get_display_info(
        self,
        validator: StripeValidator,
        valid_stripe_credentials: dict,
    ):
        """Test Stripe display info generation."""
        display_info = validator.get_display_info(valid_stripe_credentials)
        
        assert "publishable_key" in display_info
        # Secret key should be masked
        if "secret_key" in display_info:
            assert "***" in display_info["secret_key"]
    
    def test_get_required_fields(self, validator: StripeValidator):
        """Test that required fields are correctly defined."""
        required = validator.get_required_fields()
        
        assert "secret_key" in required
        assert "publishable_key" in required


# =============================================================================
# TapValidator Tests
# =============================================================================

class TestTapValidator:
    """Test suite for TapValidator."""
    
    @pytest.fixture
    def validator(self) -> TapValidator:
        """Create a TapValidator instance."""
        return TapValidator()
    
    @pytest.mark.asyncio
    async def test_validate_success(
        self,
        validator: TapValidator,
        valid_tap_credentials: dict,
        mock_httpx_response_success: MagicMock,
    ):
        """Test successful Tap credential validation."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_httpx_response_success)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            
            result = await validator.validate(valid_tap_credentials)
            
            assert result.is_valid is True
            assert result.status == ValidationStatus.VALID
    
    @pytest.mark.asyncio
    async def test_validate_missing_secret_key(
        self,
        validator: TapValidator,
        valid_tap_credentials: dict,
    ):
        """Test Tap validation with missing secret_key."""
        credentials = valid_tap_credentials.copy()
        del credentials["secret_key"]
        
        result = await validator.validate(credentials)
        
        assert result.is_valid is False
    
    def test_get_display_info(
        self,
        validator: TapValidator,
        valid_tap_credentials: dict,
    ):
        """Test Tap display info generation."""
        display_info = validator.get_display_info(valid_tap_credentials)
        
        assert "merchant_id" in display_info
        # Secret key should be masked or not present
        if "secret_key" in display_info:
            assert "***" in display_info["secret_key"]


# =============================================================================
# Validator Factory Tests
# =============================================================================

class TestValidatorFactory:
    """Test suite for GatewayValidatorFactory."""
    
    def test_get_payment_validator_fawry(self):
        """Test getting Fawry validator from factory."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
            ServiceName,
        )
        
        factory = get_validator_factory()
        validator = factory.get_validator(ServiceType.PAYMENT_GATEWAY, ServiceName.FAWRY)
        
        assert validator is not None
        assert isinstance(validator, FawryValidator)
    
    def test_get_payment_validator_paymob(self):
        """Test getting Paymob validator from factory."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
            ServiceName,
        )
        
        factory = get_validator_factory()
        validator = factory.get_validator(ServiceType.PAYMENT_GATEWAY, ServiceName.PAYMOB)
        
        assert validator is not None
        assert isinstance(validator, PaymobValidator)
    
    def test_get_payment_validator_stripe(self):
        """Test getting Stripe validator from factory."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
            ServiceName,
        )
        
        factory = get_validator_factory()
        validator = factory.get_validator(ServiceType.PAYMENT_GATEWAY, ServiceName.STRIPE)
        
        assert validator is not None
        assert isinstance(validator, StripeValidator)
    
    def test_get_unsupported_service(self):
        """Test getting validator for unsupported service."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
            ServiceName,
        )
        
        factory = get_validator_factory()
        
        # This should raise an error or return None for unsupported combinations
        with pytest.raises((ValueError, KeyError)):
            factory.get_validator(ServiceType.PAYMENT_GATEWAY, ServiceName.WHATSAPP_BUSINESS)
    
    def test_is_supported(self):
        """Test checking if a service is supported."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
            ServiceName,
        )
        
        factory = get_validator_factory()
        
        assert factory.is_supported(ServiceType.PAYMENT_GATEWAY, ServiceName.FAWRY) is True
        assert factory.is_supported(ServiceType.PAYMENT_GATEWAY, ServiceName.PAYMOB) is True
        assert factory.is_supported(ServiceType.PAYMENT_GATEWAY, ServiceName.STRIPE) is True
    
    def test_get_supported_services(self):
        """Test getting list of supported services."""
        from src.infrastructure.external_services.gateway_validators import (
            get_validator_factory,
        )
        from src.infrastructure.database.models.tenant.configuration import (
            ServiceType,
        )
        
        factory = get_validator_factory()
        supported = factory.get_supported_services()
        
        assert ServiceType.PAYMENT_GATEWAY in supported
        assert len(supported[ServiceType.PAYMENT_GATEWAY]) > 0
