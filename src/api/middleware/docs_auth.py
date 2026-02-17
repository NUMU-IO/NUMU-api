"""Basic auth middleware to protect API documentation on staging."""

import base64
import secrets
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from src.config import settings

# Paths that should be protected by basic auth on staging
PROTECTED_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


class DocsAuthMiddleware(BaseHTTPMiddleware):
    """Protect /docs, /redoc, and /openapi.json with HTTP Basic Auth.

    Only active when ``settings.environment == "staging"`` and
    ``settings.docs_username`` / ``settings.docs_password`` are configured.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Challenge with 401 if credentials are missing or incorrect."""
        path = request.url.path.rstrip("/") or "/"

        if path not in PROTECTED_DOC_PATHS:
            return await call_next(request)

        # Also allow the OAuth2 redirect and Swagger static assets
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return _unauthorized_response()

        try:
            decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
            username, password = decoded.split(":", 1)
        except Exception:
            return _unauthorized_response()

        # Constant-time comparison to avoid timing attacks
        username_ok = secrets.compare_digest(username, settings.docs_username)
        password_ok = secrets.compare_digest(password, settings.docs_password)

        if not (username_ok and password_ok):
            return _unauthorized_response()

        return await call_next(request)


def _unauthorized_response() -> StarletteResponse:
    """Return a 401 response with WWW-Authenticate header."""
    return StarletteResponse(
        content="Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="NUMU API Docs"'},
    )
