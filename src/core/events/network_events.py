"""Network reputation domain events (P1-7.5 — the Trust Network contribution feed).

Emitted when NUMU records a COD outcome (order / delivery / rto / refund) into its
internal ``network_reputation`` graph, so a post-commit handler can mirror it to the
standalone NUMU Trust Network's ``POST /v1/events`` — the cross-partner contribution
feed that builds the shared reputation graph and (Phase 2) the ML training set.

Per constitution Principle II the event carries the already-hashed phone (``phone_hash``)
— never raw PII. Because NUMU's ``phone_hash`` and the Trust Network's token are the same
HMAC-SHA256(salt, E164) with a shared salt (see the tokenization-parity test), that hash
IS the network token, so the handler can forward it directly.
"""

from __future__ import annotations

from uuid import UUID

from src.core.events.base import DomainEvent


class NetworkOutcomeRecordedEvent(DomainEvent):
    """A COD outcome was recorded for a buyer in NUMU's internal reputation graph.

    ``dedup_key`` is the idempotency anchor when present (the delivery/rto/reconciliation
    paths supply ``{store_id}:{order_id}:{event_type}``); it is ``None`` on the
    order/refund paths, and the handler falls back to the event id.
    """

    store_id: UUID
    phone_hash: str
    event_type: str  # order | delivery | rto | refund
    dedup_key: str | None = None
