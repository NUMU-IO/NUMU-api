"""Tenant-scoped database models.

These models use tenant_id discriminator for multi-tenancy.
They include all e-commerce related data that is specific to a store.
"""

from src.infrastructure.database.models.tenant.address import CustomerAddressModel
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
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.database.models.tenant.network_contribution_log import (
    NetworkContributionLogModel,
)
from src.infrastructure.database.models.tenant.network_reputation import (
    NetworkReputationModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_link_session import (
    PaymentLinkSessionModel,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.database.models.tenant.shopify_app_settings import (
    ShopifyAppSettingsModel,
)
from src.infrastructure.database.models.tenant.shopify_installation import (
    ShopifyInstallationModel,
)
from src.infrastructure.database.models.tenant.social_connection import (
    SocialConnectionModel,
)
from src.infrastructure.database.models.tenant.social_post import SocialPostModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.upsell_rule import UpsellRuleModel
from src.infrastructure.database.models.tenant.webhook import (
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
)

__all__ = [
    "AutomationLogModel",
    "AutomationRuleModel",
    "CategoryModel",
    "CouponModel",
    "CustomerAddressModel",
    "CustomerModel",
    "InvoiceModel",
    "MessageLogModel",
    "NetworkContributionLogModel",
    "NetworkReputationModel",
    "OrderModel",
    "PaymentLinkSessionModel",
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
    "RefundModel",
    "UpsellRuleModel",
    "ShipmentModel",
    "StoreModel",
    "SocialConnectionModel",
    "SocialPostModel",
    "WebhookSubscriptionModel",
    "WebhookDeliveryLogModel",
]
