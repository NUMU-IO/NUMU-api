"""Database models module."""

from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

# Public schema models
from src.infrastructure.database.models.public import (
    FeedbackModel,
    StoreOnboardingModel,
    TenantModel,
    UserModel,
    WaitlistModel,
)

# Tenant-scoped models (with tenant_id discriminator)
from src.infrastructure.database.models.tenant import (
    AutomationLogModel,
    AutomationRuleModel,
    CategoryModel,
    CouponModel,
    CustomerAddressModel,
    CustomerModel,
    InvoiceModel,
    MessageLogModel,
    OrderModel,
    PaymentTransactionModel,
    ProductModel,
    RefundModel,
    RiskAssessmentModel,
    ShipmentModel,
    ShopifyAppSettingsModel,
    ShopifyInstallationModel,
    StoreModel,
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
)

__all__ = [
    "TimestampMixin",
    "UUIDMixin",
    "TenantMixin",
    # Public
    "FeedbackModel",
    "StoreOnboardingModel",
    "TenantModel",
    "UserModel",
    "WaitlistModel",
    "AuditLogModel",
    # Tenant-scoped
    "AutomationLogModel",
    "AutomationRuleModel",
    "StoreModel",
    "ProductModel",
    "CategoryModel",
    "CouponModel",
    "CustomerModel",
    "CustomerAddressModel",
    "InvoiceModel",
    "MessageLogModel",
    "OrderModel",
    "PaymentTransactionModel",
    "RefundModel",
    "ShipmentModel",
    "RiskAssessmentModel",
    "ShopifyAppSettingsModel",
    "ShopifyInstallationModel",
    "WebhookSubscriptionModel",
    "WebhookDeliveryLogModel",
]
