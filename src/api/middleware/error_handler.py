"""Error handling middleware.

Provides a global error handler and per-exception-type handlers that
return safe, structured JSON responses.  In production, internal details
are suppressed to prevent information disclosure (OWASP A01/A09).

Every error response follows a consistent envelope:
    {
        "success": false,
        "error": {
            "code": "ENTITY_NOT_FOUND",
            "message": "Human-readable message",
            "details": { ... }            // optional, never in production
        }
    }
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import settings
from src.core.exceptions import (
    AccountLockedError,
    AuthenticationError,
    AuthorizationError,
    DomainException,
    EntityNotFoundError,
    ExternalServiceError,
    InvalidTokenError,
    PaymentError,
    PlanLimitExceededError,
    TokenExpiredError,
    ValidationError,
)
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    InvalidDiscountRule,
    PromotionConflict,
    PromotionNotFound,
    PromotionStateError,
)

logger = logging.getLogger(__name__)


# ── Standardised error body builder ──────────────────────────


def _error_body(
    code: str,
    message: str,
    details: Any = None,
) -> dict:
    """Build a consistent error response dict.

    ``details`` is only included when non-None, and in production it is
    always stripped for safety (except for whitelisted codes like
    ACCOUNT_LOCKED where the client needs ``retry_after``).
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}


_DETAILS_ALLOWED_IN_PROD = {"ACCOUNT_LOCKED", "RATE_LIMIT_EXCEEDED"}


def _safe_error_body(
    code: str,
    message: str,
    details: Any = None,
) -> dict:
    """Same as ``_error_body`` but suppresses ``details`` in production
    unless the code is whitelisted."""
    if not settings.debug and code not in _DETAILS_ALLOWED_IN_PROD:
        details = None
    return _error_body(code, message, details)


# Public, anonymous-facing surfaces. On these we keep validation details
# hidden (an unauthenticated prober shouldn't get free schema hints); on
# every other surface (merchant `/stores/*`, `/admin/*`, auth, …) we return
# the sanitized field list so the merchant hub is self-diagnosing.
_PUBLIC_SURFACE_MARKERS = ("/storefront/", "/public/")


def _is_public_surface(path: str) -> bool:
    return any(marker in path for marker in _PUBLIC_SURFACE_MARKERS)


def _sanitize_validation_errors(exc: RequestValidationError) -> list[dict]:
    """Reduce Pydantic's ``exc.errors()`` to a safe, JSON-serializable list.

    Keeps only the field location, message, and error type — deliberately
    DROPS ``input`` (which echoes the caller's submitted value) and ``ctx``
    (which may carry a non-serializable ``ValueError`` and internal detail).
    """
    out: list[dict] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        field = ".".join(str(p) for p in loc) if loc else "(request)"
        out.append({
            "field": field,
            "message": err.get("msg", "Invalid value"),
            "type": err.get("type", "value_error"),
        })
    return out


async def error_handler_middleware(request: Request, call_next: Callable):
    """Global error handling middleware."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "INTERNAL_SERVER_ERROR", "An unexpected error occurred"
            ),
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers for the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        """Surface which fields failed on trusted (merchant/admin) surfaces.

        The full ``exc.errors()`` is always logged. In the response we include
        a SANITIZED field list (path + message + type, never the submitted
        value) on non-public surfaces or in debug — so the merchant hub is
        self-diagnosing — and suppress it on public storefront endpoints to
        avoid handing anonymous probers schema hints.
        """
        logger.warning(
            "Request validation failed on %s: %s", request.url.path, exc.errors()
        )
        show_details = settings.debug or not _is_public_surface(request.url.path)
        details = _sanitize_validation_errors(exc) if show_details else None
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body(
                "VALIDATION_ERROR",
                "Request validation failed",
                details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Override default HTTPException handler to ensure consistent format.

        When ``detail`` is a plain string we wrap it as the message. When it's
        a **dict** (structured client contracts like ``cod_trust_blocked`` /
        ``phone_required_for_cod`` / ``custom_field_errors`` which carry
        ``code`` + ``message_en`` + ``message_ar`` + ``errors``), we PRESERVE
        those fields on the error object instead of ``str()``-ing the dict into
        an opaque "{'code': ...}" blob — otherwise the storefront can't localize
        or branch on the code and ends up showing the raw envelope to the buyer.
        """
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "HTTP_ERROR")
            message = (
                detail.get("message")
                or detail.get("message_en")
                or detail.get("message_ar")
                or "Request failed"
            )
            body = _error_body(code, str(message))
            # Carry the remaining structured fields (message_en/message_ar/
            # errors/…) so the client can localize + render field errors.
            body["error"].update({
                k: v for k, v in detail.items() if k not in ("code", "message")
            })
            return JSONResponse(status_code=exc.status_code, content=body)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("HTTP_ERROR", str(detail)),
        )

    @app.exception_handler(EntityNotFoundError)
    async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_error_body("ENTITY_NOT_FOUND", str(exc)),
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("VALIDATION_ERROR", str(exc)),
        )

    # Must be registered before AuthenticationError so it takes priority
    @app.exception_handler(AccountLockedError)
    async def account_locked_handler(request: Request, exc: AccountLockedError):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_safe_error_body(
                "ACCOUNT_LOCKED",
                str(exc),
                {"retry_after": exc.retry_after},
            ),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("AUTHENTICATION_ERROR", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(TokenExpiredError)
    async def token_expired_handler(request: Request, exc: TokenExpiredError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("TOKEN_EXPIRED", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidTokenError)
    async def invalid_token_handler(request: Request, exc: InvalidTokenError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_error_body("INVALID_TOKEN", str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(request: Request, exc: AuthorizationError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_error_body("AUTHORIZATION_ERROR", str(exc)),
        )

    @app.exception_handler(PlanLimitExceededError)
    async def plan_limit_handler(request: Request, exc: PlanLimitExceededError):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content=_error_body(
                "PLAN_LIMIT_EXCEEDED",
                str(exc),
                {
                    "resource": exc.resource,
                    "limit": exc.limit,
                    "current": exc.current,
                    "plan": exc.plan,
                    "upgrade_to": exc.upgrade_to,
                },
            ),
        )

    @app.exception_handler(PaymentError)
    async def payment_error_handler(request: Request, exc: PaymentError):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content=_error_body("PAYMENT_ERROR", str(exc)),
        )

    @app.exception_handler(ExternalServiceError)
    async def storage_error_handler(request: Request, exc: ExternalServiceError):
        # Always log the real cause — this was previously swallowed, so an
        # upload/storage 500 surfaced to the merchant as the generic message
        # with zero clue why (e.g. an R2 SignatureDoesNotMatch). In debug/dev
        # we also return the underlying reason so it's diagnosable from the
        # client (image picker); production keeps the generic message and
        # ``_safe_error_body`` strips ``details`` to avoid leaking infra info.
        logger.error("External service error: %s", exc, exc_info=True)
        message = (
            f"External service operation failed: {exc}"
            if settings.debug
            else "External service operation failed"
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_safe_error_body(
                "EXTERNAL_SERVICE_ERROR", message, {"detail": str(exc)}
            ),
        )

    # ── offers-v2: promotion-specific domain errors ──────────────────────
    # Must be registered BEFORE the generic DomainException handler so
    # the more-specific subclass handlers win.

    @app.exception_handler(PromotionNotFound)
    async def promotion_not_found_handler(request: Request, exc: PromotionNotFound):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_error_body("PROMOTION_NOT_FOUND", str(exc)),
        )

    @app.exception_handler(PromotionConflict)
    async def promotion_conflict_handler(request: Request, exc: PromotionConflict):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_safe_error_body(
                "PROMOTION_VERSION_CONFLICT",
                str(exc),
                {
                    "current_version": exc.current_version,
                    "attempted_version": exc.attempted_version,
                },
            ),
        )

    @app.exception_handler(PromotionStateError)
    async def promotion_state_handler(request: Request, exc: PromotionStateError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("PROMOTION_STATE_INVALID", str(exc)),
        )

    @app.exception_handler(InvalidDiscountRule)
    async def invalid_discount_rule_handler(request: Request, exc: InvalidDiscountRule):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("PROMOTION_DISCOUNT_RULE_INVALID", str(exc)),
        )

    @app.exception_handler(CouponPromotionLinkError)
    async def coupon_link_handler(request: Request, exc: CouponPromotionLinkError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_error_body("PROMOTION_COUPON_ALREADY_LINKED", str(exc)),
        )

    @app.exception_handler(DomainException)
    async def domain_error_handler(request: Request, exc: DomainException):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_error_body("DOMAIN_ERROR", str(exc)),
        )
