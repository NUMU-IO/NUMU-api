"""Shipping carrier credential validators.

This module provides validators for Egyptian and regional shipping carriers:
- Aramex: International and regional shipping
- Bosta: Egyptian last-mile delivery
- MylerZ: Egyptian e-commerce logistics
"""

from typing import Any

import httpx

from .base import GatewayValidator, ValidationResult


class AramexValidator(GatewayValidator):
    """Validator for Aramex shipping credentials.

    Aramex is a leading logistics company in the MENA region.

    Required credentials:
    - username: Aramex account username
    - password: Aramex account password
    - account_number: Aramex account number
    - account_pin: Account PIN
    - account_entity: Account entity (country code)
    - account_country_code: Two-letter country code
    """

    API_URL = "https://ws.aramex.net/ShippingAPI.V2/Shipping/Service_1_0.svc/json"

    @property
    def service_name(self) -> str:
        return "aramex"

    @property
    def required_fields(self) -> list[str]:
        return [
            "username",
            "password",
            "account_number",
            "account_pin",
            "account_entity",
            "account_country_code",
        ]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Aramex credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Use the FetchCities endpoint to validate credentials
                response = await client.post(
                    f"{self.API_URL}/FetchCities",
                    json={
                        "ClientInfo": {
                            "UserName": credentials["username"],
                            "Password": credentials["password"],
                            "Version": "v1.0",
                            "AccountNumber": credentials["account_number"],
                            "AccountPin": credentials["account_pin"],
                            "AccountEntity": credentials["account_entity"],
                            "AccountCountryCode": credentials["account_country_code"],
                        },
                        "CountryCode": credentials["account_country_code"],
                    },
                )

                if response.status_code == 200:
                    data = response.json()

                    # Check for authentication errors
                    if data.get("HasErrors"):
                        notifications = data.get("Notifications", [])
                        error_msg = (
                            notifications[0].get("Message")
                            if notifications
                            else "Authentication failed"
                        )
                        return ValidationResult.failure(
                            message=f"Aramex authentication failed: {error_msg}",
                            error_code="AUTH_FAILED",
                        )

                    return ValidationResult.success(
                        message="Aramex credentials validated successfully",
                        details={
                            "account_number": credentials["account_number"],
                            "country_code": credentials["account_country_code"],
                        },
                    )
                else:
                    return ValidationResult.error(
                        message=f"Aramex API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Aramex credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class BostaValidator(GatewayValidator):
    """Validator for Bosta shipping credentials.

    Bosta is Egypt's leading last-mile delivery company.

    Required credentials:
    - api_key: Bosta API key
    - business_id: Bosta business identifier
    """

    API_URL = "https://app.bosta.co/api/v2"
    STAGING_URL = "https://stg-app.bosta.co/api/v2"

    @property
    def service_name(self) -> str:
        return "bosta"

    @property
    def required_fields(self) -> list[str]:
        return ["api_key", "business_id"]

    @property
    def optional_fields(self) -> list[str]:
        return ["environment"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Bosta credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        api_key = credentials["api_key"]
        business_id = credentials["business_id"]
        environment = credentials.get("environment", "production")

        base_url = self.STAGING_URL if environment == "staging" else self.API_URL

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Validate by fetching cities list
                response = await client.get(
                    f"{base_url}/cities",
                    headers={
                        "Authorization": api_key,
                        "X-Business-Id": business_id,
                    },
                )

                if response.status_code == 200:
                    return ValidationResult.success(
                        message="Bosta credentials validated successfully",
                        details={
                            "business_id": business_id,
                            "environment": environment,
                        },
                    )
                elif response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid Bosta API key",
                        error_code="INVALID_API_KEY",
                    )
                elif response.status_code == 403:
                    return ValidationResult.failure(
                        message="Invalid Bosta business ID or unauthorized access",
                        error_code="UNAUTHORIZED",
                    )
                else:
                    return ValidationResult.error(
                        message=f"Bosta API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Bosta credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class MylerzValidator(GatewayValidator):
    """Validator for MylerZ shipping credentials.

    MylerZ is an Egyptian e-commerce logistics provider.

    Required credentials:
    - api_key: MylerZ API key
    - account_id: MylerZ account identifier
    """

    API_URL = "https://api.mylerz.com/v1"

    @property
    def service_name(self) -> str:
        return "mylerz"

    @property
    def required_fields(self) -> list[str]:
        return ["api_key", "account_id"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate MylerZ credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        api_key = credentials["api_key"]
        account_id = credentials["account_id"]

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Validate by fetching account info
                response = await client.get(
                    f"{self.API_URL}/account",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "X-Account-Id": account_id,
                    },
                )

                if response.status_code == 200:
                    account = response.json()
                    return ValidationResult.success(
                        message="MylerZ credentials validated successfully",
                        details={
                            "account_id": account_id,
                            "account_name": account.get("name"),
                        },
                    )
                elif response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid MylerZ API key",
                        error_code="INVALID_API_KEY",
                    )
                elif response.status_code == 403:
                    return ValidationResult.failure(
                        message="Invalid MylerZ account ID",
                        error_code="INVALID_ACCOUNT",
                    )
                else:
                    return ValidationResult.error(
                        message=f"MylerZ API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate MylerZ credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )
