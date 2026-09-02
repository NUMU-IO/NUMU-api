"""Public schema database models.

These models live in the 'public' PostgreSQL schema and are shared across all tenants.
They include:
- TenantModel: The tenant registry table
- UserModel: User accounts (global SSO)
- WaitlistModel: Beta launch waitlist
- FeedbackModel: Beta merchant feedback
"""

from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.currency_rate import CurrencyRateModel
from src.infrastructure.database.models.public.customizer_undo_entry import (
    CustomizerUndoEntryModel,
)
from src.infrastructure.database.models.public.feedback import FeedbackModel
from src.infrastructure.database.models.public.merchant_business_profile import (
    MerchantBusinessProfileModel,
)
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.omnichannel import (
    CapiEventModel,
    CatalogMappingModel,
    ChannelConnectionModel,
    ChannelMessageModel,
    MessageThreadModel,
    WebhookEventModel,
)
from src.infrastructure.database.models.public.onboarding import StoreOnboardingModel
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)
from src.infrastructure.database.models.public.platform_benchmark import (
    PlatformBenchmarkModel,
)
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
    SubscriptionPaymentProofModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.theme_admin_config import (
    ThemeAdminConfigModel,
)
from src.infrastructure.database.models.public.two_factor import TwoFactorAuthModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.waitlist import WaitlistModel
from src.infrastructure.database.models.public.wallet import (
    MerchantWalletModel,
    WalletTopupIntentModel,
    WalletTopupProofModel,
    WalletTransactionModel,
)
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessRequestModel,
    WhatsAppAccessStatus,
)

__all__ = [
    "AppModel",
    "AppInstallationModel",
    "CurrencyRateModel",
    "CustomizerUndoEntryModel",
    "FeedbackModel",
    "PaymentReconciliationRunModel",
    "PersonalAccessTokenModel",
    "ReconciliationMismatchModel",
    "StoreOnboardingModel",
    "PlatformBenchmarkModel",
    "TenantModel",
    "ThemeAdminConfigModel",
    "TwoFactorAuthModel",
    "UserModel",
    "MerchantBusinessProfileModel",
    "MerchantLeadModel",
    "WaitlistModel",
    # Merchant wallet (pay-as-you-go)
    "MerchantWalletModel",
    "WalletTransactionModel",
    "WalletTopupIntentModel",
    "WalletTopupProofModel",
    # Subscription payments (InstaPay)
    "SubscriptionPaymentIntentModel",
    "SubscriptionPaymentProofModel",
    "WhatsAppAccessRequestModel",
    "WhatsAppAccessStatus",
    # Omnichannel
    "ChannelConnectionModel",
    "MessageThreadModel",
    "ChannelMessageModel",
    "CatalogMappingModel",
    "WebhookEventModel",
    "CapiEventModel",
]
