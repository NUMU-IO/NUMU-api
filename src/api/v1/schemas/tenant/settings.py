"""Store settings Pydantic schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# Payment Settings
class PaymentMethodStatus(BaseModel):
    """Individual payment method status."""

    enabled: bool = False
    is_configured: bool = False  # Backend determines this based on API keys
    last_configured: str | None = None


# Gateway providers allowed to carry a COD deposit. Bank transfer and
# vodafone-cash aren't on this list because they're manual/async and
# would defeat the "confirm before create" purpose.
DepositGateway = Literal["paymob", "kashier", "fawry", "fawaterak", "instapay"]

DEPOSIT_GATEWAY_VALUES: tuple[str, ...] = (
    "paymob",
    "kashier",
    "fawry",
    "fawaterak",
    "instapay",
)


class CodDepositPolicy(BaseModel):
    """Per-store deposit-to-confirm-COD policy.

    When enabled, customers who select COD at checkout are asked to
    pay a fixed deposit via one of `allowed_gateways` BEFORE the
    order is created. On successful deposit, the order moves to
    CONFIRMED with `deposit_amount_cents` stored and the remaining
    balance due on delivery. If the deposit isn't completed within
    `ttl_minutes`, the order auto-cancels.

    Starts with fixed-amount only. Percentage and "cover shipping"
    variants become alternative shapes of this object when merchants
    ask for them.
    """

    enabled: bool = False
    # Fixed deposit amount in cents. Merchants typically set this to
    # match their delivery fee (40–80 EGP in Egypt).
    amount_cents: int = Field(default=0, ge=0)
    # How long the customer has to complete the deposit payment before
    # the intent expires and the order auto-cancels. Defaults to
    # 30 min (matches the existing InstaPay-proof TTL). Range is
    # deliberately bounded — under 5 min is hostile UX, over 24 h
    # creates awkward abandoned-order backlog.
    ttl_minutes: int = Field(default=30, ge=5, le=1440)
    # If the merchant cancels an order with a paid deposit, auto-issue
    # a refund via the original gateway. When False, refunds are left
    # to the merchant to process manually in the gateway console.
    auto_refund_on_cancel: bool = False
    # Allowlist of gateways the customer can pay the deposit with. If
    # the merchant saves an empty list while `enabled=True`, we 400
    # because the deposit would be uncollectible.
    allowed_gateways: list[DepositGateway] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_gateways_when_enabled(self):
        if self.enabled and not self.allowed_gateways:
            raise ValueError(
                "At least one allowed_gateway is required when the deposit "
                "policy is enabled."
            )
        if self.enabled and self.amount_cents <= 0:
            raise ValueError(
                "amount_cents must be greater than 0 when the deposit "
                "policy is enabled."
            )
        # De-dup while preserving order for deterministic storage.
        if self.allowed_gateways:
            seen: set[str] = set()
            deduped: list[DepositGateway] = []
            for g in self.allowed_gateways:
                if g not in seen:
                    seen.add(g)
                    deduped.append(g)
            object.__setattr__(self, "allowed_gateways", deduped)
        return self


class PaymentSettingsResponse(BaseModel):
    """Payment settings response."""

    cod: PaymentMethodStatus
    fawry: PaymentMethodStatus
    # Previously absent from the response though present in stored
    # settings. Surfaced now so the deposit-policy UI can render the
    # allowed-gateway picker without a separate credential fetch per
    # gateway.
    fawaterak: PaymentMethodStatus = Field(default_factory=PaymentMethodStatus)
    paymob: PaymentMethodStatus
    kashier: PaymentMethodStatus = Field(default_factory=PaymentMethodStatus)
    instapay: PaymentMethodStatus = Field(default_factory=PaymentMethodStatus)
    vodafone_cash: PaymentMethodStatus
    bank_transfer: PaymentMethodStatus
    bank_accounts_count: int = 0
    # Flattened onto the response so the merchant hub can render it
    # as a card under COD without a second round-trip.
    cod_deposit_policy: CodDepositPolicy = Field(default_factory=CodDepositPolicy)


class UpdatePaymentSettingsRequest(BaseModel):
    """Update payment settings (only toggles, not API keys)."""

    cod_enabled: bool | None = None
    fawry_enabled: bool | None = None
    fawaterak_enabled: bool | None = None
    paymob_enabled: bool | None = None
    kashier_enabled: bool | None = None
    instapay_enabled: bool | None = None
    vodafone_cash_enabled: bool | None = None
    bank_transfer_enabled: bool | None = None
    # Send the full policy object to replace it; omit to leave unchanged.
    cod_deposit_policy: CodDepositPolicy | None = None


# COD Trust Network Settings
class CodTrustResponse(BaseModel):
    """COD trust network protection settings."""

    enabled: bool = False
    threshold: int = 70
    min_confidence: Literal["low", "medium", "high"] = "medium"
    action: Literal["block", "warn"] = "block"


class UpdateCodTrustRequest(BaseModel):
    """Update COD trust network settings (all fields optional)."""

    enabled: bool | None = None
    threshold: int | None = Field(None, ge=0, le=100)
    min_confidence: Literal["low", "medium", "high"] | None = None
    action: Literal["block", "warn"] | None = None


class SavePaymobCredentialsRequest(BaseModel):
    """Save Paymob gateway credentials for a store."""

    secret_key: str = Field(..., min_length=10, max_length=500)
    public_key: str = Field(..., min_length=10, max_length=500)
    hmac_secret: str = Field(..., min_length=10, max_length=500)
    card_integration_id: str = Field(..., min_length=1, max_length=50)
    wallet_integration_id: str | None = Field(None, max_length=50)


class PaymobCredentialsResponse(BaseModel):
    """Paymob credentials status (masked, never returns real keys)."""

    is_configured: bool
    public_key_masked: str | None = None
    secret_key_masked: str | None = None
    hmac_secret_masked: str | None = None
    card_integration_id: str | None = None
    wallet_integration_id: str | None = None
    last_configured: str | None = None


class SaveInstapayCredentialsRequest(BaseModel):
    """Save merchant InstaPay configuration.

    The IPA (Instant Payment Address, e.g. ``merchant@cib``) is the
    primary routing key and the one sensitive field — a leaked or
    swapped IPA lets someone impersonate the merchant on the
    proof-verification step. Phones are optional fallback display.
    Thresholds are policy the merchant tunes.
    """

    ipa: str = Field(..., min_length=3, max_length=80)
    ipa_display_name: str | None = Field(None, max_length=100)
    fallback_phone: str | None = Field(None, max_length=20)
    auto_approve_threshold_cents: int = Field(50_000, ge=0, le=10_000_000)
    auto_approve_daily_cap_cents: int = Field(500_000, ge=0, le=100_000_000)
    auto_approve_daily_count: int = Field(10, ge=0, le=1_000)


class InstapayCredentialsResponse(BaseModel):
    """Masked InstaPay config (the IPA itself is the only sensitive bit)."""

    is_configured: bool
    enabled: bool = False
    ipa_masked: str | None = None
    ipa_display_name: str | None = None
    fallback_phone: str | None = None
    auto_approve_threshold_cents: int | None = None
    auto_approve_daily_cap_cents: int | None = None
    auto_approve_daily_count: int | None = None
    last_configured: str | None = None


class SaveKashierCredentialsRequest(BaseModel):
    """Save Kashier gateway credentials for a store."""

    merchant_id: str = Field(..., min_length=3, max_length=100)
    api_key: str = Field(..., min_length=5, max_length=500)
    secret_key: str | None = Field(None, max_length=500)


class KashierCredentialsResponse(BaseModel):
    """Kashier credentials status (masked, never returns real keys)."""

    is_configured: bool
    merchant_id: str | None = None
    api_key_masked: str | None = None
    last_configured: str | None = None


class SaveFawryCredentialsRequest(BaseModel):
    """Save Fawry gateway credentials for a store."""

    merchant_code: str = Field(..., min_length=3, max_length=100)
    security_key: str = Field(..., min_length=10, max_length=500)


class FawryCredentialsResponse(BaseModel):
    """Fawry credentials status (masked, never returns real keys)."""

    is_configured: bool
    merchant_code: str | None = None
    security_key_masked: str | None = None
    last_configured: str | None = None


class SaveFawaterakCredentialsRequest(BaseModel):
    """Save Fawaterak gateway credentials for a store."""

    api_key: str = Field(..., min_length=10, max_length=500)
    vendor_key: str = Field(..., min_length=10, max_length=500)
    environment: str = Field(default="staging", pattern="^(staging|production)$")


class FawaterakCredentialsResponse(BaseModel):
    """Fawaterak credentials status (masked, never returns real keys)."""

    is_configured: bool
    api_key_masked: str | None = None
    vendor_key_masked: str | None = None
    environment: str | None = None
    last_configured: str | None = None


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


# Bosta Shipping Credentials
class SaveBostaCredentialsRequest(BaseModel):
    """Save Bosta shipping credentials for a store."""

    api_key: str = Field(..., min_length=10, max_length=500)
    business_id: str = Field(..., min_length=3, max_length=100)
    webhook_secret: str | None = Field(None, max_length=500)
    auto_create_shipment: bool = False


class BostaCredentialsResponse(BaseModel):
    """Bosta credentials status (masked, never returns real keys)."""

    is_configured: bool
    api_key_masked: str | None = None
    business_id: str | None = None
    auto_create_shipment: bool = False
    last_configured: str | None = None


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


# ============ Customization Settings ============


class CustomizationIdentity(BaseModel):
    """Store identity customization.

    Logo model (Shopify-parity):
      * ``logo_url`` — primary logo (header, light backgrounds)
      * ``logo_dark_url`` — variant for dark surfaces (fallback for footer)
      * ``logo_footer_url`` — explicit footer override (wins over ``logo_dark_url``)
      * ``footer_logo_filter_mode`` — ``none`` (default) | ``white`` | ``invert``
    Widths are pixel numbers; ``0`` means use the theme's default / natural size.
    """

    logo_url: str = ""
    store_name: str = ""
    favicon_url: str = ""
    # Logo variants (footer/dark surfaces)
    logo_footer_url: str = ""
    logo_dark_url: str = ""
    # Accessibility / interaction
    logo_alt_text: str = ""
    logo_link_target: str = "/"
    # Responsive widths — 0 means "use theme default"
    logo_width_desktop: int = 0
    logo_width_mobile: int = 0
    logo_footer_width_desktop: int = 0
    logo_footer_width_mobile: int = 0
    # Spacing / background
    logo_padding: int = 0
    logo_background_color: str = ""
    # Footer-only filter hint (storefront maps to CSS)
    footer_logo_filter_mode: str = "none"


class CustomizationTheme(BaseModel):
    """Store theme customization."""

    base_theme: str = "modern"  # modern | boutique | elegant | skeuomorphic
    primary_color: str = ""
    secondary_color: str = ""
    accent_color: str = ""
    background_color: str = ""
    text_color: str = ""
    button_style: str = "rounded"  # rounded | square | pill
    enable_animations: bool = True
    border_radius: int = 12  # px
    heading_font: str = "Cairo"
    nav_style: str = "floating"  # floating | fixed | sticky


class CustomizationHeader(BaseModel):
    """Store header customization."""

    nav_layout: str = "left-aligned"  # left-aligned | centered
    show_search_bar: bool = True
    show_cart_icon: bool = True
    announcement_text: str = ""
    announcement_color: str = "#4318FF"
    announcement_text_color: str = "#FFFFFF"


class CustomizationHero(BaseModel):
    """Store hero/banner customization."""

    hero_image_url: str = ""
    headline: str = ""
    subtitle: str = ""
    cta_text: str = ""
    cta_link: str = ""


class CustomizationProducts(BaseModel):
    """Store products section customization."""

    layout: str = "grid"  # grid | list
    products_per_row: int = Field(default=3, ge=2, le=4)
    show_price: bool = True
    show_rating: bool = True
    # Image aspect ratio for product cards + PDP main image. Storefront
    # maps these to Tailwind aspect utilities (3/4, 1/1, 4/3). Default
    # "portrait" gives the most visual weight to the photo — fashion/
    # apparel stores want this; homeware stores often prefer "square".
    image_aspect: str = "portrait"  # portrait | square | landscape


class CustomizationSocialLinks(BaseModel):
    """Social media links."""

    facebook: str = ""
    instagram: str = ""
    twitter: str = ""
    whatsapp: str = ""


class CustomizationFooter(BaseModel):
    """Store footer customization."""

    footer_text: str = ""
    social_links: CustomizationSocialLinks = Field(
        default_factory=CustomizationSocialLinks
    )
    show_newsletter: bool = True


class CustomizationNavLink(BaseModel):
    """Navigation link."""

    label: str = ""
    to: str = ""


class CustomizationNavigation(BaseModel):
    """Store navigation customization."""

    links: list[CustomizationNavLink] = Field(default_factory=list)
    show_categories_in_nav: bool = True


class CustomizationLabels(BaseModel):
    """Store label customization."""

    home_title: str = ""
    products_title: str = ""
    checkout_title: str = ""
    order_confirmed_title: str = ""
    cart_empty: str = ""
    search_placeholder: str = ""
    add_to_cart: str = ""
    added_to_cart: str = ""
    continue_shopping: str = ""
    footer_shop_heading: str = ""
    footer_help_heading: str = ""
    footer_contact_heading: str = ""


class CustomizationLayout(BaseModel):
    """Store page layout customization."""

    header_layout: str = "logo-right"
    footer_layout: str = "4-col"
    footer_columns: int = 4
    home_sections: list[str] = Field(
        default_factory=lambda: [
            "hero",
            "categories",
            "new_arrivals",
            "promo",
            "best_sellers",
            "testimonials",
            "newsletter",
        ]
    )
    hero_position: str = "top"
    product_card_style: str = "default"


class CustomizationResponse(BaseModel):
    """Full customization settings response."""

    customization_mode: str = "preset"  # preset | custom
    identity: CustomizationIdentity = Field(default_factory=CustomizationIdentity)
    theme: CustomizationTheme = Field(default_factory=CustomizationTheme)
    header: CustomizationHeader = Field(default_factory=CustomizationHeader)
    hero: CustomizationHero = Field(default_factory=CustomizationHero)
    products: CustomizationProducts = Field(default_factory=CustomizationProducts)
    footer: CustomizationFooter = Field(default_factory=CustomizationFooter)
    navigation: CustomizationNavigation = Field(default_factory=CustomizationNavigation)
    labels: CustomizationLabels = Field(default_factory=CustomizationLabels)
    layout: CustomizationLayout = Field(default_factory=CustomizationLayout)
    is_published: bool = False
    last_published_at: str | None = None
    # V2 section engine fields
    schema_version: int | None = None
    templates: dict[str, Any] | None = None
    # External theme — merchant-edited values keyed by the bundle's
    # settings_schema. Stored under store.theme_settings["external_theme"]
    # ["merchant_settings"], not the customization blob, so it ships in the
    # same payload the storefront already reads from theme_settings.
    external_theme_merchant_settings: dict[str, Any] | None = None


class UpdateCustomizationRequest(BaseModel):
    """Update customization settings request. All fields optional for partial updates.

    Supports both v1 (flat sections) and v2 (section engine) formats.
    When schema_version=2, the ``templates`` field holds per-page TemplateConfig objects.
    V1 fields (hero, products, layout, etc.) are still accepted for backwards compat.
    """

    customization_mode: str | None = None  # preset | custom
    identity: dict[str, Any] | None = None
    theme: dict[str, Any] | None = None
    header: dict[str, Any] | None = None
    hero: dict[str, Any] | None = None
    products: dict[str, Any] | None = None
    footer: dict[str, Any] | None = None
    navigation: dict[str, Any] | None = None
    labels: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    # V2 section engine fields
    schema_version: int | None = None  # 2 for v2 format
    templates: dict[str, Any] | None = None  # { "home": { sections, order } }
    # External theme merchant settings — values keyed by the active external
    # theme's settings_schema. Persisted under
    # ``store.theme_settings["external_theme"]["merchant_settings"]`` so the
    # storefront's existing fetch path picks them up.
    external_theme_merchant_settings: dict[str, Any] | None = None


# Invoice / Tax Settings
class InvoiceSettingsResponse(BaseModel):
    """Invoice and tax settings for ETA compliance."""

    tax_id: str = ""
    name_ar: str = ""
    branch_id: str = "0"
    activity_code: str = "4649"
    governorate: str = ""
    city: str = ""
    street: str = ""
    building_number: str = ""


class UpdateInvoiceSettingsRequest(BaseModel):
    """Update invoice/tax settings."""

    tax_id: str | None = None
    name_ar: str | None = None
    branch_id: str | None = None
    activity_code: str | None = None
    governorate: str | None = None
    city: str | None = None
    street: str | None = None
    building_number: str | None = None


# Combined Store Settings
class StoreSettingsResponse(BaseModel):
    """Combined store settings response."""

    payment: PaymentSettingsResponse
    shipping: ShippingSettingsResponse
    whatsapp: WhatsAppSettingsResponse
