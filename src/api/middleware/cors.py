"""CORS configuration with security hardening."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings

logger = logging.getLogger(__name__)

# Default allowed methods for API endpoints
DEFAULT_ALLOWED_METHODS = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
]

# Default allowed headers
DEFAULT_ALLOWED_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Origin",
    "X-Requested-With",
    "X-Request-ID",
    "X-Tenant-Subdomain",
]

# Headers to expose to the client
DEFAULT_EXPOSED_HEADERS = [
    "X-Request-ID",
    "X-Process-Time",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "Retry-After",
]


def _validate_origins(origins: list[str], environment: str) -> list[str]:
    """Validate and sanitize CORS origins.

    Args:
        origins: List of allowed origins
        environment: Current environment (development, production, etc.)

    Returns:
        Validated list of origins
    """
    if not origins:
        if environment == "production":
            logger.warning(
                "CORS: No origins configured for production! "
                "Set CORS_ORIGINS environment variable."
            )
            return []
        # Default development origins
        return ["http://localhost:3000", "http://127.0.0.1:3000"]

    validated = []
    for origin in origins:
        origin = origin.strip()

        if origin == "*" and environment == "production":
            logger.warning(
                "CORS: Wildcard origin '*' detected in production! "
                "This is a security risk. Configure specific origins."
            )
            # In production, don't allow wildcard - skip it
            continue

        if origin.startswith(("http://", "https://")):
            validated.append(origin)
        elif origin == "*":
            validated.append(origin)
        else:
            logger.warning(
                f"CORS: Invalid origin format '{origin}' - must include protocol"
            )

    return validated


def setup_cors(app: FastAPI) -> None:
    """Configure CORS for the application with security considerations.

    In production:
    - Validates that specific origins are configured
    - Logs warnings for permissive settings
    - Uses restricted headers and methods

    In development:
    - Allows localhost origins for convenience
    - More permissive but still logs warnings
    """
    environment = settings.environment

    # Get configured origins
    configured_origins = settings.cors_origins or []

    # In debug mode, default to permissive but log warning
    if settings.debug:
        if not configured_origins:
            configured_origins = [
                "http://localhost:3000",
                "http://localhost:3002",
                "http://localhost:3030",  # Dashboard
                "http://localhost:5173",  # Vite default
                "http://127.0.0.1:3000",
                "http://127.0.0.1:3002",
                "http://127.0.0.1:3030",  # Dashboard
                "http://127.0.0.1:5173",
                "http://172.30.144.1:3030",  # LAN access
            ]
            logger.info(
                f"CORS: Debug mode - using default development origins: {configured_origins}"
            )
    else:
        # Production mode - validate strictly
        if not configured_origins:
            logger.error(
                "CORS: No origins configured for production! "
                "API will reject cross-origin requests. "
                "Set CORS_ORIGINS environment variable."
            )
        elif "*" in configured_origins:
            logger.error(
                "CORS: Wildcard origin '*' in production is a critical security risk! "
                "Configure specific origins in CORS_ORIGINS."
            )

    # Validate origins
    origins = _validate_origins(configured_origins, environment)

    # Log final configuration
    if origins:
        logger.info(f"CORS: Configured allowed origins: {origins}")
    else:
        logger.warning(
            "CORS: No valid origins configured - cross-origin requests will fail"
        )

    # Check for credentials + wildcard conflict
    allow_credentials = True
    if "*" in origins:
        # Can't use credentials with wildcard - this is a browser restriction
        logger.warning(
            "CORS: Cannot use credentials with wildcard origin. "
            "Disabling credentials for CORS."
        )
        allow_credentials = False

    # Configure methods
    allowed_methods = DEFAULT_ALLOWED_METHODS
    if settings.debug:
        # In debug, allow all methods
        allowed_methods = ["*"]

    # Configure headers
    allowed_headers = DEFAULT_ALLOWED_HEADERS
    if settings.debug:
        # In debug, allow all headers
        allowed_headers = ["*"]

    # In debug mode, also allow subdomain origins (e.g., octyra.localhost:3000)
    allow_origin_regex = None
    if settings.debug:
        allow_origin_regex = r"^https?://[\w-]+\.localhost:\d+$"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=allow_credentials,
        allow_methods=allowed_methods,
        allow_headers=allowed_headers,
        expose_headers=DEFAULT_EXPOSED_HEADERS,
        max_age=600
        if environment == "production"
        else 0,  # Cache preflight for 10 min in prod
    )

    if settings.debug:
        logger.debug(
            f"CORS middleware configured: "
            f"origins={origins}, "
            f"credentials={allow_credentials}, "
            f"methods={allowed_methods}"
        )
