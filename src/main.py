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
    RateLimitMiddleware,
    ResponseTimeMiddleware,
    SecurityHeadersMiddleware,
    SentryMiddleware,
    TenantMiddleware,
    setup_cors,
    setup_exception_handlers,
)
from src.api.v1.routes import api_router
from src.config import settings
from src.config.logging_config import configure_logging, get_logger
from src.infrastructure.database import engine

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

    # Initialize event bus (registers all event handlers)
    from src.infrastructure.events.setup import create_event_bus

    create_event_bus()

    logger.info(
        "app_startup",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        debug=settings.debug,
    )

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
    {"name": "Webhooks - Bosta", "description": "Bosta shipping webhook receiver"},
    {
        "name": "Webhooks - WhatsApp",
        "description": "WhatsApp messaging webhook receiver",
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

    # Serve local uploads in development (when R2 is not configured)
    if settings.debug and not settings.r2_account_id:
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
