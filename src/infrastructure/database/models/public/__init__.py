"""Public schema database models.

These models live in the 'public' PostgreSQL schema and are shared across all tenants.
They include:
- TenantModel: The tenant registry table
- UserModel: User accounts (global SSO)
- WaitlistModel: Beta launch waitlist
- FeedbackModel: Beta merchant feedback
"""

from src.infrastructure.database.models.public.feedback import FeedbackModel
from src.infrastructure.database.models.public.onboarding import StoreOnboardingModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.waitlist import WaitlistModel

__all__ = [
    "FeedbackModel",
    "StoreOnboardingModel",
    "TenantModel",
    "UserModel",
    "WaitlistModel",
]
