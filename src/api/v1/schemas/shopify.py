"""Pydantic schemas for the Shopify module endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterShopRequest(BaseModel):
    """POST /shopify/auth/register-shop."""

    shopify_domain: str = Field(..., max_length=255)
    access_token: str = Field(..., max_length=512)
    scopes: list[str] = Field(default_factory=list)


class InstallationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: str
    tenant_id: str
    shopify_domain: str
    status: str = "active"
    app_plan: str = "free"


class LookupResponse(BaseModel):
    store_id: str


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardOverviewResponse(BaseModel):
    cod_success_rate: float = 0.0
    revenue_protected_cents: int = 0
    high_risk_orders_count: int = 0
    payment_recovery_cents: int = 0
    total_orders: int = 0
    total_cod_orders: int = 0
    total_revenue_cents: int = 0
    period_days: int = 30
    # COD-to-Prepaid conversion metrics
    conversion_links_sent: int = 0
    conversion_links_completed: int = 0
    conversion_rate: float = 0.0
    conversion_revenue_cents: int = 0


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class RiskFactorSchema(BaseModel):
    factor: str
    score: float
    weight: float
    reason: str


class RiskOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_number: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    total_cents: int = 0
    currency: str = "EGP"
    payment_method: str | None = None
    risk_score: int = 0
    risk_level: str = "low"
    score_type: str = "preliminary"
    suggested_action: str | None = None
    action_taken: str | None = None
    factors: list[RiskFactorSchema] = Field(default_factory=list)
    scored_at: datetime | None = None
    created_at: datetime | None = None


class RiskActionRequest(BaseModel):
    action: str = Field(..., max_length=50)


# ---------------------------------------------------------------------------
# Payment Channels
# ---------------------------------------------------------------------------


class PaymentChannelSchema(BaseModel):
    channel: str
    gateway: str
    display_name: str
    total_attempts: int = 0
    successful: int = 0
    failed: int = 0
    success_rate: float = 0.0
    revenue_cents: int = 0
    revenue_share_pct: float = 0.0
    avg_processing_ms: int | None = None
    trend: str = "stable"


class FailureReasonSchema(BaseModel):
    reason: str
    code: str | None = None
    count: int = 0
    pct: float = 0.0


class PaymentChannelsResponse(BaseModel):
    period_days: int = 30
    total_revenue_cents: int = 0
    total_transactions: int = 0
    overall_success_rate: float = 0.0
    channels: list[PaymentChannelSchema] = Field(default_factory=list)
    top_failure_reasons: list[FailureReasonSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------


class RuleConditionSchema(BaseModel):
    field: str
    operator: str
    value: object


class RuleActionSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str


class AutomationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    is_active: bool = True
    priority: int = 0
    trigger_event: str
    conditions: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    times_triggered: int = 0
    last_triggered_at: datetime | None = None
    created_at: datetime | None = None


class AutomationRuleUpdateRequest(BaseModel):
    is_active: bool | None = None
    name: str | None = None
    description: str | None = None
    priority: int | None = None
    conditions: list[dict] | None = None
    actions: list[dict] | None = None


class CreateFromTemplateRequest(BaseModel):
    template_id: str = Field(..., max_length=100)


class AutomationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_id: str
    rule_name: str | None = None
    order_id: str | None = None
    order_number: str | None = None
    trigger_event: str
    actions_executed: list[dict] = Field(default_factory=list)
    status: str = "success"
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class AppSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: str
    shopify_domain: str = ""
    app_plan: str = "free"
    paymob_connected: bool = False
    whatsapp_connected: bool = False
    cod_risk_scoring_enabled: bool = True
    auto_approve_threshold: int = 30
    auto_hold_threshold: int = 70
    auto_cancel_threshold: int = 90


class UpdateSettingsRequest(BaseModel):
    cod_risk_scoring_enabled: bool | None = None
    auto_approve_threshold: int | None = None
    auto_hold_threshold: int | None = None
    auto_cancel_threshold: int | None = None


class ConnectPaymobRequest(BaseModel):
    api_key: str
    integration_id: str
    hmac_secret: str


# ---------------------------------------------------------------------------
# Payment Links
# ---------------------------------------------------------------------------


class CreatePaymentLinkRequest(BaseModel):
    shopify_order_id: str
    amount_cents: int = Field(..., gt=0)
    currency: str = Field(default="EGP", max_length=3)
    customer_phone: str | None = None
    customer_name: str | None = None


class PaymentLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    store_id: str
    shopify_order_id: str | None = None
    amount_cents: int
    currency: str = "EGP"
    status: str = "pending"
    available_gateways: list[str] = Field(default_factory=list)
    merchant_branding: dict | None = None
    payment_url: str = ""
    expires_at: datetime | None = None
    created_at: datetime | None = None


class PaymentLinkPublicResponse(BaseModel):
    """Public response for the payment page — no internal IDs exposed."""

    session_id: str
    amount_cents: int
    currency: str = "EGP"
    status: str = "pending"
    available_gateways: list[str] = Field(default_factory=list)
    merchant_branding: dict | None = None
    store_name: str = ""
    order_number: str = ""
    expires_at: datetime | None = None
    is_expired: bool = False


class CompletePaymentRequest(BaseModel):
    gateway_used: str = Field(..., max_length=50)
    gateway_transaction_id: str = Field(..., max_length=255)


class CompletePaymentResponse(BaseModel):
    status: str
    message: str


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhookProcessRequest(BaseModel):
    topic: str
    shop_domain: str
    payload: dict = Field(default_factory=dict)
    webhook_id: str | None = None
