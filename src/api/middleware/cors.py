"""CORS configuration."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings


def setup_cors(app: FastAPI) -> None:
    """Configure CORS for the application."""
    
    # Parse allowed origins from settings
    origins = []
    if settings.CORS_ORIGINS:
        origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]
    
    # In development, allow all origins
    if settings.DEBUG:
        origins = ["*"]
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )
