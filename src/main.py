"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.middleware import (
    logging_middleware,
    setup_cors,
    setup_exception_handlers,
)
from src.api.v1.routes import api_router
from src.config import settings
from src.infrastructure.database import engine

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Octyrafiy API...")
    logger.info(f"Debug mode: {settings.DEBUG}")
    logger.info(f"API Version: {settings.API_VERSION}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Octyrafiy API...")
    await engine.dispose()
    logger.info("Database connection closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        description="E-commerce platform API for Octyrafiy",
        version=settings.API_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )
    
    # Setup CORS
    setup_cors(app)
    
    # Setup exception handlers
    setup_exception_handlers(app)
    
    # Add middleware
    app.middleware("http")(logging_middleware)
    
    # Include routers
    app.include_router(api_router)
    
    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
