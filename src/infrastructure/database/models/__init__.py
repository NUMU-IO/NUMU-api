"""Database models module."""

from src.infrastructure.agent.knowledge.models import (  # noqa: E402
    NumuKnowledgeChunkModel,
    NumuKnowledgeDocModel,
    TenantKnowledgeChunkModel,
    TenantKnowledgeDocModel,
    TenantNoteModel,
)

# NUMU Agent (merchant copilot) — imported here so the tables register on
# Base.metadata for Alembic autogenerate consistency (migration is manual).
from src.infrastructure.agent.persistence.models import (  # noqa: E402
    AgentActionProposalModel,
    AgentAuditLogModel,
    AgentConversationModel,
    AgentTurnModel,
)
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

# Public schema models
from src.infrastructure.database.models.public import (
    CapiEventModel,
    CatalogMappingModel,
    ChannelConnectionModel,
    ChannelMessageModel,
    FeedbackModel,
    MerchantWalletModel,
    MessageThreadModel,
    StoreOnboardingModel,
    TenantModel,
    UserModel,
    WaitlistModel,
    WalletTopupIntentModel,
    WalletTopupProofModel,
    WalletTransactionModel,
    WebhookEventModel,
    WhatsAppAccessRequestModel,
)

# Access-control graph (public schema). UserModel and TenantMembershipModel have
# relationships through the ``membership_roles`` M2M, so these must be imported
# here for the SQLAlchemy mapper graph to resolve from the package alone (e.g. in
# scripts/create_superuser.py) — not only when the whole app is loaded.
from src.infrastructure.database.models.public.membership_override import (
    MembershipRoleModel,  # noqa: F401 -- association table; imported for mapper
)
from src.infrastructure.database.models.public.role import RoleModel
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)

# Tenant-scoped models (with tenant_id discriminator)
from src.infrastructure.database.models.tenant import (
    AutomationLogModel,
    AutomationRuleModel,
    CategoryModel,
    CouponModel,
    CustomerAddressModel,
    CustomerModel,
    InstapayIntentModel,
    InvoiceModel,
    MerchantSignalModel,
    MessageLogModel,
    MetaEventLogModel,
    MetafieldDefinitionModel,
    MetafieldValueModel,
    MetricTargetModel,
    NetworkContributionLogModel,
    NetworkReputationModel,
    OrderModel,
    PageViewModel,
    PaymentLinkSessionModel,
    PaymentProofModel,
    PaymentTransactionModel,
    ProductModel,
    PromotionDismissalModel,
    PromotionDisplayModel,
    PromotionEventDailyModel,
    PromotionEventModel,
    PromotionModel,
    PromotionTargetModel,
    PromotionTranslationModel,
    RefundModel,
    RiskAssessmentModel,
    ShipmentModel,
    ShopifyAppSettingsModel,
    ShopifyInstallationModel,
    SocialConnectionModel,
    SocialPostModel,
    StoreModel,
    StoreThemeModel,
    ThemeAssetModel,
    ThemeErrorEventModel,
    ThemeModel,
    ThemeVersionModel,
    TikTokEventLogModel,
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
    WhatsAppTemplateModel,
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
    "WhatsAppAccessRequestModel",
    # Merchant wallet (payg)
    "MerchantWalletModel",
    "WalletTransactionModel",
    "WalletTopupIntentModel",
    "WalletTopupProofModel",
    "AuditLogModel",
    "TenantMembershipModel",
    "RoleModel",
    # Omnichannel
    "ChannelConnectionModel",
    "MessageThreadModel",
    "ChannelMessageModel",
    "WhatsAppTemplateModel",
    "CatalogMappingModel",
    "WebhookEventModel",
    "CapiEventModel",
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
    "MetaEventLogModel",
    "MetafieldDefinitionModel",
    "MetafieldValueModel",
    "MerchantSignalModel",
    "MetricTargetModel",
    "TikTokEventLogModel",
    "NetworkContributionLogModel",
    "NetworkReputationModel",
    "InstapayIntentModel",
    "OrderModel",
    "PageViewModel",
    "PaymentLinkSessionModel",
    "PaymentProofModel",
    "PaymentTransactionModel",
    "PromotionModel",
    "PromotionDismissalModel",
    "PromotionDisplayModel",
    "PromotionEventDailyModel",
    "PromotionEventModel",
    "PromotionTargetModel",
    "PromotionTranslationModel",
    "RefundModel",
    "ShipmentModel",
    "RiskAssessmentModel",
    "ShopifyAppSettingsModel",
    "ShopifyInstallationModel",
    "SocialConnectionModel",
    "SocialPostModel",
    "WebhookSubscriptionModel",
    "WebhookDeliveryLogModel",
    "ThemeModel",
    "ThemeVersionModel",
    "StoreThemeModel",
    "ThemeAssetModel",
    "ThemeErrorEventModel",
    # NUMU Agent
    "AgentConversationModel",
    "AgentTurnModel",
    "AgentActionProposalModel",
    "AgentAuditLogModel",
    "NumuKnowledgeDocModel",
    "NumuKnowledgeChunkModel",
    "TenantKnowledgeDocModel",
    "TenantKnowledgeChunkModel",
    "TenantNoteModel",
]
