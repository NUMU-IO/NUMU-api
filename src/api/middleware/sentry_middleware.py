"""Sentry middleware for request context and performance tracking."""

from collections.abc import Callable

import sentry_sdk
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SentryMiddleware(BaseHTTPMiddleware):
    """Middleware to enrich Sentry events with request context.

    Captures:
    - User ID and tenant ID as tags
    - Request path and method
    - Performance transaction tracking
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add Sentry context."""
        with sentry_sdk.configure_scope() as scope:
            # Set request context
            scope.set_tag("path", request.url.path)
            scope.set_tag("method", request.method)

            # Add user context if available (set by auth dependencies)
            user_id = getattr(request.state, "user_id", None)
            if user_id:
                scope.set_user({"id": str(user_id)})
                scope.set_tag("user_id", str(user_id))

            # Add tenant context if available (set by TenantMiddleware)
            tenant_id = getattr(request.state, "tenant_id", None)
            if tenant_id:
                scope.set_tag("tenant_id", str(tenant_id))

            tenant_slug = getattr(request.state, "tenant_slug", None)
            if tenant_slug:
                scope.set_tag("tenant_slug", tenant_slug)

            # Add request ID if available (set by logging middleware)
            request_id = getattr(request.state, "request_id", None)
            if request_id:
                scope.set_tag("request_id", request_id)

            # Start a transaction for performance monitoring
            transaction_name = f"{request.method} {request.url.path}"
            with sentry_sdk.start_transaction(
                op="http.server",
                name=transaction_name,
            ) as transaction:
                # Add custom data to the transaction
                transaction.set_data("url", str(request.url))
                transaction.set_data("query_string", str(request.query_params))

                response = await call_next(request)

                # Set HTTP status on transaction
                transaction.set_http_status(response.status_code)

                return response
