"""Pydantic schemas for the risk narrative endpoint (backend-024)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NarrativeFactorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    score: int = Field(ge=0, le=100)
    weight: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=512)


class NarrativeEntitiesIn(BaseModel):
    """Optional PII context the caller has access to.

    All fields are optional — if a caller doesn't have the customer
    profile loaded, it can omit them and rely on the regex-based
    belt-and-suspenders pass for sanity checking.
    """

    customer_first_name: str | None = None
    customer_last_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    shipping_address1: str | None = None
    shipping_address2: str | None = None
    shipping_city: str | None = None
    shipping_country: str | None = None
    order_number: str | None = None
    shopify_order_id: str | None = None


class NarrativeRequest(BaseModel):
    factors: list[NarrativeFactorIn] = Field(min_length=1, max_length=20)
    language: Literal["ar", "en"] = "ar"
    purpose: Literal["merchant_dashboard", "customer_recovery_personalization"] = (
        "merchant_dashboard"
    )
    entities: NarrativeEntitiesIn | None = None


class NarrativeResponse(BaseModel):
    narrative_en: str | None = None
    narrative_ar: str | None = None
    model_version: str | None = None
    failure_reason: str | None = None
