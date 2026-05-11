"""Twilio SMS integration — Phase 8.6.

Stub by design: real Twilio API calls happen only when
TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM are configured.
Without credentials the service is a no-op that logs the would-be
send, so campaign runs against an un-configured store don't error —
they just emit zero deliveries (the campaign goes to FAILED with a
message rather than throwing).
"""

from src.infrastructure.external_services.twilio.sms_service import (
    SMSDeliveryResult,
    TwilioSMSService,
)

__all__ = ["SMSDeliveryResult", "TwilioSMSService"]
