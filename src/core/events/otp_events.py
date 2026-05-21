"""WhatsApp OTP domain events (backend-025 / spec 015).

Emitted only on successful verify. Carries hashed phone (per
constitution Principle II — never raw PII in events) so the trust
signal handler can write a positive ``network_event`` without
re-deriving the hash.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from src.core.events.base import DomainEvent


class OtpVerifiedEvent(DomainEvent):
    """Emitted when a customer successfully verifies their WhatsApp OTP."""

    otp_id: UUID
    tenant_id: UUID
    store_id: UUID
    phone_hash: str
    verified_at: datetime
