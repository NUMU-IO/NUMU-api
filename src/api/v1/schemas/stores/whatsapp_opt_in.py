"""Pydantic v2 schemas for the WhatsApp opt-in API surface."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OptInRow(BaseModel):
    """A row returned by GET /whatsapp/opt-ins."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    customer_id: UUID | None = None
    phone: str
    source: Literal["checkout", "signup", "import", "api", "inbound_reply"]
    opted_in_at: datetime
    opted_out_at: datetime | None = None
    opt_out_reason: (
        Literal[
            "inbound_stop_keyword",
            "merchant_revoke",
            "customer_request_via_support",
            "api_revoke",
        ]
        | None
    ) = None


class OptInCreate(BaseModel):
    """Merchant-facing opt-in creation (e.g., import flow)."""

    phone: str = Field(..., description="Will be canonicalized to E.164.")
    customer_id: UUID | None = None
    source: Literal["import", "api", "signup", "inbound_reply"]


class OptInRevoke(BaseModel):
    """Merchant-initiated revocation."""

    phone: str
    reason: Literal["merchant_revoke", "customer_request_via_support", "api_revoke"]


class StorefrontOptIn(BaseModel):
    """Storefront-facing opt-in (anonymous; checkout-session-token gated per FR-007a)."""

    phone: str = Field(
        ..., description="Will be canonicalized to E.164 and matched against the cart."
    )
    checkout_session_token: UUID = Field(
        ...,
        description="Issued by POST /storefront/{store_slug}/checkout-session at the Contact step.",
    )
    customer_id_hint: UUID | None = None
    locale: str = "ar"
