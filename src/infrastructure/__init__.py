"""Infrastructure layer module.

This module contains implementations of external concerns:
- Database: Connection, ORM models, repositories
- External Services: Payment, Email, Storage, AI, Shipping
- Cache: Redis implementation
- Messaging: Background tasks (Celery)
- Slack: Alerting and notifications
"""

from src.infrastructure.cache import RedisCacheService
from src.infrastructure.database import (
    AsyncSessionLocal,
    Base,
    close_db,
    engine,
    get_db_session,
    init_db,
)
from src.infrastructure.external_services import (
    CloudflareR2StorageService,
    OpenAIService,
    PasswordService,
    ResendEmailService,
    StripePaymentService,
    TokenService,
    password_service,
    token_service,
)
from src.infrastructure.repositories import (
    ProductRepository,
    StoreRepository,
    UserRepository,
)
from src.infrastructure.slack import (
    AlertChannel,
    AlertSeverity,
    SlackAlert,
    SlackAlertService,
    slack_alert_service,
)

__all__ = [
    # Database
    "Base",
    "engine",
    "AsyncSessionLocal",
    "get_db_session",
    "init_db",
    "close_db",
    # Repositories
    "UserRepository",
    "StoreRepository",
    "ProductRepository",
    # Services
    "PasswordService",
    "password_service",
    "TokenService",
    "token_service",
    "StripePaymentService",
    "ResendEmailService",
    "OpenAIService",
    "CloudflareR2StorageService",
    "RedisCacheService",
    # Slack Alerting
    "AlertSeverity",
    "AlertChannel",
    "SlackAlert",
    "SlackAlertService",
    "slack_alert_service",
]
