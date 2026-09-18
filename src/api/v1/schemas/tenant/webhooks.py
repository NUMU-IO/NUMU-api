"""Webhook Pydantic schemas."""

from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from src.core.entities.webhook import SUBSCRIBABLE_EVENT_TYPES


class CreateWebhookSubscriptionRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://myapp.example.com/webhooks/numu",
                "events": ["order.created", "order.paid"],
                "description": "Send order events to my ERP",
            }
        }
    )

    url: AnyHttpUrl = Field(
        ..., description="HTTPS URL that will receive webhook POSTs"
    )
    events: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Event types to subscribe to. Valid values: "
            f"{[e.value for e in SUBSCRIBABLE_EVENT_TYPES]}"
        ),
    )
    description: str | None = Field(None, max_length=255)


class UpdateWebhookSubscriptionRequest(BaseModel):
    """Change an endpoint in place. Every field is optional."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"events": ["order.paid"], "is_active": False}}
    )

    url: AnyHttpUrl | None = None
    events: list[str] | None = Field(default=None, min_length=1)
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=255)


class WebhookTestResponse(BaseModel):
    """The result of the test delivery, as the endpoint answered it."""

    delivered: bool
    status_code: int | None = None
    error: str | None = None
    delivery_id: str


class WebhookSubscriptionCreatedResponse(BaseModel):
    """Returned once at creation — includes the signing secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    store_id: str
    url: str
    events: list[str]
    is_active: bool
    description: str | None
    secret: str = Field(
        ..., description="HMAC-SHA256 signing secret. Shown ONCE — save it now."
    )
    created_at: datetime


class WebhookSubscriptionResponse(BaseModel):
    """Standard subscription response — secret is never included."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    store_id: str
    url: str
    events: list[str]
    is_active: bool
    description: str | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subscription_id: str | None
    event_type: str
    event_id: str
    status: str
    attempt_count: int
    last_status_code: int | None
    last_response_body: str | None
    last_error: str | None
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    exhausted_at: datetime | None
    created_at: datetime
