"""Application configuration using Pydantic Settings."""

import logging
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, PostgresDsn, field_validator, model_validator
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

    # Cloudflare DNS automation for per-env store subdomains.
    # Merchant hub stores the env-suffixed subdomain directly (e.g. `yarab-test`),
    # so the service creates `<subdomain>.numueg.app` -> droplet IP without
    # touching the name. Disabled on prod (handled by `* CNAME -> Heroku`).
    cloudflare_api_token: str = ""
    cloudflare_zone_id: str = ""
    droplet_ip: str = ""
    cloudflare_auto_dns_enabled: bool = False

    # API
    api_v1_prefix: str = "/api/v1"
    allowed_hosts: list[str] = ["*"]
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5000"]

    # Beta launch
    beta_mode: bool = True  # Require invite code for store creation

    # API Documentation auth (staging only)
    docs_username: str = ""
    docs_password: str = ""

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "numu"

    # Connection pool (total max = pool_size + max_overflow PER PROCESS)
    # API + Celery + admin each have their own pool — keep under Postgres max_connections
    # Bumped 2026-04-23 after /api/v1/stores/ started returning 500s under
    # analytics + bundles burst load; old 5+10=15 cap exhausted while long
    # range-aggregation queries held connections.
    db_pool_size: int = 10  # Persistent connections maintained in pool
    db_max_overflow: int = 20  # Extra connections allowed beyond pool_size
    db_pool_timeout: int = 30  # Seconds to wait for a connection before error
    db_pool_recycle: int = 1800  # Recycle connections older than 30 minutes
    # Abort any query that runs longer than this (ms). Kills runaway analytics
    # queries before they pin a connection for the whole request timeout.
    db_statement_timeout_ms: int = 30000

    # Celery workers run with their own smaller pool (per process). Heavy
    # background jobs still get bandwidth without stealing from the API. Set
    # process_role=celery on the worker container (NUMU_PROCESS_ROLE env)
    # and the import in connection.py picks up these values.
    celery_db_pool_size: int = 5
    celery_db_max_overflow: int = 5
    # Role identifier — read from NUMU_PROCESS_ROLE at startup. "api" uses
    # the db_* pool sizes above; "celery" uses celery_db_*. Anything else
    # (tests, scripts) falls back to the api sizes.
    process_role: str = "api"

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

    # Storefront cache (store + theme reads). Short TTL is the safety net;
    # explicit invalidation on mutation is the correctness mechanism.
    storefront_cache_enabled: bool = True
    cache_ttl_store_seconds: int = 60
    cache_ttl_theme_seconds: int = 60
    cache_negative_ttl_seconds: int = 10

    # Async analytics ingest (Step 09). When enabled, the storefront
    # /track and /track-event endpoints push to the Celery `analytics`
    # queue and return 202 instead of writing funnel_events synchronously.
    # Flip to False to revert to the legacy synchronous write path.
    analytics_async_enabled: bool = True
    analytics_idempotency_ttl_seconds: int = 86_400  # 24h

    # Prometheus /metrics endpoint (Step 16). Disabled by default; ops
    # enables in staging/prod via env once nginx is configured to gate
    # /metrics behind an IP allowlist (or the deploy environment routes
    # to it from inside the cluster only). `metrics_auth_token`, if set,
    # is required as a Bearer token on /metrics requests — defence in
    # depth alongside the network ACL.
    metrics_endpoint_enabled: bool = False
    metrics_auth_token: str | None = None

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
    credential_encryption_key: str | None = None  # AES key for merchant credentials
    # Secure cookie settings (should be True in production with HTTPS)
    SECURE_COOKIES: bool = False
    SAMESITE_COOKIES: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = (
        None  # Set to your domain in production (e.g., "numu.com")
    )

    # ─── Try-a-Demo flow (Stream 1 of NUMU plan) ──────────────────────
    # Cloudflare Turnstile bot protection. Get keys from
    # https://dash.cloudflare.com/?to=/:account/turnstile
    # Test keys (always pass): site=1x00000000000000000000AA, secret=1x0000000000000000000000000000000AA
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    # Where storefronts live ("{subdomain}.{base}"). Demo storefronts use the same.
    storefront_base_domain: str = "numueg.app"
    # Where the merchant hub is hosted. Used to build the demo redirect URL.
    merchant_hub_url: str = "https://merchant.numueg.app"

    # Secret used to HMAC-hash staff invitation tokens. Must stay stable
    # across restarts — if it changes, all outstanding invitation links break.
    invite_secret: str = Field(default="default-invite-secret")

    @model_validator(mode="after")
    def validate_jwt_keys(self) -> "Settings":
        """Validate that RSA keys are provided when using RS256."""
        # Env files store PEM keys with literal \n — convert to actual newlines.
        if self.jwt_private_key:
            self.jwt_private_key = self.jwt_private_key.replace("\\n", "\n")
        if self.jwt_public_key:
            self.jwt_public_key = self.jwt_public_key.replace("\\n", "\n")

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
        """Validate environment-appropriate secrets and required configuration."""
        is_production = self.environment == "production"
        is_staging = self.environment == "staging"

        if is_production or is_staging:
            # --- Debug mode check ---
            if self.debug and is_production:
                raise ValueError(
                    "CRITICAL: DEBUG must be false in production. "
                    "Set DEBUG=false in your environment."
                )

            # --- Default/weak secrets check ---
            weak_defaults = {
                "dev-only-session-secret-change-in-prod-32chars",
                "change-me-session-secret",
            }
            if self.session_secret_key in weak_defaults:
                raise ValueError(
                    "CRITICAL: SESSION_SECRET_KEY is set to a development default. "
                    "Generate a secure secret: "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )

            # --- Sentry DSN strongly recommended in non-dev ---
            if not self.sentry_dsn:
                logger.warning(
                    "SENTRY_DSN is not set. Error tracking is disabled for %s.",
                    self.environment,
                )

            # --- Required production-only checks ---
            if is_production:
                if not self.cors_origins or self.cors_origins == ["*"]:
                    raise ValueError(
                        "CORS_ORIGINS must be explicitly set to your production domains. "
                        "Wildcard (*) is not allowed in production."
                    )

                # PLATFORM_SECRET_SALT gates phone hashing for the
                # cross-merchant trust network. Without it, every
                # write_network_event silently no-ops (phone_hash is
                # None) and the network table stays empty — the
                # strategic moat becomes marketing copy. Fail loud at
                # startup instead.
                if not self.platform_secret_salt:
                    raise ValueError(
                        "CRITICAL: PLATFORM_SECRET_SALT must be set in production. "
                        "Without it, the cross-merchant trust network cannot record "
                        "events and risk scoring falls back to baseline. "
                        'Generate a secret: python -c "import secrets; '
                        'print(secrets.token_urlsafe(32))"'
                    )

                if not self.resend_api_key:
                    logger.warning(
                        "RESEND_API_KEY is not set. Email delivery will fail in production."
                    )

                if not self.slack_enabled:
                    logger.warning(
                        "SLACK_ENABLED is false. Operational alerts are disabled in production."
                    )

        return self

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 100  # Authenticated general
    rate_limit_anon_requests_per_minute: int = 60  # Anonymous general
    rate_limit_auth_requests_per_minute: int = 5  # Login/register/refresh
    rate_limit_checkout_requests_per_minute: int = 10  # Storefront checkout
    # Load-test bypass: when set, requests carrying header
    # `X-Load-Test-Token: <this value>` skip rate limiting on the GENERAL
    # and TRACKING tiers ONLY. Auth, checkout, and coupon-apply are still
    # rate-limited even with the token — bypassing those would expose
    # credential-stuffing and order-spam vectors. Empty string disables
    # the bypass entirely (the default). Rotate the token regularly and
    # never commit it; set via env in CI only.
    load_test_bypass_token: str = ""

    # Stripe
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None

    # Tap Payments
    tap_secret_key: str | None = None
    tap_publishable_key: str | None = None

    # Meta (Facebook/Instagram) Graph API
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_config_id: str | None = None  # Embedded Signup configuration ID
    meta_graph_api_version: str = "v19.0"
    meta_webhook_verify_token: str | None = None
    meta_login_config_id: str | None = None

    # Omnichannel Inbox
    inbox_realtime_enabled: bool = True

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    # Reverse geocoding (storefront checkout location picker)
    # Point to self-hosted Nominatim in prod (e.g. http://nominatim:8080)
    # or LocationIQ during bootstrap (https://us1.locationiq.com/v1).
    # Leave both unset to disable the feature — checkout still works with manual entry.
    nominatim_url: str | None = None
    locationiq_key: str | None = (
        None  # only needed if nominatim_url points at LocationIQ
    )

    # Google AI Studio (for AI insights & policy generation via Gemini)
    # Uses Google's OpenAI-compatible endpoint so the existing AsyncOpenAI
    # client can be reused without rewriting to the google-genai SDK.
    google_ai_api_key: str | None = None
    google_ai_model: str = "gemini-3.1-flash-lite-preview"
    google_ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    # Resend (Email)
    resend_api_key: str | None = None
    resend_webhook_secret: str | None = None
    resend_forward_to: str = "yahyasheriif@gmail.com"  # Forwards all received emails to this address (for testing)
    email_from_address: str = "noreply@numu.com"
    email_from_name: str = "numu"
    # Absolute base URL used inside email HTML to reference hosted brand
    # assets (logo PNG, etc.). Must be a public HTTPS URL since Gmail
    # strips base64 data: URLs inside <img> tags. The default points at
    # the landing-page nginx location which already serves /numu-logo-*.
    brand_assets_base_url: str = "https://numueg.app"

    # Absolute base URL serving the storefront's static assets (theme
    # preview screenshots under /themes/{slug}/preview.png, etc.). The
    # merchant hub fetches these directly via <img src> from this host,
    # so it must be reachable from the merchant browser. In production
    # this points at the deployed storefront (or its CDN edge); in dev,
    # set to the local Vite host (e.g. http://localhost:5173).
    storefront_assets_base_url: str = "https://numueg.app"

    # Google OAuth
    google_oauth_client_id: str | None = None

    # Google Cloud Vision (InstaPay proof OCR — Phase C). API-key path
    # for v1; service-account JSON via google-auth is the upgrade path
    # if paid traffic justifies the extra dep. Unset → the
    # ``google_vision`` provider is unavailable; admin attempts to
    # assign it 503 cleanly via the DI factory.
    google_vision_api_key: str | None = None

    # HuggingFace Hub access token. Used by the HF-backed OCR
    # providers (DeepSeek / GLM Spaces) to bump our ZeroGPU queue
    # priority. Anonymous calls get rejected after 60s on busy
    # Spaces; a free HF account ($0) is enough to clear that. Unset
    # → calls run anonymously and frequently soft-fail.
    huggingface_token: str | None = None

    # S3-compatible Object Storage (MinIO / Cloudflare R2 / AWS S3)
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str = "numu"
    s3_public_url: str | None = None
    s3_region: str = "us-east-1"

    # Legacy R2 aliases (mapped to s3_* settings)
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
    # Shopify App Integration
    # =========================================================================

    # Shared secret between the Shopify app and this API.
    # Set SHOPIFY_INTERNAL_KEY (or NUMU_API_INTERNAL_KEY) in your .env.
    # Must match NUMU_API_INTERNAL_KEY in the numu-payments-intelligence .env.
    shopify_internal_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "shopify_internal_key",
            "numu_api_internal_key",
            "SHOPIFY_INTERNAL_KEY",
            "NUMU_API_INTERNAL_KEY",
        ),
    )

    # Base URL of the Shopify-app companion (numu-payments-intelligence).
    # Used by the verification-overage relay (backend-004) to POST usage
    # events to the Shopify-app's /api/billing/usage-record endpoint.
    # Default: empty string; must be set in production for backend-004
    # to function.  Example: "https://shopify.numu.app".
    shopify_app_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "shopify_app_url",
            "SHOPIFY_APP_URL",
        ),
    )

    # Secret salt for HMAC-SHA256 hashing of phone numbers in the
    # network_reputation table.  Must be a 256-bit (32-byte) hex string.
    # NEVER store in code or database — env-only.
    platform_secret_salt: str = Field(
        default="",
        validation_alias=AliasChoices(
            "platform_secret_salt",
            "PLATFORM_SECRET_SALT",
        ),
    )
    # Previous salt for rotation — set this to the OLD salt value when
    # rotating, so lookups check both hashes during the transition window.
    platform_secret_salt_old: str = Field(
        default="",
        validation_alias=AliasChoices(
            "platform_secret_salt_old",
            "PLATFORM_SECRET_SALT_OLD",
        ),
    )

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

    # Kashier (Egyptian Payment Gateway)
    kashier_mid: str | None = None  # Merchant ID (MID-xx-xx)
    kashier_api_key: str | None = None  # API key (also used as HMAC secret)
    kashier_mode: str = "test"  # "test" or "live"
    kashier_currency: str = "EGP"  # Default currency

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

    # Mylerz Shipping (Egyptian Courier)
    mylerz_api_key: str | None = None
    mylerz_merchant_id: str | None = None
    mylerz_base_url: str = "https://api.mylerz.com/api"
    mylerz_webhook_secret: str | None = None

    # J&T Express Shipping (Egyptian Courier)
    jt_api_key: str | None = None
    jt_customer_code: str | None = None
    jt_base_url: str = "https://openapi.jtexpress-eg.com/api"
    jt_webhook_secret: str | None = None

    # WhatsApp Business API
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_webhook_verify_token: str | None = None
    whatsapp_app_secret: str | None = None  # For webhook signature verification
    whatsapp_enabled: bool = False
    whatsapp_business_api_version: str = "v21.0"

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
