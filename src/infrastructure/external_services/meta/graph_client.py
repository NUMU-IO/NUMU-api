"""Meta Graph API base client with retry, rate-limit, and logging."""

from typing import Any

import httpx
import sentry_sdk

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


class MetaGraphAPIError(Exception):
    """Base exception for Meta Graph API errors."""

    def __init__(
        self, message: str, code: int | None = None, error_data: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.error_data = error_data or {}


class MetaRateLimitError(MetaGraphAPIError):
    """Rate limit exceeded."""

    pass


class MetaAuthenticationError(MetaGraphAPIError):
    """Authentication failed."""

    pass


class MetaGraphClient:
    """Base client for Meta Graph API with built-in retry and rate-limit handling."""

    def __init__(
        self,
        access_token: str,
        app_secret: str | None = None,
    ):
        self.access_token = access_token
        self.app_secret = app_secret or settings.meta_app_secret
        self.api_version = settings.meta_graph_api_version or "v21.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        if self.app_secret:
            headers["X-Meta-App-Secret"] = self.app_secret
        return headers

    async def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make a GET request to the Graph API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        params = params or {}
        params["access_token"] = self.access_token

        logger.debug(
            "meta_api_request",
            method="GET",
            endpoint=endpoint,
            params_keys=list(params.keys()),
        )

        response = await self._client.get(
            url, headers=self._get_headers(), params=params, **kwargs
        )
        return self._handle_response(response, endpoint=endpoint)

    async def post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make a POST request to the Graph API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = data or {}
        data["access_token"] = self.access_token

        logger.debug(
            "meta_api_request",
            method="POST",
            endpoint=endpoint,
            data_keys=list(data.keys()),
        )

        response = await self._client.post(
            url, headers=self._get_headers(), json=data, **kwargs
        )
        return self._handle_response(response, endpoint=endpoint)

    async def delete(
        self,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make a DELETE request to the Graph API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        params = {"access_token": self.access_token}

        logger.debug(
            "meta_api_request",
            method="DELETE",
            endpoint=endpoint,
        )

        response = await self._client.delete(
            url, headers=self._get_headers(), params=params, **kwargs
        )
        return self._handle_response(response, endpoint=endpoint)

    def _handle_response(
        self, response: httpx.Response, endpoint: str = ""
    ) -> dict[str, Any]:
        """Handle API response and errors."""
        try:
            data: dict[str, Any] = response.json()
        except Exception:
            logger.error(
                "meta_api_parse_error",
                status_code=response.status_code,
                text=response.text[:500],
            )
            raise MetaGraphAPIError(
                f"Failed to parse response: {response.text[:200]}",
                code=response.status_code,
            )

        if response.status_code == 200:
            sentry_sdk.add_breadcrumb(
                category="meta_api",
                message=endpoint,
                level="info",
                data={"status_code": response.status_code, "endpoint": endpoint},
            )
            return data

        error = data.get("error", {})
        error_code = error.get("code")
        error_message = error.get("message", "")

        if error_code in (4, 200, 190):
            raise MetaAuthenticationError(
                f"Token expired or invalid: {error_message}",
                code=error_code,
                error_data=error,
            )
        elif error_code == 4 or "rate limit" in error_message.lower():
            raise MetaRateLimitError(
                f"Rate limit exceeded: {error_message}",
                code=error_code,
                error_data=error,
            )
        else:
            raise MetaGraphAPIError(error_message, code=error_code, error_data=error)

    def _extract_rate_limit_info(self, response: httpx.Response) -> dict[str, Any]:
        """Extract rate limit info from response headers."""
        return {
            "x-business-use-case-usage": response.headers.get(
                "X-Business-Use-Case-Usage"
            ),
            "x-app-usage": response.headers.get("X-App-Usage"),
            "retry_after": response.headers.get("Retry-After"),
        }
