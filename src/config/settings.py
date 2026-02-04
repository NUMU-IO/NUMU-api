"""Application configuration using Pydantic Settings."""

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "NUMU API"
    app_version: str = "0.1.0"
    debug: bool = True
    environment: Literal["development", "staging", "production"] = "development"

    # API
    api_v1_prefix: str = "/api/v1"
    allowed_hosts: list[str] = ["*"]
    cors_origins: list[str] = ["http://localhost:3000"]

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "numu"

    @property
    def database_url(self) -> str:
        """Construct async PostgreSQL connection URL."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def database_url_sync(self) -> str:
        """Construct sync PostgreSQL connection URL (for Alembic)."""
        return str(
            PostgresDsn.build(
                scheme="postgresql",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL."""
        password_part = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # JWT Authentication (RS256 asymmetric signing)
    jwt_private_key: str = Field(default="")
    jwt_public_key: str = Field(default="")
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Legacy HS256 secret (kept for backwards-compatible token verification during migration)
    jwt_secret_key: str = Field(default="")

    # Session (separate from JWT for admin panel cookies)
    # Default is 32+ chars for development, MUST be changed in production
    session_secret_key: str = Field(
        default="dev-only-session-secret-change-in-prod-32chars"
    )

    @model_validator(mode="after")
    def validate_jwt_keys(self) -> "Settings":
        """Validate that RSA keys are provided when using RS256."""
        if self.jwt_algorithm == "RS256":
            if not self.jwt_private_key:
                raise ValueError(
                    "JWT_PRIVATE_KEY is required for RS256 algorithm. "
                    "Generate keys with: python scripts/generate_jwt_keys.py"
                )
            if not self.jwt_public_key:
                raise ValueError(
                    "JWT_PUBLIC_KEY is required for RS256 algorithm. "
                    "Generate keys with: python scripts/generate_jwt_keys.py"
                )
        return self

    @field_validator("session_secret_key")
    @classmethod
    def validate_session_secret(cls, v: str) -> str:
        """Validate session secret key."""
        if len(v) < 32:
            raise ValueError(
                "SESSION_SECRET_KEY must be at least 32 characters for security. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Validate that default secrets are not used in production."""
        if self.environment == "production":
            if self.session_secret_key == "change-me-session-secret":
                raise ValueError(
                    "CRITICAL: SESSION_SECRET_KEY must be changed from default in production! "
                    "Set a secure value in your environment or .env file."
                )
            if self.debug:
                logger.warning(
                    "WARNING: Debug mode is enabled in production. "
                    "This exposes sensitive information and should be disabled."
                )
        return self

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 60
    rate_limit_auth_requests_per_minute: int = 5

    # Stripe
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None

    # Tap Payments
    tap_secret_key: str | None = None
    tap_publishable_key: str | None = None

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    # Resend (Email)
    resend_api_key: str | None = None
    email_from_address: str = "noreply@numu.com"
    email_from_name: str = "numu"

    # Cloudflare R2
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "numu"
    r2_public_url: str | None = None

    # Database Backups (uses R2 credentials for storage)
    r2_backup_bucket_name: str = "numu-db-backups"
    backup_retention_days: int = 30

    # Shippo
    shippo_api_key: str | None = None

    # =========================================================================
    # Egyptian Market Integrations
    # =========================================================================

    # Paymob (Egyptian Payment Gateway)
    paymob_api_key: str | None = None
    paymob_integration_id: str | None = None  # Card payments integration
    paymob_iframe_id: str | None = None
    paymob_hmac_secret: str | None = None  # Webhook verification
    paymob_wallet_integration_id: str | None = None  # Mobile wallets

    # Fawry (Retail Pay Points)
    fawry_merchant_code: str | None = None
    fawry_security_key: str | None = None
    fawry_base_url: str = (
        "https://atfawry.fawrystaging.com"  # Use production URL in prod
    )

    # Cash on Delivery (COD)
    cod_enabled: bool = True
    cod_fee_percentage: float = 0.0  # Optional COD fee (0-100)
    cod_fee_flat: int = 0  # Flat COD fee in cents
    cod_max_amount: int = 1000000  # Max COD amount in cents (10,000 EGP)
    cod_min_amount: int = 0  # Min COD amount in cents

    # Bosta Shipping (Egyptian Courier)
    bosta_api_key: str | None = None
    bosta_business_id: str | None = None
    bosta_base_url: str = "https://app.bosta.co/api/v2"
    bosta_webhook_secret: str | None = None

    # WhatsApp Business API
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_webhook_verify_token: str | None = None
    whatsapp_app_secret: str | None = None  # For webhook signature verification
    whatsapp_enabled: bool = False

    # Egyptian Tax Authority (ETA) E-Invoicing
    eta_client_id: str | None = None
    eta_client_secret: str | None = None
    eta_base_url: str = "https://api.invoicing.eta.gov.eg/api/v1"
    eta_token_url: str = "https://id.eta.gov.eg/connect/token"
    eta_activity_code: str = "4649"  # Wholesale of other household goods
    eta_enabled: bool = False

    # Localization
    default_locale: str = "en"
    supported_locales: list[str] = ["en", "ar"]

    # =========================================================================
    # Observability (Sentry, Structured Logging)
    # =========================================================================

    # Sentry
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.1  # 10% of transactions
    sentry_profiles_sample_rate: float = 0.1  # 10% of profiled transactions
    sentry_send_default_pii: bool = False  # Set True to capture user emails, IPs

    # Structured Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" for production, "console" for development

    # =========================================================================
    # Slack Alerting
    # =========================================================================

    slack_enabled: bool = False
    slack_environment: str = "development"  # Used in alert messages

    # Webhooks (one per channel)
    slack_webhook_critical: str | None = None
    slack_webhook_payments: str | None = None
    slack_webhook_fraud: str | None = None
    slack_webhook_shipping: str | None = None
    slack_webhook_infra: str | None = None
    slack_webhook_business: str | None = None
    slack_webhook_dev: str | None = None  # For non-prod alerts

    # Bot token (for mentions and interactive alerts)
    slack_bot_token: str | None = None

    # User IDs for escalation mentions
    slack_user_oncall: str | None = None
    slack_user_fraud_lead: str | None = None
    slack_user_infra_lead: str | None = None
    slack_user_payments_lead: str | None = None

    # Channel IDs (for bot API calls if needed)
    slack_channel_critical: str | None = None
    slack_channel_fraud: str | None = None

    # Behavior settings
    slack_force_dev_channel: bool = False  # Force all alerts to dev channel (non-prod)

    # Rate limiting
    slack_cooldown_critical_seconds: int = 300  # 5 minutes
    slack_cooldown_warn_seconds: int = 1800  # 30 minutes
    slack_cooldown_info_seconds: int = 14400  # 4 hours

    def get_slack_webhook(self, channel: str) -> str | None:
        """Get webhook URL for a channel, respecting force_dev_channel setting."""
        if self.slack_force_dev_channel or self.environment != "production":
            return self.slack_webhook_dev
        return getattr(self, f"slack_webhook_{channel}", None)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Export settings instance for convenience
settings = get_settings()
