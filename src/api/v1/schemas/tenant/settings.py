"""Store settings Pydantic schemas."""

from typing import Any
from pydantic import BaseModel, Field


# Payment Settings
class PaymentMethodStatus(BaseModel):
    """Individual payment method status."""
    enabled: bool = False
    is_configured: bool = False  # Backend determines this based on API keys
    last_configured: str | None = None


class PaymentSettingsResponse(BaseModel):
    """Payment settings response."""
    cod: PaymentMethodStatus
    fawry: PaymentMethodStatus
    paymob: PaymentMethodStatus
    vodafone_cash: PaymentMethodStatus
    bank_transfer: PaymentMethodStatus
    bank_accounts_count: int = 0


class UpdatePaymentSettingsRequest(BaseModel):
    """Update payment settings (only toggles, not API keys)."""
    cod_enabled: bool | None = None
    fawry_enabled: bool | None = None
    paymob_enabled: bool | None = None
    vodafone_cash_enabled: bool | None = None
    bank_transfer_enabled: bool | None = None


# Shipping Settings
class ShippingCarrierStatus(BaseModel):
    """Individual shipping carrier status."""
    enabled: bool = False
    is_configured: bool = False
    last_configured: str | None = None


class ShippingZone(BaseModel):
    """Shipping zone configuration."""
    id: str
    zone: str
    governorates: str
    rate: float
    estimated_days: str


class ShippingSettingsResponse(BaseModel):
    """Shipping settings response."""
    aramex: ShippingCarrierStatus
    bosta: ShippingCarrierStatus
    mylerz: ShippingCarrierStatus
    manual: ShippingCarrierStatus
    zones: list[ShippingZone] = []
    free_shipping_threshold: float = 0


class UpdateShippingSettingsRequest(BaseModel):
    """Update shipping settings."""
    aramex_enabled: bool | None = None
    bosta_enabled: bool | None = None
    mylerz_enabled: bool | None = None
    manual_enabled: bool | None = None
    free_shipping_threshold: float | None = None


class CreateShippingZoneRequest(BaseModel):
    """Create shipping zone request."""
    zone: str = Field(..., min_length=1, max_length=100)
    governorates: str = Field(..., min_length=1, max_length=500)
    rate: float = Field(..., ge=0)
    estimated_days: str = Field(..., min_length=1, max_length=50)


class UpdateShippingZoneRequest(BaseModel):
    """Update shipping zone request."""
    zone: str | None = Field(None, min_length=1, max_length=100)
    governorates: str | None = Field(None, min_length=1, max_length=500)
    rate: float | None = Field(None, ge=0)
    estimated_days: str | None = Field(None, min_length=1, max_length=50)


# WhatsApp Settings
class NotificationTemplate(BaseModel):
    """Notification template configuration."""
    enabled: bool = False
    template: str = ""
    delay: int | None = None  # For abandoned cart


class WhatsAppNotifications(BaseModel):
    """WhatsApp notification templates."""
    order_confirmation: NotificationTemplate
    order_shipped: NotificationTemplate
    order_delivered: NotificationTemplate
    abandoned_cart: NotificationTemplate
    low_stock: NotificationTemplate


class WhatsAppSettingsResponse(BaseModel):
    """WhatsApp settings response."""
    enabled: bool = False
    is_configured: bool = False
    last_configured: str | None = None
    phone_number: str | None = None  # Masked phone number
    notifications: WhatsAppNotifications
    messages_today: int = 0
    delivery_rate: float = 0
    api_quota: int = 1000


class UpdateWhatsAppSettingsRequest(BaseModel):
    """Update WhatsApp settings."""
    enabled: bool | None = None
    notifications: dict[str, Any] | None = None


# Combined Store Settings
class StoreSettingsResponse(BaseModel):
    """Combined store settings response."""
    payment: PaymentSettingsResponse
    shipping: ShippingSettingsResponse
    whatsapp: WhatsAppSettingsResponse
