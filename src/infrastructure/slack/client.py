"""Slack HTTP client with rate limiting and retry logic.

Handles communication with Slack API:
- Webhook posts for channel messages
- Bot API calls for mentions and DMs
- Rate limiting (1 msg/sec per webhook)
- Exponential backoff for failures
"""

import asyncio
from typing import Any

import httpx

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.slack.channels import AlertChannel

logger = get_logger(__name__)

# Slack rate limits
SLACK_RATE_LIMIT_PER_SECOND = 1
SLACK_BURST_LIMIT = 10
SLACK_BACKOFF_BASE_SECONDS = 1
SLACK_MAX_RETRIES = 3


class SlackClientError(Exception):
    """Base exception for Slack client errors."""
    pass


class SlackRateLimitError(SlackClientError):
    """Raised when Slack rate limit is hit."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


class SlackWebhookError(SlackClientError):
    """Raised when webhook request fails."""
    pass


class SlackClient:
    """Async HTTP client for Slack API.

    Supports:
    - Webhook posts with rate limiting
    - Bot token API calls
    - Automatic retry with exponential backoff
    """

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = SLACK_MAX_RETRIES,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: dict[str, float] = {}  # Per-webhook rate limiting

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _get_webhook_url(self, channel: AlertChannel) -> str | None:
        """Get webhook URL for a channel."""
        return settings.get_slack_webhook(channel.value)

    async def _respect_rate_limit(self, webhook_url: str) -> None:
        """Enforce rate limit by waiting if necessary."""
        now = asyncio.get_event_loop().time()
        last_time = self._last_request_time.get(webhook_url, 0)
        elapsed = now - last_time

        if elapsed < SLACK_RATE_LIMIT_PER_SECOND:
            wait_time = SLACK_RATE_LIMIT_PER_SECOND - elapsed
            await asyncio.sleep(wait_time)

        self._last_request_time[webhook_url] = asyncio.get_event_loop().time()

    async def post_webhook(
        self,
        channel: AlertChannel,
        payload: dict[str, Any],
    ) -> bool:
        """Post message to Slack webhook with rate limiting.

        Args:
            channel: Target channel
            payload: Slack Block Kit payload

        Returns:
            True if successful, False otherwise

        Raises:
            SlackWebhookError: If all retries fail
            SlackRateLimitError: If rate limited by Slack
        """
        webhook_url = self._get_webhook_url(channel)

        if not webhook_url:
            logger.warning(
                "slack_webhook_not_configured",
                channel=channel.value,
                msg=f"No webhook URL configured for {channel.value}",
            )
            return False

        if not settings.slack_enabled:
            logger.debug(
                "slack_disabled",
                channel=channel.value,
                msg="Slack alerting is disabled",
            )
            return False

        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                # Respect rate limit
                await self._respect_rate_limit(webhook_url)

                response = await client.post(webhook_url, json=payload)

                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(
                        "slack_rate_limited",
                        channel=channel.value,
                        retry_after=retry_after,
                    )
                    raise SlackRateLimitError(retry_after)

                # Check for success
                if response.status_code == 200:
                    logger.info(
                        "slack_webhook_success",
                        channel=channel.value,
                        attempt=attempt + 1,
                    )
                    return True

                # Non-200 response
                logger.warning(
                    "slack_webhook_error",
                    channel=channel.value,
                    status_code=response.status_code,
                    response_text=response.text[:200],
                    attempt=attempt + 1,
                )
                last_error = SlackWebhookError(
                    f"Webhook returned {response.status_code}: {response.text[:100]}"
                )

            except SlackRateLimitError:
                raise  # Don't retry rate limits
            except httpx.TimeoutException as e:
                logger.warning(
                    "slack_webhook_timeout",
                    channel=channel.value,
                    attempt=attempt + 1,
                    error=str(e),
                )
                last_error = e
            except httpx.RequestError as e:
                logger.warning(
                    "slack_webhook_request_error",
                    channel=channel.value,
                    attempt=attempt + 1,
                    error=str(e),
                )
                last_error = e

            # Exponential backoff before retry
            if attempt < self.max_retries - 1:
                backoff = SLACK_BACKOFF_BASE_SECONDS * (2 ** attempt)
                await asyncio.sleep(backoff)

        # All retries failed
        logger.error(
            "slack_webhook_failed",
            channel=channel.value,
            max_retries=self.max_retries,
            last_error=str(last_error),
        )
        return False

    async def post_bot_message(
        self,
        channel_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Post message using Bot token API.

        Required for:
        - Direct messages to users
        - @mentions that need to resolve
        - Interactive message updates

        Args:
            channel_id: Slack channel or user ID
            payload: Message payload (blocks, text, etc.)

        Returns:
            API response dict or None on failure
        """
        if not settings.slack_bot_token:
            logger.warning(
                "slack_bot_token_not_configured",
                msg="Bot token required for this operation",
            )
            return None

        if not settings.slack_enabled:
            return None

        client = await self._get_client()
        url = "https://slack.com/api/chat.postMessage"

        try:
            response = await client.post(
                url,
                json={
                    "channel": channel_id,
                    **payload,
                },
                headers={
                    "Authorization": f"Bearer {settings.slack_bot_token}",
                    "Content-Type": "application/json",
                },
            )

            data = response.json()

            if not data.get("ok"):
                logger.error(
                    "slack_bot_api_error",
                    channel=channel_id,
                    error=data.get("error"),
                )
                return None

            logger.info(
                "slack_bot_message_sent",
                channel=channel_id,
                ts=data.get("ts"),
            )
            return data

        except Exception as e:
            logger.error(
                "slack_bot_message_failed",
                channel=channel_id,
                error=str(e),
            )
            return None


# Global client instance
_slack_client: SlackClient | None = None


def get_slack_client() -> SlackClient:
    """Get or create global Slack client instance."""
    global _slack_client
    if _slack_client is None:
        _slack_client = SlackClient()
    return _slack_client
