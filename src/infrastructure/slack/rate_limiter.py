"""Redis-based rate limiter and deduplication for Slack alerts.

Prevents alert spam by:
- Deduplicating similar alerts within cooldown windows
- Aggregating suppressed alert counts
- Different cooldowns per severity level
"""

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache import RedisCacheService
from src.infrastructure.slack.alerts import AlertSeverity, SlackAlert

logger = get_logger(__name__)

# Redis key prefixes
DEDUP_KEY_PREFIX = "slack:alert:dedup"
QUEUE_KEY = "slack:alert:queue"


class AlertRateLimiter:
    """Rate limiter for Slack alerts using Redis.

    Features:
    - Per-alert deduplication using content hash
    - Severity-based cooldown windows
    - Suppressed count tracking for aggregation
    """

    def __init__(self, cache: RedisCacheService | None = None) -> None:
        self._cache = cache

    async def _get_cache(self) -> RedisCacheService:
        """Get or create cache instance."""
        if self._cache is None:
            self._cache = RedisCacheService()
        return self._cache

    def _get_cooldown_seconds(self, severity: AlertSeverity) -> int:
        """Get cooldown window for severity level."""
        return {
            AlertSeverity.CRITICAL: settings.slack_cooldown_critical_seconds,
            AlertSeverity.WARN: settings.slack_cooldown_warn_seconds,
            AlertSeverity.INFO: settings.slack_cooldown_info_seconds,
        }[severity]

    def _get_dedup_key(self, alert: SlackAlert) -> str:
        """Generate Redis key for alert deduplication."""
        return f"{DEDUP_KEY_PREFIX}:{alert.dedup_key}"

    async def should_send(self, alert: SlackAlert) -> tuple[bool, int]:
        """Check if alert should be sent based on rate limiting.

        Args:
            alert: The alert to check

        Returns:
            Tuple of (should_send, suppressed_count)
            - should_send: True if this is a new alert or cooldown expired
            - suppressed_count: Number of similar alerts suppressed since last send
        """
        cache = await self._get_cache()
        key = self._get_dedup_key(alert)
        cooldown = self._get_cooldown_seconds(alert.severity)

        try:
            # Try to get existing count
            existing = await cache.get(key)

            if existing is None:
                # First occurrence - set counter and allow send
                await cache.set(key, 1, expire=cooldown)
                logger.debug(
                    "alert_rate_limit_new",
                    dedup_key=alert.dedup_key,
                    cooldown=cooldown,
                )
                return True, 0

            # Increment suppressed count
            new_count = await cache.increment(key)
            logger.debug(
                "alert_rate_limit_suppressed",
                dedup_key=alert.dedup_key,
                suppressed_count=new_count - 1,
            )
            return False, new_count - 1

        except Exception as e:
            # On Redis failure, allow the alert through
            logger.warning(
                "alert_rate_limit_error",
                error=str(e),
                msg="Redis error, allowing alert through",
            )
            return True, 0

    async def get_suppressed_count(self, alert: SlackAlert) -> int:
        """Get current suppressed count for an alert type."""
        cache = await self._get_cache()
        key = self._get_dedup_key(alert)

        try:
            count = await cache.get(key)
            return int(count) - 1 if count else 0
        except Exception:
            return 0

    async def reset_cooldown(self, alert: SlackAlert) -> None:
        """Reset cooldown for an alert (e.g., after manual acknowledgment)."""
        cache = await self._get_cache()
        key = self._get_dedup_key(alert)

        try:
            await cache.delete(key)
            logger.info(
                "alert_cooldown_reset",
                dedup_key=alert.dedup_key,
            )
        except Exception as e:
            logger.warning(
                "alert_cooldown_reset_error",
                error=str(e),
            )


