"""Public schema database models.

These models live in the 'public' PostgreSQL schema and are shared across all tenants.
They include:
- TenantModel: The tenant registry table
- UserModel: User accounts (global SSO)
- WaitlistModel: Beta launch waitlist
- FeedbackModel: Beta merchant feedback
"""

from src.infrastructure.database.models.public.feedback import FeedbackModel
from src.infrastructure.database.models.public.omnichannel import (
    CapiEventModel,
    CatalogMappingModel,
    ChannelConnectionModel,
    ChannelMessageModel,
    MessageThreadModel,
    WebhookEventModel,
)
from src.infrastructure.database.models.public.onboarding import StoreOnboardingModel
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.theme_admin_config import (
    ThemeAdminConfigModel,
)
from src.infrastructure.database.models.public.two_factor import TwoFactorAuthModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.waitlist import WaitlistModel

__all__ = [
    "FeedbackModel",
    "PaymentReconciliationRunModel",
    "ReconciliationMismatchModel",
    "StoreOnboardingModel",
    "TenantModel",
    "ThemeAdminConfigModel",
    "TwoFactorAuthModel",
    "UserModel",
    "WaitlistModel",
    # Omnichannel
    "ChannelConnectionModel",
    "MessageThreadModel",
    "ChannelMessageModel",
    "CatalogMappingModel",
    "WebhookEventModel",
    "CapiEventModel",
]
