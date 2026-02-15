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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        description="E-commerce platform API for NUMU",
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # Setup exception handlers
    setup_exception_handlers(app)

    # Add SessionMiddleware for admin panel cookie-based auth
    # Uses separate session secret from JWT for security
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)

    # Add middleware (order matters: first added = outermost)
    # Security headers should be outermost to ensure all responses have them
    app.add_middleware(SecurityHeadersMiddleware)
    # Cache headers for public storefront endpoints (before compression so Vary is correct)
    app.add_middleware(CacheHeadersMiddleware)
    # Gzip compression for dev/local (Nginx handles compression in staging/prod)
    app.add_middleware(CompressionMiddleware)
    # Rate limiting should be next to block requests early
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(SentryMiddleware)  # Captures request context for Sentry
    app.add_middleware(LoggingMiddleware)  # Structured logging with request context
    app.add_middleware(
        ResponseTimeMiddleware
    )  # Response time tracking and slow request logging

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

    # Setup admin panel (public schema only)
    setup_admin(app)

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="127.0.0.1",
        port=8021,
        reload=settings.debug,
    )
