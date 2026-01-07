"""CORS configuration."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings


def setup_cors(app: FastAPI) -> None:
    """Configure CORS for the application."""
    
    # Get allowed origins from settings (already a list)
    origins = settings.cors_origins or []
    
    # In development, allow all origins
    if settings.debug:
        origins = ["*"]
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )
