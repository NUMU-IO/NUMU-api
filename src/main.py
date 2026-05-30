"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import sentry_sdk
import uvicorn
from fastapi import FastAPI
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from src.api.admin import setup_admin
from src.api.middleware import (
    CacheHeadersMiddleware,
    CompressionMiddleware,
    CSRFMiddleware,
    DocsAuthMiddleware,
    LoggingMiddleware,
    MaintenanceModeMiddleware,
    RateLimitMiddleware,
    ResponseTimeMiddleware,
    SecurityHeadersMiddleware,
    SentryMiddleware,
    TenantMiddleware,
    setup_cors,
    setup_exception_handlers,
)
from src.api.short_link_redirect import router as short_link_redirect_router
from src.api.v1.routes import api_router
from src.api.v1.routes.order_redirect import router as order_redirect_router
from src.config import settings
from src.config.logging_config import configure_logging, get_logger
from src.infrastructure.database import AsyncSessionLocal, engine

# Configure structured logging
configure_logging()
logger = get_logger(__name__)


def init_sentry() -> None:
    """Initialize Sentry SDK for error tracking and performance monitoring."""
    if not settings.sentry_dsn:
        logger.warning(
            "sentry_dsn_not_configured",
            msg="Sentry DSN not set, error tracking disabled",
        )
        return

    # Configure logging integration to capture WARNING+ as breadcrumbs, ERROR+ as events
    sentry_logging = LoggingIntegration(
        level=logging.INFO,  # Capture INFO+ as breadcrumbs
        event_level=logging.ERROR,  # Send ERROR+ as Sentry events
    )

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"numu-api@{settings.app_version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=settings.sentry_send_default_pii,
        integrations=[
            sentry_logging,
            AsyncioIntegration(),
            SqlalchemyIntegration(),
        ],
        before_send=_filter_sentry_events,
    )
    logger.info(
        "sentry_initialized",
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


def _filter_sentry_events(event: dict, _hint: dict) -> dict | None:
    """Filter Sentry events before sending (PII scrubbing, ignoring certain errors)."""
    # Ignore health check errors
    if "request" in event and event["request"].get("url", "").endswith("/health"):
        return None
    return event


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    init_sentry()

    # Check for Meta credentials
    from src.config import settings as app_settings

    if not app_settings.meta_app_id or not app_settings.meta_app_secret:
        logger.warning(
            "meta_credentials_missing",
            msg="Meta App ID/Secret not configured - Omnichannel Inbox features will be unavailable",
            meta_app_id_set=bool(app_settings.meta_app_id),
            meta_app_secret_set=bool(app_settings.meta_app_secret),
        )

    # Initialize event bus (registers all event handlers)
    from src.infrastructure.events.setup import create_event_bus

    create_event_bus()

    # Validate email-template registry (defaults must render against
    # their sample data). Fail-fast on a malformed default.
    from src.application.services.email_template_registry import validate_registry

    validate_registry()

    # Load plan-limit overrides from DB so admin changes survive restarts.
    try:
        from sqlalchemy import select as sa_select

        from src.api.v1.routes.admin.plan_limits import (
            PLAN_LIMITS_KEY,
            _apply_overrides,
        )
        from src.infrastructure.database.models.public.platform_config import (
            PlatformConfigModel,
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                sa_select(PlatformConfigModel).where(
                    PlatformConfigModel.key == PLAN_LIMITS_KEY
                )
            )
            row = result.scalar_one_or_none()
            if row and isinstance(row.value, dict):
                _apply_overrides(row.value)
                logger.info("plan_limits_loaded_from_db", plans=list(row.value.keys()))
    except Exception:
        logger.warning(
            "plan_limits_db_load_failed — using code defaults", exc_info=True
        )

    logger.info(
        "app_startup",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        debug=settings.debug,
    )

    # backend-030 / US5 / T093 — subscribe the platform Meta app to the
    # ``message_template_status_update`` webhook field on NUMU's platform
    # WABA so template approval-status updates push in near-real-time
    # (FR-028 / research R1). Idempotent at Meta's end. Best-effort —
    # failures here don't block app boot; the polling sync (T091)
    # provides the fallback path.
    try:
        from src.infrastructure.external_services.meta.whatsapp_client import (
            WhatsAppClient,
        )

        if (
            settings.whatsapp_enabled
            and settings.whatsapp_access_token
            and settings.whatsapp_business_account_id
            and settings.meta_app_id
        ):
            client = WhatsAppClient(
                phone_number_id=settings.whatsapp_phone_number_id or "",
                access_token=settings.whatsapp_access_token,
                waba_id=settings.whatsapp_business_account_id,
            )
            try:
                await client.subscribe_app_to_waba(settings.meta_app_id)
                logger.info(
                    "whatsapp_platform_app_subscribed_to_waba",
                    waba_id=settings.whatsapp_business_account_id,
                )
            finally:
                await client.close()
    except Exception:
        logger.warning("whatsapp_platform_app_subscribe_failed", exc_info=True)

    yield

    # Shutdown
    logger.info("app_shutdown", msg="Shutting down NUMU API")
    await engine.dispose()
    logger.info("database_closed", msg="Database connection closed")


# Ordered tag metadata for OpenAPI documentation grouping
OPENAPI_TAGS = [
    # ── Core ──────────────────────────────────────────────
    {"name": "Root", "description": "Root API information"},
    {"name": "Health", "description": "Health checks and system status"},
    {"name": "Authentication", "description": "User authentication and authorization"},
    {"name": "Tenants", "description": "Multi-tenant management"},
    # ── Store management ──────────────────────────────────
    {"name": "Stores", "description": "Store CRUD operations"},
    {"name": "Store Products", "description": "Product catalog management"},
    {"name": "Store Orders", "description": "Order processing and fulfillment"},
    {"name": "Store Customers", "description": "Customer management"},
    {"name": "Store Coupons", "description": "Coupon and discount management"},
    {
        "name": "Store Invoices",
        "description": "ETA e-invoicing for the Egyptian market",
    },
    {
        "name": "Store Inventory",
        "description": "Inventory tracking and stock management",
    },
    {"name": "Store Analytics", "description": "Store analytics and reporting"},
    {"name": "Store Dashboard", "description": "Dashboard metrics and summaries"},
    {"name": "Store Settings", "description": "Store settings and configuration"},
    {"name": "Store Onboarding", "description": "Store onboarding progress"},
    {"name": "Store Feedback", "description": "User feedback management"},
    # ── Storefront (customer-facing) ──────────────────────
    {
        "name": "Storefront - Public",
        "description": "Public storefront catalog and customer auth",
    },
    {
        "name": "Storefront - Customer",
        "description": "Customer account and profile endpoints",
    },
    {"name": "Storefront - Cart", "description": "Shopping cart operations"},
    {"name": "Storefront - Checkout", "description": "Checkout and payment processing"},
    {"name": "Storefront - Coupons", "description": "Coupon validation for shoppers"},
    # ── Configuration ─────────────────────────────────────
    {
        "name": "Configuration Requests",
        "description": "Tenant configuration requests (payment, shipping, etc.)",
    },
    # ── Admin ─────────────────────────────────────────────
    {"name": "Admin", "description": "Admin panel endpoints"},
    {"name": "Admin - Tenants", "description": "Admin tenant management"},
    {"name": "Admin - Waitlist", "description": "Admin waitlist management"},
    {"name": "Admin - Feedback", "description": "Admin feedback management"},
    {"name": "Admin - Credentials", "description": "Admin credentials management"},
    # ── Public ────────────────────────────────────────────
    {"name": "Public", "description": "Public landing and waitlist endpoints"},
    {"name": "Public - Waitlist", "description": "Public waitlist signup"},
    {"name": "Public - Landing", "description": "Public landing page data"},
    # ── Webhooks ──────────────────────────────────────────
    {"name": "Webhooks - Paymob", "description": "Paymob payment webhook receiver"},
    {"name": "Webhooks - Fawry", "description": "Fawry payment webhook receiver"},
    {
        "name": "Webhooks - Bosta",
        "description": "Bosta shipping webhook receiver",
    },
    {
        "name": "Webhooks - WhatsApp",
        "description": "WhatsApp messaging webhook receiver",
    },
    # ── Staff & Roles ────────────────────────────────
    {"name": "Staff", "description": "Staff account management"},
    {"name": "Staff - Invitations", "description": "Staff invitation workflow"},
    {"name": "Roles", "description": "Role and permission management"},
    {
        "name": "Webhooks - Meta",
        "description": "Meta (Facebook/Instagram) messaging webhook receiver",
    },
    {
        "name": "Store Channels",
        "description": "Omnichannel messaging inbox (Instagram, Messenger, WhatsApp)",
    },
]


def _should_expose_docs() -> bool:
    """Determine if API docs should be exposed (debug or staging)."""
    return settings.debug or settings.environment == "staging"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    expose_docs = _should_expose_docs()

    app = FastAPI(
        redirect_slashes=False,
        title=settings.app_name,
        description=(
            "NUMU is an e-commerce platform API built for the Egyptian market. "
            "It provides endpoints for store management, product catalog, orders, "
            "invoicing (ETA e-invoicing), payments (Paymob, Fawry, COD), "
            "and shipping (Bosta)."
        ),
        version=settings.app_version,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
        contact={
            "name": "NUMU Engineering",
            "email": "engineering@numu.com",
        },
        license_info={
            "name": "Proprietary",
            "url": "https://numu.com/terms",
        },
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    # Setup exception handlers
    setup_exception_handlers(app)

    # Add SessionMiddleware for admin panel cookie-based auth
    # Uses separate session secret from JWT for security
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)

    # Add middleware (order matters: first added = outermost)
    # In debug mode, use a minimal middleware stack to avoid
    # BaseHTTPMiddleware nesting issues (Starlette known issue).
    if not settings.debug:
        app.add_middleware(SecurityHeadersMiddleware)
        if settings.environment == "staging" and settings.docs_username:
            app.add_middleware(DocsAuthMiddleware)
        app.add_middleware(CacheHeadersMiddleware)
        app.add_middleware(CompressionMiddleware)
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(SentryMiddleware)
        app.add_middleware(ResponseTimeMiddleware)

    # Essential middleware — always active
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(LoggingMiddleware)
    # Maintenance mode — outermost so it short-circuits everything except
    # the allow-list (health, admin, auth, docs).
    app.add_middleware(MaintenanceModeMiddleware)

    # Setup CORS — added LAST so it's the outermost middleware
    # This ensures preflight OPTIONS requests are handled before any other middleware
    setup_cors(app)

    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint - API information."""
        if settings.debug:
            return {
                "name": settings.app_name,
                "version": settings.app_version,
                "docs": "/docs",
                "health": "/api/v1/public/health",
            }
        # Production: minimal info to reduce info disclosure
        return {
            "status": "ok",
            "health": "/api/v1/public/health",
        }

    # Include routers
    app.include_router(api_router)
    # Short-link redirector — mounted at app root (no /api/v1 prefix)
    # so the public URL stays clean: numueg.app/r/{short_code}.
    app.include_router(short_link_redirect_router)
    # Order-link redirector — mounted at app root for the same reason:
    # WhatsApp template CTA buttons point at numueg.app/o/{order_id}
    # and resolve to the tenant store's /track/<id> page (backend-030).
    app.include_router(order_redirect_router)

    # Serve local uploads in development (when object storage is not
    # configured). Gated on the same ``object_storage_configured`` signal as
    # the storage factory so the mount exists exactly when LocalStorageService
    # is in use.
    if settings.debug and not settings.object_storage_configured:
        from pathlib import Path

        from starlette.staticfiles import StaticFiles

        uploads_dir = Path(__file__).resolve().parents[1] / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

    # Note: brand assets (logo PNG) used inside emails are served by the
    # landing-page nginx location at https://numueg.app/numu-logo-*.png
    # — see brand_assets_base_url in settings.py.

    # Setup admin panel (public schema only)
    setup_admin(app)

    # Step 16 — Prometheus /metrics endpoint.
    # Gated by ``settings.metrics_endpoint_enabled`` so the route is
    # entirely absent when the flag is off. Additional defence: if
    # ``settings.metrics_auth_token`` is configured, the handler also
    # requires a matching Bearer token. nginx is expected to gate
    # /metrics on an IP allowlist (the Step 14 plan); the token is
    # defence in depth.
    if settings.metrics_endpoint_enabled:
        from fastapi import HTTPException, Request
        from fastapi.responses import Response as FastAPIResponse

        from src.infrastructure.observability.prometheus_metrics import (
            render_exposition,
        )

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint(request: Request) -> FastAPIResponse:
            expected = settings.metrics_auth_token
            if expected:
                auth = request.headers.get("Authorization") or ""
                # Constant-time comparison via secrets.compare_digest
                import secrets

                presented = ""
                if auth.startswith("Bearer "):
                    presented = auth[len("Bearer ") :].strip()
                if not presented or not secrets.compare_digest(presented, expected):
                    raise HTTPException(status_code=401, detail="unauthorized")
            body, content_type = render_exposition()
            return FastAPIResponse(content=body, media_type=content_type)

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.debug,
    )
