"""Celery task: propagate GDPR erasure to the standalone Trust Network.

The customers/redact webhook handler erases NUMU's local data
synchronously, then enqueues this task so the TN-side erasure is
guaranteed *eventually* without coupling the Shopify webhook ACK to the
TN's availability (Shopify only gives us 48h of redeliveries; GDPR
gives 30 days — an internal retry queue fits the deadlines better).

Retry policy: exponential backoff up to ~10 attempts spanning hours.
On final failure the canonical ``.alert()`` fires (ops must finish the
erasure by hand — see docs/security/trust-network-isolation.md).
"""

from __future__ import annotations

import asyncio

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.trust_network.erase_subject",
    bind=True,
    max_retries=10,
    retry_backoff=True,
    retry_backoff_max=3600,
    retry_jitter=True,
    soft_time_limit=30,
)
def erase_subject_from_trust_network(self, phone_hash: str, reason: str = "") -> dict:
    """Erase one buyer token from the standalone TN, retrying on failure."""
    from src.application.services.trust_network_privacy import post_erasure

    done, detail = _run_async(post_erasure(phone_hash))
    if done:
        return {"status": "done", "detail": detail}

    if self.request.retries >= self.max_retries:
        logger.alert(
            "trust_network_erasure_exhausted",
            token_prefix=phone_hash[:8] if phone_hash else "",
            reason=reason,
            detail=detail,
        )
        return {"status": "failed", "detail": detail}

    # Manual self.retry ignores retry_backoff — compute the ladder here:
    # 30s, 1m, 2m, 4m, ... capped at 1h (~4h+ total across 10 attempts).
    countdown = min(3600, 30 * (2**self.request.retries))
    raise self.retry(
        countdown=countdown, exc=RuntimeError(f"tn_erasure_retry: {detail}")
    )
