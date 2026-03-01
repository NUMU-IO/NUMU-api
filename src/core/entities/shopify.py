"""Shopify domain entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ShopifyInstallation(BaseEntity):
    """Shopify app installation."""

    store_id: UUID
    tenant_id: UUID
    shopify_domain: str
    access_token_encrypted: str = ""
    scopes: list[str] | None = None
    app_plan: str = "free"
    installed_at: datetime = Field(default_factory=datetime.now)
    uninstalled_at: datetime | None = None
    is_active: bool = True


class RiskAssessment(BaseEntity):
    """Order risk assessment."""

    store_id: UUID
    tenant_id: UUID | None = None
    order_id: UUID | None = None
    shopify_order_id: str | None = None
    order_number: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    total_cents: int = 0
    currency: str = "EGP"
    payment_method: str | None = None
    risk_score: int = 0
    risk_level: str = "low"
    suggested_action: str | None = None
    action_taken: str | None = None
    action_taken_at: datetime | None = None
    action_taken_by: str | None = None
    factors: list[dict] = Field(default_factory=list)


class PaymentTransaction(BaseEntity):
    """Payment transaction entry."""

    store_id: UUID
    tenant_id: UUID | None = None
    order_id: UUID | None = None
    channel: str
    gateway: str
    display_name: str | None = None
    amount_cents: int = 0
    currency: str = "EGP"
    status: str = "pending"
    failure_reason: str | None = None
    failure_code: str | None = None
    gateway_transaction_id: str | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None


class AutomationRule(BaseEntity):
    """Automation rule definition."""

    store_id: UUID
    tenant_id: UUID | None = None
    name: str
    description: str | None = None
    is_active: bool = True
    priority: int = 0
    trigger_event: str
    conditions: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    times_triggered: int = 0
    last_triggered_at: datetime | None = None


class AutomationLog(BaseEntity):
    """Automation rule execution log."""

    store_id: UUID
    tenant_id: UUID | None = None
    rule_id: UUID
    rule_name: str | None = None
    order_id: UUID | None = None
    order_number: str | None = None
    trigger_event: str
    actions_executed: list[dict] = Field(default_factory=list)
    status: str = "success"
    error_details: str | None = None


class ShopifyAppSettings(BaseEntity):
    """Per-store Shopify app settings."""

    store_id: UUID
    tenant_id: UUID | None = None
    cod_risk_scoring_enabled: bool = True
    auto_approve_threshold: int = 30
    auto_hold_threshold: int = 70
    auto_cancel_threshold: int = 90
    paymob_connected: bool = False
    whatsapp_connected: bool = False