class AlertQueue:
    """Priority queue for Slack alerts.

    Alerts are queued with priority based on severity:
    - CRITICAL: 0 (highest priority)
    - WARN: 1
    - INFO: 2 (lowest priority)
    """

    def __init__(self, cache: RedisCacheService | None = None) -> None:
        self._cache = cache

    async def _get_cache(self) -> RedisCacheService:
        """Get or create cache instance."""
        if self._cache is None:
            self._cache = RedisCacheService()
        return self._cache

    def _get_priority(self, severity: AlertSeverity) -> int:
        """Get queue priority for severity (lower = higher priority)."""
        return {
            AlertSeverity.CRITICAL: 0,
            AlertSeverity.WARN: 1,
            AlertSeverity.INFO: 2,
        }[severity]

    async def enqueue(self, alert: SlackAlert) -> bool:
        """Add alert to processing queue.

        Args:
            alert: Alert to queue

        Returns:
            True if successfully queued
        """
        cache = await self._get_cache()
        client = await cache._get_client()

        try:
            import json
            priority = self._get_priority(alert.severity)
            alert_data = json.dumps({
                "severity": alert.severity.value,
                "title": alert.title,
                "service": alert.service.value,
                "timestamp": alert.timestamp.isoformat(),
                "correlation_id": alert.correlation_id,
                "tenant_id": alert.tenant_id,
                "order_id": alert.order_id,
                "amount": str(alert.amount) if alert.amount else None,
                "currency": alert.currency,
                "mention_users": alert.mention_users,
                "notes": alert.notes,
                "details": alert.details,
                "risk_signals": alert.risk_signals,
                "sentry_url": alert.sentry_url,
                "runbook_url": alert.runbook_url,
                "dashboard_url": alert.dashboard_url,
            })

            # Use sorted set for priority queue
            await client.zadd(QUEUE_KEY, {alert_data: priority})

            logger.debug(
                "alert_queued",
                correlation_id=alert.correlation_id,
                priority=priority,
            )
            return True

        except Exception as e:
            logger.error(
                "alert_queue_error",
                error=str(e),
                correlation_id=alert.correlation_id,
            )
            return False

    async def dequeue(self) -> SlackAlert | None:
        """Get highest priority alert from queue.

        Returns:
            Alert or None if queue is empty
        """
        cache = await self._get_cache()
        client = await cache._get_client()

        try:
            import json
            from datetime import datetime
            from decimal import Decimal

            # Pop lowest score (highest priority)
            result = await client.zpopmin(QUEUE_KEY)

            if not result:
                return None

            alert_data = json.loads(result[0][0])

            return SlackAlert(
                severity=AlertSeverity(alert_data["severity"]),
                title=alert_data["title"],
                service=alert_data["service"],
                timestamp=datetime.fromisoformat(alert_data["timestamp"]),
                correlation_id=alert_data["correlation_id"],
                tenant_id=alert_data.get("tenant_id"),
                order_id=alert_data.get("order_id"),
                amount=Decimal(alert_data["amount"]) if alert_data.get("amount") else None,
                currency=alert_data.get("currency", "EGP"),
                mention_users=alert_data.get("mention_users", []),
                notes=alert_data.get("notes"),
                details=alert_data.get("details", {}),
                risk_signals=alert_data.get("risk_signals", []),
                sentry_url=alert_data.get("sentry_url"),
                runbook_url=alert_data.get("runbook_url"),
                dashboard_url=alert_data.get("dashboard_url"),
            )

        except Exception as e:
            logger.error(
                "alert_dequeue_error",
                error=str(e),
            )
            return None

    async def queue_length(self) -> int:
        """Get number of alerts in queue."""
        cache = await self._get_cache()
        client = await cache._get_client()

        try:
            return await client.zcard(QUEUE_KEY)
        except Exception:
            return 0


# Global instances
_rate_limiter: AlertRateLimiter | None = None
_alert_queue: AlertQueue | None = None


def get_rate_limiter() -> AlertRateLimiter:
    """Get global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AlertRateLimiter()
    return _rate_limiter


def get_alert_queue() -> AlertQueue:
    """Get global alert queue instance."""
    global _alert_queue
    if _alert_queue is None:
        _alert_queue = AlertQueue()
    return _alert_queue
