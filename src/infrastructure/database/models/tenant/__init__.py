"""Tenant-scoped database models.

These models use tenant_id discriminator for multi-tenancy.
They include all e-commerce related data that is specific to a store.
"""

from src.infrastructure.database.models.tenant.abandoned_checkout import (
    AbandonedCheckoutModel,
)
from src.infrastructure.database.models.tenant.address import CustomerAddressModel
from src.infrastructure.database.models.tenant.analytics_rollup import (
    AnalyticsDailyRollupModel,
)
from src.infrastructure.database.models.tenant.automation_log import AutomationLogModel
from src.infrastructure.database.models.tenant.automation_rule import (
    AutomationRuleModel,
)
from src.infrastructure.database.models.tenant.category import CategoryModel
from src.infrastructure.database.models.tenant.configuration import (
    AuditAction,
    ConfigurationRequest,
    CredentialAuditLog,
    RequestPriority,
    RequestStatus,
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.coupon import CouponModel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.customer_touch import (
    CustomerTouchModel,
)
from src.infrastructure.database.models.tenant.email_log import EmailLogModel
from src.infrastructure.database.models.tenant.email_template import EmailTemplateModel
from src.infrastructure.database.models.tenant.funnel_event import FunnelEventModel
from src.infrastructure.database.models.tenant.gift_card import (
    GiftCardModel,
    GiftCardTransactionModel,
)
from src.infrastructure.database.models.tenant.instapay_intent import (
    InstapayIntentModel,
)
from src.infrastructure.database.models.tenant.inventory_level import (
    InventoryLevelModel,
)
from src.infrastructure.database.models.tenant.inventory_transfer import (
    InventoryTransferModel,
)
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.database.models.tenant.location import LocationModel
from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.database.models.tenant.meta_event_log import MetaEventLogModel
from src.infrastructure.database.models.tenant.network_contribution_log import (
    NetworkContributionLogModel,
)
from src.infrastructure.database.models.tenant.network_reputation import (
    NetworkReputationModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.order_activity import OrderActivityModel
from src.infrastructure.database.models.tenant.otp_code import OtpCodeModel
from src.infrastructure.database.models.tenant.page_view import PageViewModel
from src.infrastructure.database.models.tenant.payment_link_session import (
    PaymentLinkSessionModel,
)
from src.infrastructure.database.models.tenant.payment_proof import PaymentProofModel
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.product_review import ProductReviewModel
from src.infrastructure.database.models.tenant.promotion import (
    PromotionDismissalModel,
    PromotionDisplayModel,
    PromotionEventDailyModel,
    PromotionEventModel,
    PromotionModel,
    PromotionTargetModel,
    PromotionTranslationModel,
)
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.database.models.tenant.shipping_rate import ShippingRateModel
from src.infrastructure.database.models.tenant.shipping_zone import ShippingZoneModel
from src.infrastructure.database.models.tenant.shipping_zone_governorate import (
    ShippingZoneGovernorateModel,
)
from src.infrastructure.database.models.tenant.shopify_app_settings import (
    ShopifyAppSettingsModel,
)
from src.infrastructure.database.models.tenant.shopify_installation import (
    ShopifyInstallationModel,
)
from src.infrastructure.database.models.tenant.short_link import ShortLinkModel
from src.infrastructure.database.models.tenant.social_connection import (
    SocialConnectionModel,
)
from src.infrastructure.database.models.tenant.social_post import SocialPostModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.theme import (
    StoreThemeModel,
    ThemeAssetModel,
    ThemeModel,
    ThemeVersionModel,
)
from src.infrastructure.database.models.tenant.upsell_rule import UpsellRuleModel
from src.infrastructure.database.models.tenant.variant import VariantModel
from src.infrastructure.database.models.tenant.webhook import (
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
)
from src.infrastructure.database.models.tenant.whatsapp_campaign import (
    WhatsAppCampaignModel,
    WhatsAppCampaignRecipientModel,
)
from src.infrastructure.database.models.tenant.whatsapp_conversation import (
    WhatsAppConversationModel,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)

__all__ = [
    "AbandonedCheckoutModel",
    "AnalyticsDailyRollupModel",
    "AutomationLogModel",
    "AutomationRuleModel",
    "CategoryModel",
    "CouponModel",
    "EmailLogModel",
    "EmailTemplateModel",
    "FunnelEventModel",
    "GiftCardModel",
    "GiftCardTransactionModel",
    "CustomerAddressModel",
    "CustomerModel",
    "CustomerTouchModel",
    "InventoryLevelModel",
    "InventoryTransferModel",
    "InvoiceModel",
    "LocationModel",
    "MarketingCampaignModel",
    "MessageLogModel",
    "MetaEventLogModel",
    "NetworkContributionLogModel",
    "NetworkReputationModel",
    "InstapayIntentModel",
    "OrderModel",
    "OrderActivityModel",
    "PageViewModel",
    "PaymentLinkSessionModel",
    "PaymentProofModel",
    "PaymentTransactionModel",
    "RiskAssessmentModel",
    "ShopifyAppSettingsModel",
    "ShopifyInstallationModel",
    # Configuration models
    "ConfigurationRequest",
    "ServiceCredential",
    "CredentialAuditLog",
    "ServiceType",
    "ServiceName",
    "RequestStatus",
    "RequestPriority",
    "AuditAction",
    "ProductModel",
    "ProductReviewModel",
    "VariantModel",
    "PromotionModel",
    "PromotionDismissalModel",
    "PromotionDisplayModel",
    "PromotionEventDailyModel",
    "PromotionEventModel",
    "PromotionTargetModel",
    "PromotionTranslationModel",
    "RefundModel",
    "UpsellRuleModel",
    "ShipmentModel",
    "ShippingRateModel",
    "ShippingZoneModel",
    "ShippingZoneGovernorateModel",
    "ShortLinkModel",
    "StoreModel",
    "SocialConnectionModel",
    "SocialPostModel",
    "ThemeModel",
    "ThemeVersionModel",
    "StoreThemeModel",
    "ThemeAssetModel",
    "WebhookSubscriptionModel",
    "WebhookDeliveryLogModel",
    # WhatsApp models
    "WhatsAppTemplateModel",
    "WhatsAppConversationModel",
    "WhatsAppCampaignModel",
    "WhatsAppCampaignRecipientModel",
    "OtpCodeModel",
]
