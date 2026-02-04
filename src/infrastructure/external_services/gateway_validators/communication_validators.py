"""Communication service credential validators.

This module provides validators for communication services:
- WhatsApp Business API: Customer messaging
- Twilio: SMS and voice communications
"""

from typing import Any

import httpx

from .base import GatewayValidator, ValidationResult


class WhatsAppValidator(GatewayValidator):
    """Validator for WhatsApp Business API credentials.

    WhatsApp Business API is essential for customer communication in Egypt/MENA.

    Required credentials:
    - access_token: Meta/Facebook access token
    - phone_number_id: WhatsApp phone number ID
    - business_account_id: WhatsApp Business Account ID

    Optional credentials:
    - webhook_verify_token: Token for webhook verification
    """

    GRAPH_API_URL = "https://graph.facebook.com/v18.0"

    @property
    def service_name(self) -> str:
        return "whatsapp_business"

    @property
    def required_fields(self) -> list[str]:
        return ["access_token", "phone_number_id", "business_account_id"]

    @property
    def optional_fields(self) -> list[str]:
        return ["webhook_verify_token"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate WhatsApp Business API credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        access_token = credentials["access_token"]
        phone_number_id = credentials["phone_number_id"]
        business_account_id = credentials["business_account_id"]

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Step 1: Validate access token by fetching phone number info
                phone_response = await client.get(
                    f"{self.GRAPH_API_URL}/{phone_number_id}",
                    params={
                        "access_token": access_token,
                        "fields": "display_phone_number,verified_name,quality_rating",
                    },
                )

                if phone_response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid WhatsApp access token",
                        error_code="INVALID_ACCESS_TOKEN",
                    )

                if phone_response.status_code == 400:
                    error_data = phone_response.json().get("error", {})
                    if "phone_number_id" in str(error_data.get("message", "")).lower():
                        return ValidationResult.failure(
                            message="Invalid WhatsApp phone number ID",
                            error_code="INVALID_PHONE_NUMBER_ID",
                        )
                    return ValidationResult.failure(
                        message=error_data.get("message", "Invalid credentials"),
                        error_code="INVALID_CREDENTIALS",
                    )

                if phone_response.status_code != 200:
                    return ValidationResult.error(
                        message=f"WhatsApp API returned status {phone_response.status_code}",
                        error_code="API_ERROR",
                    )

                phone_data = phone_response.json()

                # Step 2: Validate business account ID
                business_response = await client.get(
                    f"{self.GRAPH_API_URL}/{business_account_id}",
                    params={
                        "access_token": access_token,
                        "fields": "name,id",
                    },
                )

                if business_response.status_code != 200:
                    return ValidationResult.failure(
                        message="Invalid WhatsApp Business Account ID",
                        error_code="INVALID_BUSINESS_ACCOUNT",
                    )

                business_data = business_response.json()

                return ValidationResult.success(
                    message="WhatsApp Business API credentials validated successfully",
                    details={
                        "phone_number": phone_data.get("display_phone_number"),
                        "verified_name": phone_data.get("verified_name"),
                        "quality_rating": phone_data.get("quality_rating"),
                        "business_name": business_data.get("name"),
                    },
                )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate WhatsApp credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )


class TwilioValidator(GatewayValidator):
    """Validator for Twilio credentials.

    Twilio provides SMS and voice communication services.

    Required credentials:
    - account_sid: Twilio Account SID
    - auth_token: Twilio Auth Token

    Optional credentials:
    - phone_number: Twilio phone number for sending
    - messaging_service_sid: Messaging Service SID
    """

    API_URL = "https://api.twilio.com/2010-04-01"

    @property
    def service_name(self) -> str:
        return "twilio"

    @property
    def required_fields(self) -> list[str]:
        return ["account_sid", "auth_token"]

    @property
    def optional_fields(self) -> list[str]:
        return ["phone_number", "messaging_service_sid"]

    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate Twilio credentials."""
        structure_result = self.validate_structure(credentials)
        if not structure_result.is_valid:
            return structure_result

        account_sid = credentials["account_sid"]
        auth_token = credentials["auth_token"]

        # Validate SID format
        if not account_sid.startswith("AC"):
            return ValidationResult.failure(
                message="Invalid Twilio Account SID format (should start with 'AC')",
                error_code="INVALID_SID_FORMAT",
            )

        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                # Validate by fetching account info
                response = await client.get(
                    f"{self.API_URL}/Accounts/{account_sid}.json",
                    auth=(account_sid, auth_token),
                )

                if response.status_code == 200:
                    account = response.json()
                    return ValidationResult.success(
                        message="Twilio credentials validated successfully",
                        details={
                            "account_sid": account_sid,
                            "friendly_name": account.get("friendly_name"),
                            "status": account.get("status"),
                            "type": account.get("type"),
                        },
                    )
                elif response.status_code == 401:
                    return ValidationResult.failure(
                        message="Invalid Twilio credentials",
                        error_code="INVALID_CREDENTIALS",
                    )
                elif response.status_code == 404:
                    return ValidationResult.failure(
                        message="Twilio account not found",
                        error_code="ACCOUNT_NOT_FOUND",
                    )
                else:
                    return ValidationResult.error(
                        message=f"Twilio API returned status {response.status_code}",
                        error_code="API_ERROR",
                    )

        except httpx.TimeoutException:
            return ValidationResult.timeout()
        except Exception as e:
            return ValidationResult.error(
                message=f"Failed to validate Twilio credentials: {str(e)}",
                error_code="VALIDATION_ERROR",
            )
