"""Repository implementations module."""

from src.infrastructure.repositories.address_repository import CustomerAddressRepository
from src.infrastructure.repositories.analytics_rollup_repository import (
    AnalyticsRollupRepository,
)
from src.infrastructure.repositories.cart_repository import RedisCartRepository
from src.infrastructure.repositories.catalog_mapping_repository import (
    CatalogMappingRepositoryImpl,
)
from src.infrastructure.repositories.category_repository import CategoryRepository
from src.infrastructure.repositories.channel_connection_repository import (
    ChannelConnectionRepositoryImpl,
)
from src.infrastructure.repositories.channel_message_repository import (
    ChannelMessageRepositoryImpl,
)
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.credential_repository import CredentialRepository
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.email_log_repository import (
    EmailLogRepository,
    EmailLogRepositoryImpl,
)
from src.infrastructure.repositories.email_template_repository import (
    EmailTemplateRepository,
    EmailTemplateRepositoryImpl,
)
from src.infrastructure.repositories.feedback_repository import FeedbackRepository
from src.infrastructure.repositories.invoice_repository import InvoiceRepository
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)
from src.infrastructure.repositories.message_log_repository import MessageLogRepository
from src.infrastructure.repositories.message_thread_repository import (
    MessageThreadRepositoryImpl,
)
from src.infrastructure.repositories.onboarding_repository import OnboardingRepository
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.product_bundle_repository import (
    ProductBundleRepository,
)
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.product_review_repository import (
    ProductReviewRepository,
)
from src.infrastructure.repositories.refund_repository import RefundRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository
from src.infrastructure.repositories.shipping_zone_repository import (
    ShippingZoneRepository,
)
from src.infrastructure.repositories.social_connection_repository import (
    SocialConnectionRepository,
)
from src.infrastructure.repositories.social_post_repository import SocialPostRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.repositories.theme_customization_version_repository import (
    ThemeCustomizationVersionRepository,
)
from src.infrastructure.repositories.theme_repository import ThemeRepository
from src.infrastructure.repositories.theme_version_repository import (
    ThemeVersionRepository,
)
from src.infrastructure.repositories.two_factor_repository import TwoFactorRepository
from src.infrastructure.repositories.upsell_rule_repository import UpsellRuleRepository
from src.infrastructure.repositories.user_repository import UserRepository
from src.infrastructure.repositories.waitlist_repository import WaitlistRepository
from src.infrastructure.repositories.webhook_delivery_log_repository import (
    WebhookDeliveryLogRepository,
)
from src.infrastructure.repositories.webhook_event_repository import (
    WebhookEventRepositoryImpl,
)
from src.infrastructure.repositories.webhook_subscription_repository import (
    WebhookSubscriptionRepository,
)
from src.infrastructure.repositories.whatsapp_template_repository import (
    WhatsAppTemplateRepositoryImpl,
)

__all__ = [
    "AnalyticsRollupRepository",
    "UserRepository",
    "StoreRepository",
    "StoreThemeRepository",
    "ThemeRepository",
    "MarketplaceRepository",
    "ThemeCustomizationVersionRepository",
    "ThemeVersionRepository",
    "TwoFactorRepository",
    "CategoryRepository",
    "CouponRepository",
    "CredentialRepository",
    "CustomerRepository",
    "CustomerAddressRepository",
    "EmailLogRepository",
    "EmailLogRepositoryImpl",
    "EmailTemplateRepository",
    "EmailTemplateRepositoryImpl",
    "FeedbackRepository",
    "InvoiceRepository",
    "MessageLogRepository",
    "OnboardingRepository",
    "OrderRepository",
    "PageViewRepository",
    "ProductBundleRepository",
    "ProductRepository",
    "ProductReviewRepository",
    "RedisCartRepository",
    "RefundRepository",
    "ShipmentRepository",
    "ShippingZoneRepository",
    "SocialConnectionRepository",
    "SocialPostRepository",
    "WaitlistRepository",
    "UpsellRuleRepository",
    "WebhookSubscriptionRepository",
    "WebhookDeliveryLogRepository",
    # Omnichannel
    "ChannelConnectionRepositoryImpl",
    "MessageThreadRepositoryImpl",
    "ChannelMessageRepositoryImpl",
    "WhatsAppTemplateRepositoryImpl",
    "CatalogMappingRepositoryImpl",
    "WebhookEventRepositoryImpl",
]
