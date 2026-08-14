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
    # Cloudflare for SaaS — the Fallback Origin hostname merchants CNAME their
    # custom domain to. Set once in the CF dashboard; the storefront serves it.
    custom_domain_fallback_target: str = "origin.numueg.app"

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

    # RLS enforcement (gated). When set, the REQUEST engine connects as this
    # NON-SUPERUSER role so Row-Level Security actually applies (a superuser
    # bypasses every policy). Migrations still run as postgres_user (they need
    # DDL). Unset by default → behaves exactly as before (superuser, RLS inert).
    # ⚠️ Only set these AFTER validating every tenant-touching path sets tenant
    # context or app.rls_bypass — see docs/REports/RLS-enforcement.md. Flipping
    # blind blanks stores whose queries run without context.
    db_app_user: str | None = None
    db_app_password: str | None = None

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

    # SSL/TLS for managed Postgres providers (e.g. Supabase Supavisor pooler).
    # Empty/unset → plaintext, which is what the local Docker container wants.
    # asyncpg (and the SQLAlchemy asyncpg dialect) takes an `ssl` connect-arg,
    # NOT libpq's `sslmode` query param, so a managed DB needs this rather than
    # a `?sslmode=` URL suffix. Supabase: set "require" (encrypt, no cert check)
    # or "verify-full" with `postgres_ssl_root_cert` pointing at its CA bundle.
    postgres_sslmode: str = ""
    postgres_ssl_root_cert: str = ""

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
    def object_storage_configured(self) -> bool:
        """True when real S3/R2 object-storage credentials are present.

        Single source of truth shared by the storage factory
        (``_get_storage``) and the dev ``/uploads`` static mount, so the two
        never disagree about whether object storage is available. When this is
        ``False`` the app uses ``LocalStorageService`` and serves files from
        the local ``/uploads`` mount. We require the full credential triple — a
        partially-filled or placeholder config (e.g. a Docker-only ``minio``
        endpoint with no real secret) counts as *not* configured, which is what
        previously made the factory pick the S3 client and 500 on every upload.
        """
        has_s3 = bool(
            self.s3_endpoint_url and self.s3_access_key_id and self.s3_secret_access_key
        )
        has_r2 = bool(
            self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key
        )
        return has_s3 or has_r2

    def asyncpg_ssl(self):  # type: ignore[no-untyped-def]
        """Build an ssl.SSLContext for asyncpg, or None when SSL is disabled.

        Shared by the app engine (connection.py) and Alembic (alembic/env.py)
        so migrations and the live app negotiate TLS identically.
        """
        mode = (self.postgres_sslmode or "").strip().lower()
        if not mode or mode == "disable":
            return None

        import ssl as _ssl

        if mode in ("require", "prefer", "allow"):
            # Encrypt the connection but skip cert/hostname verification — the
            # simplest mode that satisfies Supabase's "SSL required" without
            # shipping its CA bundle.
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            return ctx

        # verify-ca / verify-full — validate against the provider CA.
        ctx = _ssl.create_default_context(cafile=self.postgres_ssl_root_cert or None)
        if mode == "verify-ca":
            ctx.check_hostname = False
        return ctx

    @property
    def rls_enforced(self) -> bool:
        """True when the request engine connects as the non-superuser app role."""
        return bool(self.db_app_user and self.db_app_password)

    @property
    def database_url(self) -> str:
        """Async PostgreSQL URL for the REQUEST engine.

        Uses the non-superuser app role when configured (RLS enforced), else
        the default role (RLS inert — the superuser bypasses policies).
        """
        user = self.db_app_user or self.postgres_user
        password = self.db_app_password or self.postgres_password
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=user,
                password=password,
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

    # ── NUMU Agent (merchant copilot) ────────────────────────────────────────
    # Pluggable LLM provider behind an OpenAI-compatible client (FR-012). The
    # default is Groq's Llama 3.3 70B (free/low-cost); swap provider/model by
    # config alone — no tool/business-logic change. A cheaper model may be used
    # for trivial read chat via the optional router.
    agent_enabled: bool = True
    agent_llm_base_url: str = "https://api.groq.com/openai/v1"
    agent_llm_api_key: str = ""
    agent_llm_model: str = "llama-3.3-70b-versatile"
    agent_llm_model_cheap: str | None = None  # optional cheap model for trivial chat
    agent_llm_temperature: float = 0.2
    agent_max_tool_iterations: int = 5  # cap the perceive→act loop (runaway guard)
    agent_request_timeout_seconds: int = 30
    # Free-tier rate-limit handling: queue/retry via Redis instead of failing hard.
    agent_rate_limit_max_retries: int = 3
    agent_rate_limit_backoff_seconds: float = 2.0
    # NUMU-knowledge RAG (two layers: shared platform docs + per-tenant). Embeddings
    # default to multilingual-e5-large (1024-dim). When agent_embed_url is unset, a
    # deterministic local fallback embedder is used (dev/offline) — swap by config.
    agent_embed_url: str = (
        ""  # embeddings endpoint base; empty → deterministic hash fallback
    )
    # Wire format of agent_embed_url: "openai" (OpenAI-compatible /embeddings, e.g.
    # DeepInfra/TEI) or "hf" (Hugging Face feature-extraction pipeline router).
    agent_embed_provider: str = "openai"
    agent_embed_api_key: str = ""
    agent_embed_model: str = "intfloat/multilingual-e5-large"
    agent_embed_dim: int = 1024
    agent_knowledge_top_k: int = 5
    # Knowledge base (spec 002): pgvector is soft-added — retrieval falls back to the
    # JSONB scan when the extension/column isn't present, so nothing breaks. The corpus
    # coverage report flags areas with no published article or stale beyond this window.
    agent_knowledge_staleness_days: int = 90
    # Base URL of the developer docs ingested into Layer A (theme/SDK/CLI/API topics).
    agent_docs_ingest_base_url: str = "https://docs.numueg.app"
    # Shared secret n8n presents when upserting Layer A knowledge (server-side only).
    agent_knowledge_upsert_secret: str = ""
    # n8n orchestration lane (research R9): heavy/async/bulk work is offloaded to
    # the existing self-hosted n8n via an ALLOW-LISTED webhook only. The LLM never
    # calls n8n directly and can never invoke an arbitrary URL.
    agent_n8n_base_url: str = "https://n8n.numueg.app"
    agent_n8n_webhook_secret: str = ""  # signs triggers + verifies callbacks (HMAC)
    # Allow-listed workflow names (LLM can never invoke an arbitrary URL). Spec 002 adds
    # the knowledge ingestion/refresh + per-tenant Layer-B reindex lanes.
    agent_n8n_allowed_workflows: list[str] = [
        "ping",
        "knowledge_ingest_docs",
        "knowledge_refresh",
        "tenant_layerb_reindex",
    ]

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

    # Phase C cutover: when True, the Shopify final-score path persists the
    # canonical FSM's decision as the assessment's `suggested_action` instead
    # of the raw `_suggested_action` ladder (the display-strangle). Default off
    # — flip once the shadow log shows acceptable FSM-vs-ladder agreement.
    trust_fsm_decision_enabled: bool = False

    # Trust Network cutover (P1-7): when True, the COD final-score path treats
    # the standalone NUMU Trust Network's /v1/decisions risk_score as
    # authoritative — it drives persistence, auto-cancel, and auto-approve
    # instead of NUMU's embedded score_order() result. Default off — flip only
    # after the shadow log (trust_network_shadow) shows sustained zero drift.
    # Fail-open: if the network doesn't answer (disabled / timeout / non-200)
    # the embedded score is used, so COD scoring never depends on network
    # availability. The same shadow call (TRUST_NETWORK_SHADOW_ENABLED) carries
    # the authoritative score, so keep the shadow on when this is flipped.
    trust_network_authoritative: bool = False

    # Commerce-correctness Phase 1: when True, the storefront checkout runs
    # the offers-v2 engine (CalculateCartDiscountsUseCase / DiscountCalculator)
    # against the cart at order-create time and folds the resulting automatic
    # discount + free-shipping into the order total, persisting the applied
    # promotion ids on the order. When False (the default) checkout keeps the
    # legacy single-coupon-only behaviour and never touches the offers engine,
    # so the change is a no-op until explicitly enabled per environment. The
    # discount applied at order-create reconciles with what
    # POST /cart/discounts returns for the same cart.
    ff_apply_offers_at_checkout: bool = False

    # JWT Authentication (RS256 asymmetric signing)
    jwt_private_key: str = Field(default="")
    jwt_public_key: str = Field(default="")
    jwt_algorithm: str = "RS256"
    # Bumped 30 → 480 (8h) on 2026-05-26 for dev-friendly QA sessions —
    # the CLI token in ~/.numurc had no refresh_token field so an
    # expired access token forced the user to re-login mid-session.
    # 8h covers a full working day; refresh tokens still rotate at 7d.
    access_token_expire_minutes: int = 480
    refresh_token_expire_days: int = 7
    # Admin "log in as merchant" sessions. The handed-off token is a Bearer in
    # the hub's sessionStorage (tab-isolated) and is NOT refreshable, so it must
    # last a full work session on its own rather than the short access TTL —
    # otherwise impersonation breaks ~every access-token expiry.
    impersonation_token_expire_minutes: int = 480

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

    # ─── Web Push (VAPID / RFC 8292) ──────────────────────────────────
    # Vendor-free push for the merchant-hub PWA. Generate the keypair ONCE;
    # rotating the public key invalidates every existing browser subscription,
    # so merchants would silently stop receiving notifications until they
    # re-subscribe.
    #
    # The private key belongs in the production env ONLY (/opt/numu-api/.env).
    # Never commit it, never log it.
    #
    # When these are unset, push degrades silently: the endpoints report
    # "unavailable" and nothing raises. That is intentional so a deploy without
    # the keys cannot take the API down.
    VAPID_PUBLIC_KEY: str | None = None
    VAPID_PRIVATE_KEY: str | None = None
    # RFC 8292 requires a contact URI so a push service can reach the sender
    # about a misbehaving deployment.
    VAPID_SUBJECT: str = "mailto:support@numueg.app"

    @property
    def web_push_enabled(self) -> bool:
        """True when both VAPID keys are configured."""
        return bool(self.VAPID_PUBLIC_KEY and self.VAPID_PRIVATE_KEY)

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
    # Reverse proxies whose `X-Forwarded-For` / `X-Real-IP` we believe when
    # deciding which bucket a request is rate-limited under. Bare addresses or
    # CIDRs, as a JSON list in the environment:
    #   TRUSTED_PROXY_IPS=["10.0.0.0/8","172.31.0.0/16"]
    #
    # EMPTY (the default) means the header is trusted from anyone, which is
    # what this app has always done — so any caller can pick their own bucket
    # by varying the header and the per-IP limits are advisory at best.
    # Populating this closes that hole, but ONLY if the entries actually name
    # the hop in front of this process. Name the wrong thing and every request
    # buckets under the load balancer's address instead — a single shared
    # bucket for the entire platform, which takes out every rate-limited
    # endpoint at once. So: confirm the real edge topology (does Cloudflare /
    # the ALB overwrite `X-Forwarded-For`, or append to it?) before setting
    # this. `RateLimitMiddleware` logs a warning at startup while it is empty.
    trusted_proxy_ips: list[str] = []

    # Stripe
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None

    # Tap Payments
    tap_secret_key: str | None = None
    tap_publishable_key: str | None = None

    # Public base URL of THIS API as reachable from the internet, e.g.
    # https://numueg.app — used to build OAuth callback/redirect URIs
    # (Meta + TikTok + TikTok Shop). The routes fall back to
    # http://localhost:8000 when unset, so OAuth connect flows only work
    # in production once PUBLIC_API_URL is configured.
    public_api_url: str | None = None

    # Meta (Facebook/Instagram) Graph API
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_config_id: str | None = None  # Embedded Signup configuration ID
    # The ONE Graph API version every Meta call uses — CAPI events, OAuth,
    # Custom Audiences, EMQ, Custom Conversions, ad promote. There are no
    # per-call-site fallbacks any more: they all read this, so the value in
    # boot logs is the value on the wire.
    #
    # Why not just leave it: this was pinned to "v19.0", deprecated Feb 2025.
    # Nothing visibly broke, because an expired Graph version does not fail —
    # Meta silently routes the call to the next oldest usable version. So we
    # had no control over which contract applied and no access to any CAPI
    # parameter added since v19.
    #
    # Why v25.0 specifically (chosen 2026-07-30):
    #   * v26.0 is the newest that resolves (probed: v27.0 does not exist yet).
    #   * v23.0 is NOT a safe "conservative" pick despite being in Graph
    #     support — MARKETING API v23.0 expired 9 Jun 2026, and this same
    #     constant drives the Marketing calls (audiences, ad promote).
    #     Marketing versions age out in ~1 year, Graph in ~2, so the
    #     Marketing clock is the binding one.
    #   * v25.0 (18 Feb 2026) is current-minus-one with Marketing runway to
    #     roughly Feb 2027 — in support on both surfaces, and not the
    #     freshest-possible release.
    #
    # Override per environment with META_GRAPH_API_VERSION. Review trigger and
    # owner: docs/external-contracts.md.
    meta_graph_api_version: str = "v25.0"
    meta_webhook_verify_token: str | None = None
    meta_login_config_id: str | None = None

    # TikTok for Business — Marketing/Events API OAuth. Activation switch
    # for /oauth/tiktok/*: unset → the route returns 503 and merchants use
    # the paste-Pixel-ID + Events-API-token flow instead.
    tiktok_app_id: str | None = None
    tiktok_app_secret: str | None = None

    # TikTok Shop (sales channel) — Open Platform App. Activation switch for
    # /oauth/tiktok-shop/* + the webhook receiver. Unset → OAuth returns 503
    # and webhooks are rejected as unsigned.
    tiktok_shop_app_key: str | None = None
    tiktok_shop_app_secret: str | None = None

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

    # Local-dev asset base URL. When object storage is NOT configured (see
    # ``object_storage_configured``), uploads are written to
    # ``<project_root>/uploads`` and served by the FastAPI ``/uploads`` static
    # mount. Must match the API's own public origin (the mount lives on the API
    # app). Default targets the dev API port; override behind a reverse proxy
    # or when the API runs on a non-default port.
    local_storage_base_url: str = "http://localhost:8001/uploads"

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

    # Platform Paymob account (NUMU as the payee — subscription billing and
    # merchant-wallet top-ups). Distinct from the per-merchant defaults above:
    # money collected here lands in NUMU's own Paymob account.
    platform_paymob_secret_key: str | None = None
    platform_paymob_public_key: str | None = None
    platform_paymob_hmac_secret: str | None = None
    platform_paymob_card_integration_id: str | None = None
    platform_paymob_wallet_integration_id: str | None = None  # Vodafone Cash etc.

    # Platform Kashier account (NUMU as the payee — card top-ups for the
    # merchant wallet). Secrets stay env-only; non-secret wallet knobs are
    # admin-editable via platform_config (see wallet_settings service).
    platform_kashier_mid: str | None = None
    platform_kashier_api_key: str | None = None
    platform_kashier_mode: str = "test"  # "test" or "live"

    # Platform InstaPay identity (NUMU's own IPA) for merchant-wallet top-ups.
    platform_instapay_ipa: str | None = None
    platform_instapay_display_name: str | None = None
    # Platform Vodafone Cash wallet number for manual (non-gateway) top-ups.
    platform_vodafone_cash_number: str | None = None
    # Optional OCR provider for top-up receipts (google_vision | deepseek_hf
    # | glm_hf); empty/None -> Noop (rules that need OCR silently no-op).
    platform_instapay_ocr_provider: str | None = None

    # Public base URL of this API — used to build absolute webhook
    # notification URLs for platform-directed payments (wallet top-ups).
    platform_api_base_url: str = "https://numueg.app"

    # Merchant wallet (pay-as-you-go commission tier)
    wallet_negative_allowance_cents: int = 5_000  # checkout blocked below -50 EGP
    wallet_low_balance_threshold_cents: int = 10_000  # warn below 100 EGP
    ff_wallet_topups: bool = False
    ff_wallet_checkout_gate: bool = False
    # Go-live gate: NEW tenants (no golive_exempt feature flag) cannot take
    # storefront orders until they pick a paid plan or Pay as you Grow.
    # Admin-overridable via wallet_settings (golive_gate_enabled).
    ff_golive_gate: bool = False

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

    # GOWA (go-whatsapp-web-multidevice) — the second WhatsApp transport.
    #
    # UNOFFICIAL: it drives the WhatsApp Web multi-device protocol as a logged-in
    # account rather than the Business API, so the number in use carries a real
    # ban risk. Which merchants use it is an explicit per-store choice made in
    # the admin backoffice; these settings only describe how to reach the
    # self-hosted instance.
    #
    # `gowa_base_url` points at the TLS front door, never at the container: the
    # instance can send messages as ANY paired merchant, so it is bound to
    # loopback on its host and reachable only through an authenticated proxy.
    # `gowa_basic_auth` is "user:password" matching the instance's
    # APP_BASIC_AUTH; `gowa_webhook_secret` must equal its
    # WHATSAPP_WEBHOOK_SECRET or every inbound webhook fails verification.
    gowa_base_url: str | None = None
    gowa_basic_auth: str | None = None
    gowa_webhook_secret: str | None = None
    gowa_enabled: bool = False

    # Route the SHARED platform number through GOWA instead of Meta Cloud.
    #
    # When true, every store that has not explicitly chosen a transport and does
    # not have its own Meta credentials sends via the paired platform device.
    # Behaviour is otherwise unchanged — same templates, same triggers, same
    # merchant hub — only the wire underneath differs.
    #
    # CONCENTRATION RISK, deliberately called out: on the BYO path a ban costs
    # one merchant their number. On the shared path every store sends from the
    # SAME account, so a ban is a fleet-wide WhatsApp outage. The per-device
    # caps in `gowa_guard` are the mitigation, and they apply to the whole fleet
    # combined — see GOWA_PLATFORM_* there.
    gowa_platform_default: bool = False

    # Phone-first checkout identity (WhatsApp-OTP gate + save-cart nudge).
    #
    # Platform-wide rollout gate: while False the entire feature is inert —
    # no gate at checkout, no nudge, `identity.otp_available` reads false —
    # regardless of per-store settings. Once flipped, stores default to
    # require_verification=True (see core.checkout_fields.IdentityConfig)
    # unless the merchant opts out, and stores whose transport cannot
    # deliver an OTP (Meta without an approved AUTH template) self-degrade
    # to today's behaviour via the otp_available capability.
    checkout_identity_enabled: bool = False

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
    # Overrides the environment tag on Sentry events. The prod EC2 box runs
    # with ENVIRONMENT=staging (its .env is a copy of the droplet's staging
    # file, and flipping it would arm the strict production validators), so
    # without this override prod errors are tagged "staging" and invisible
    # to production-scoped Sentry alerts.
    sentry_environment: str | None = None
    sentry_traces_sample_rate: float = 0.1  # 10% of transactions
    sentry_profiles_sample_rate: float = 0.1  # 10% of profiled transactions
    sentry_send_default_pii: bool = False  # Set True to capture user emails, IPs

    # Structured Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" for production, "console" for development
    # Destination for log.alert(...) events. Empty = log-only (no webhook).
    # Provider-agnostic: point at a Slack/n8n/HTTP incoming-webhook URL.
    log_alert_webhook_url: str = ""

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
