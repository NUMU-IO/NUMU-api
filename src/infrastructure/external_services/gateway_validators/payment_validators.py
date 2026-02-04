"""Payment gateway credential validators.

This module provides validators for Egyptian and regional payment gateways:
- Fawry: Egypt's leading payment network
- Paymob: Egypt's payment infrastructure provider
- Vodafone Cash: Mobile wallet payments
- Stripe: International payments
- Tap: MENA region payment gateway
"""

import hashlib
from typing import Any

import httpx

from .base import GatewayValidator, ValidationResult


class FawryValidator(GatewayValidator):
    """Validator for Fawry payment gateway credentials.

    Fawry is Egypt's leading payment network, supporting:
    - Reference number payments (pay at Fawry outlets)
    - Card payments
    - Fawry Pay wallet

    Required credentials:
    - merchant_code: Fawry merchant identifier
    - security_key: Secret key for signature generation

    Optional credentials:
    - return_url: URL for payment completion redirect
    """

    # Fawry API endpoints
    STAGING_URL = "https://atfawry.fawrystaging.com/ECommerceWeb/Fawry"
    PRODUCTION_URL = "https://www.atfawry.com/ECommerceWeb/Fawry"

    @property
    def service_name(self) -> str:
        return "fawry"

    @property
    def required_fields(self) -> list[str]:
        return ["merchant_code", "security_key"]

    @property
    def optional_fields(self) -> list[str]:
        return ["return_url", "environment"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Fawry credentials.

        Validates by attempting to generate a valid signature and
        checking the merchant status with Fawry's API.
        """
        # First validate structure
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        merchant_code = credentials["merchant_code"]
        security_key = credentials["security_key"]
        environment = credentials.get("environment", "staging")

        # Determine API URL
        base_url = self.STAGING_URL if environment == "staging" else self.PRODUCTION_URL

        try:
            # Generate test signature to validate security key format
            test_data = f"{merchant_code}test123"
            signature = hashlib.sha256((test_data + security_key).encode()).hexdigest()

            # Make a test API call to validate merchant code
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Use the payment status endpoint with a dummy reference
                response = await client.get(
                    f"{base_url}/payments/status/v2",
                    params={
                        "merchantCode": merchant_code,
                        "merchantRefNumber": "VALIDATION_TEST",
                        "signature": signature,
                    },
                )

                # Fawry returns specific error codes
                # 9901 = Invalid merchant code
                # 9946 = Invalid signature
                # Other codes indicate the credentials are valid but order not found (expected)

                if response.status_code == 200:
                    data = response.json()
                    status_code = data.get("statusCode")

                    if status_code in ["9901"]:
                        return ValidationResult.failure(
                            message="Invalid Fawry merchant code",
                            error_code="INVALID_MERCHANT_CODE",
                        )
                    elif status_code in ["9946"]:
                        return ValidationResult.failure(
                            message="Invalid Fawry security key",
                            error_code="INVALID_SECURITY_KEY",
                        )
                    else:
                        # Any other response means credentials are valid
                        return ValidationResult.success(
                            message="Fawry credentials validated successfully",
                            details={
                                "merchant_code": merchant_code,
                                "environment": environment,
                            },
                        )
                else:
                    return ValidationResult.error(
                        message=f"Fawry API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout(
                message="Fawry API validation request timed out"
            )
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Fawry credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class PaymobValidator(GatewayValidator):
    """Validator for Paymob payment gateway credentials.

    Paymob is Egypt's payment infrastructure provider, supporting:
    - Card payments
    - Mobile wallets
    - Installments
    - BNPL (Buy Now Pay Later)

    Required credentials:
    - api_key: Paymob API key
    - integration_id: Payment integration ID

    Optional credentials:
    - iframe_id: Paymob iframe ID for card payments
    - hmac_secret: HMAC secret for webhook verification
    """

    API_URL = "https://accept.paymob.com/api"

    @property
    def service_name(self) -> str:
        return "paymob"

    @property
    def required_fields(self) -> list[str]:
        return ["api_key", "integration_id"]

    @property
    def optional_fields(self) -> list[str]:
        return ["iframe_id", "hmac_secret"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Paymob credentials.

        Validates by attempting to authenticate with the Paymob API
        and checking the integration ID.
        """
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        api_key = credentials["api_key"]
        integration_id = credentials["integration_id"]

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Step 1: Authenticate to get auth token
                auth_response = await client.post(
                    f"{self.API_URL}/auth/tokens", json={"api_key": api_key}
                )

                if auth_response.status_code != 201:
                    return ValidationResult.failure(
                        message="Invalid Paymob API key",
                        error_code="INVALID_API_KEY",
                    )

                auth_data = auth_response.json()
                auth_token = auth_data.get("token")

                if not auth_token:
                    return ValidationResult.failure(
                        message="Failed to obtain Paymob auth token",
                        error_code="AUTH_FAILED",
                    )

                # Step 2: Verify integration ID exists
                # Get merchant profile to validate
                profile_response = await client.get(
                    f"{self.API_URL}/acceptance/payment_integrations",
                    headers={"Authorization": f"Bearer {auth_token}"},
                )

                if profile_response.status_code == 200:
                    integrations = profile_response.json()
                    integration_ids = [str(i.get("id")) for i in integrations]

                    if str(integration_id) not in integration_ids:
                        return ValidationResult.failure(
                            message="Integration ID not found in your Paymob account",
                            error_code="INVALID_INTEGRATION_ID",
                            details={"available_integrations": len(integrations)},
                        )

                    return ValidationResult.success(
                        message="Paymob credentials validated successfully",
                        details={
                            "integration_id": integration_id,
                            "total_integrations": len(integrations),
                        },
                    )
                else:
                    return ValidationResult.error(
                        message="Failed to verify Paymob integration",
                        error_code="VERIFICATION_FAILED",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Paymob credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class VodafoneCashValidator(GatewayValidator):
    """Validator for Vodafone Cash mobile wallet credentials.

    Vodafone Cash is Egypt's leading mobile wallet service.

    Required credentials:
    - merchant_id: Vodafone Cash merchant identifier
    - api_key: API key for authentication
    - pin: Merchant PIN for transactions
    """

    @property
    def service_name(self) -> str:
        return "vodafone_cash"

    @property
    def required_fields(self) -> list[str]:
        return ["merchant_id", "api_key", "pin"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Vodafone Cash credentials.

        Note: Vodafone Cash API access requires special partnership.
        This validator performs basic structure validation.
        """
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        # Vodafone Cash requires special API access
        # For now, we validate structure and format
        merchant_id = credentials["merchant_id"]

        # Basic format validation
        if not merchant_id.isdigit():
            return ValidationResult.failure(
                message="Vodafone Cash merchant ID must be numeric",
                error_code="INVALID_FORMAT",
            )

        return ValidationResult.success(
            message="Vodafone Cash credentials structure validated",
            details={
                "merchant_id": merchant_id,
                "note": "Full validation requires API partnership",
            },
        )


class StripeValidator(GatewayValidator):
    """Validator for Stripe payment gateway credentials.

    Required credentials:
    - secret_key: Stripe secret API key (sk_live_* or sk_test_*)
    - publishable_key: Stripe publishable key (pk_live_* or pk_test_*)

    Optional credentials:
    - webhook_secret: Webhook signing secret (whsec_*)
    """

    API_URL = "https://api.stripe.com/v1"

    @property
    def service_name(self) -> str:
        return "stripe"

    @property
    def required_fields(self) -> list[str]:
        return ["secret_key", "publishable_key"]

    @property
    def optional_fields(self) -> list[str]:
        return ["webhook_secret"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Stripe credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        secret_key = credentials["secret_key"]
        publishable_key = credentials["publishable_key"]

        # Validate key format
        if not secret_key.startswith(("sk_live_", "sk_test_")):
            return ValidationResult.failure(
                message="Invalid Stripe secret key format",
                error_code="INVALID_KEY_FORMAT",
            )

        if not publishable_key.startswith(("pk_live_", "pk_test_")):
            return ValidationResult.failure(
                message="Invalid Stripe publishable key format",
                error_code="INVALID_KEY_FORMAT",
            )

        # Check environment consistency
        secret_env = "live" if "live" in secret_key else "test"
        pub_env = "live" if "live" in publishable_key else "test"

        if secret_env != pub_env:
            return ValidationResult.failure(
                message="Secret and publishable keys must be from the same environment",
                error_code="ENVIRONMENT_MISMATCH",
            )

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Test the secret key by fetching account info
                response = await client.get(
                    f"{self.API_URL}/account", auth=(secret_key, "")
                )

                if response.status_code == 200:
                    account = response.json()
                    return ValidationResult.success(
                        message="Stripe credentials validated successfully",
                        details={
                            "account_id": account.get("id"),
                            "business_name": account.get("business_profile", {}).get(
                                "name"
                            ),
                            "environment": secret_env,
                        },
                    )
                elif response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid Stripe API key",
                        error_code="INVALID_API_KEY",
                    )
                else:
                    return ValidationResult.error(
                        message=f"Stripe API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Stripe credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class TapValidator(GatewayValidator):
    """Validator for Tap Payments gateway credentials.

    Tap is a leading payment gateway in the MENA region.

    Required credentials:
    - secret_key: Tap secret API key
    - publishable_key: Tap publishable key
    """

    API_URL = "https://api.tap.company/v2"

    @property
    def service_name(self) -> str:
        return "tap"

    @property
    def required_fields(self) -> list[str]:
        return ["secret_key", "publishable_key"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Tap Payments credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        secret_key = credentials["secret_key"]

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Test by fetching business info
                response = await client.get(
                    f"{self.API_URL}/business",
                    headers={"Authorization": f"Bearer {secret_key}"},
                )

                if response.status_code == 200:
                    business = response.json()
                    return ValidationResult.success(
                        message="Tap credentials validated successfully",
                        details={
                            "business_id": business.get("id"),
                            "business_name": business.get("name"),
                        },
                    )
                elif response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid Tap API key",
                        error_code="INVALID_API_KEY",
                    )
                else:
                    return ValidationResult.error(
                        message=f"Tap API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Tap credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )
