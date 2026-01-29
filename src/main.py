"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import uvicorn

from fastapi import FastAPI

from src.api.middleware import (
    RateLimitMiddleware,
    TenantMiddleware,
    logging_middleware,
    setup_cors,
    setup_exception_handlers,
)
from src.api.admin import setup_admin
from src.api.v1.routes import api_router
from src.config import settings
from src.infrastructure.database import engine

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting NUMU API...")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"API Version: {settings.app_version}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down NUMU API...")
    await engine.dispose()
    logger.info("Database connection closed")


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
    
    # Setup CORS
    setup_cors(app)
    
    # Setup exception handlers
    setup_exception_handlers(app)
    
    # Add SessionMiddleware for admin panel cookie-based auth
    # Uses separate session secret from JWT for security
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
    
    # Add middleware (order matters: first added = outermost)
    # Rate limiting should be outermost to block requests early
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(TenantMiddleware)
    app.middleware("http")(logging_middleware)
    
    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint - API information."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "description": "E-commerce platform API for NUMU",
            "docs": "/docs" if settings.debug else None,
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
        host="0.0.0.0",
        port=8021,
        reload=settings.debug,
    )
