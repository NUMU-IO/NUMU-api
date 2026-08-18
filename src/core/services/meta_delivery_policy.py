"""Delivery policy for Meta Conversions API events — pure decisions, no I/O.

Everything here answers one of four questions about a single event:

  * how urgent is it                      -> :func:`priority_for`
  * is this failure worth another attempt -> :func:`classify_status`
  * when should the next attempt happen   -> :func:`next_attempt_delay`
  * is it still legal to send it at all   -> :func:`expires_at_for`

Kept free of DB and HTTP on purpose: these are the rules most likely to be
wrong, and rules that need a Postgres fixture to test do not get tested.

──────────────────────────────────────────────────────────────────────
The invariant every rule here serves
──────────────────────────────────────────────────────────────────────
Meta deduplicates a repeated ``(pixel_id, event_name, event_id)`` for 48
hours. A resend INSIDE that window is merged with the copy Meta already
holds. A resend OUTSIDE it is a brand-new event — it double-counts the
conversion and inflates the merchant's reported revenue.

So retrying is only safe while the event is inside its dedup window, and
"give up" is not a degraded outcome — past that line, NOT sending is the
correct behaviour and sending is a data-integrity bug. Expiry is a
correctness rule here, not a housekeeping convenience.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

# ──────────────────────────────────────────────────────────────────────
# Windows
# ──────────────────────────────────────────────────────────────────────

# Meta's deduplication window for a repeated (pixel, event_name, event_id).
# Mirrors ``_META_DEDUP_WINDOW_SECONDS`` in the CAPI task, which uses it to
# gate identity-enrichment resends. Same number, same reason.
DEDUP_WINDOW = timedelta(hours=48)

# Meta rejects a CAPI event whose ``event_time`` is more than 7 days old.
# Retries preserve the ORIGINAL event_time (never "now" — that would move
# the conversion in time), so this is a hard ceiling on any resend.
MAX_EVENT_AGE = timedelta(days=7)

# A claimed row is leased to one worker for this long. If that worker dies
# between claim and send, the lease lapses and the row becomes claimable
# again — crash recovery with no extra bookkeeping.
#
# 10 minutes: comfortably longer than one attempt's own ceiling (a 15s HTTP
# timeout plus Celery's <=300s backoff ladder), short enough that a crash
# during an outage does not idle the event for an hour.
CLAIM_LEASE = timedelta(minutes=10)


# ──────────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────────


class DeliveryStatus(StrEnum):
    """Lifecycle of one outbox row.

    Stored as TEXT rather than a Postgres enum: adding a value to a native
    enum takes a DDL lock, and this vocabulary will grow (a ``throttled``
    state is already plausible).
    """

    # Persisted, not yet acknowledged by Meta. Covers "queued" and "in
    # flight" — we deliberately do not distinguish, because a worker that
    # died mid-flight is indistinguishable from one that never started, and
    # the same lease expiry recovers both.
    PENDING = "pending"
    # Meta returned 2xx. Terminal.
    SENT = "sent"
    # Failed, retryable, waiting for ``next_retry_at``.
    RETRYING = "retrying"
    # Failed permanently — bad payload, dead token, unknown pixel. Terminal.
    # Retrying cannot fix it; a human must.
    FAILED = "failed"
    # Retryable, but the attempt budget ran out before the window did.
    # Terminal. The distinction from FAILED matters: this one WOULD have
    # succeeded eventually, so a run of these means Meta or the network was
    # down, not that the merchant is misconfigured.
    DEAD_LETTER = "dead_letter"
    # Still retryable, but past the point where sending is safe. Terminal.
    # Not a failure of ours — see the module docstring.
    EXPIRED = "expired"
    # Deliberately not sent (CAPI switched off mid-flight, no match keys).
    # Terminal, and counted as an error nowhere.
    SKIPPED = "skipped"
    # Rows written before the lifecycle columns existed. Their outcome is
    # only knowable from ``response_status``, and nothing may retry them —
    # every one is far outside its dedup window by now. Terminal.
    LEGACY = "legacy"


TERMINAL_STATUSES: frozenset[str] = frozenset({
    DeliveryStatus.SENT,
    DeliveryStatus.FAILED,
    DeliveryStatus.DEAD_LETTER,
    DeliveryStatus.EXPIRED,
    DeliveryStatus.SKIPPED,
    DeliveryStatus.LEGACY,
})

# Statuses that mean "delivery is still owed". Used by the observability
# counters so a new state cannot silently drop out of the pending count.
OPEN_STATUSES: frozenset[str] = frozenset({
    DeliveryStatus.PENDING,
    DeliveryStatus.RETRYING,
})


class FailureKind(StrEnum):
    """Why an attempt failed, at the granularity retry decisions need."""

    TRANSPORT = "transport"  # connect/read timeout, DNS, reset
    RATE_LIMITED = "rate_limited"  # Meta is throttling this app/dataset
    SERVER_ERROR = "server_error"  # 5xx, or Meta's transient error codes
    INVALID_PAYLOAD = "invalid_payload"  # we built something Meta rejects
    INVALID_CREDENTIALS = "invalid_credentials"  # token dead/revoked/wrong scope
    PERMANENT = "permanent"  # everything else Meta refuses

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE_KINDS


_RETRYABLE_KINDS: frozenset[FailureKind] = frozenset({
    FailureKind.TRANSPORT,
    FailureKind.RATE_LIMITED,
    FailureKind.SERVER_ERROR,
})


# ──────────────────────────────────────────────────────────────────────
# Failure classification
# ──────────────────────────────────────────────────────────────────────

# Meta returns throttling as HTTP **400** with a code in the body, not as
# 429. Treating "4xx = permanent" therefore discarded every event sent while
# a store was being rate-limited — the precise moment it had the most
# traffic worth measuring.
#
#   4     Application request limit reached
#   17    User request limit reached
#   32    Page request limit reached
#   341   Application limit reached
#   613   Calls to this API have exceeded the rate limit
#   80004 Too many calls to this ad account / dataset
_RATE_LIMIT_CODES: frozenset[int] = frozenset({4, 17, 32, 341, 613, 80004})

# Also 400-shaped, also transient. Meta's own guidance is to retry these.
#   1  API Unknown — "possibly a temporary issue"
#   2  API Service — "temporary issue due to downtime"
_TRANSIENT_CODES: frozenset[int] = frozenset({1, 2})

# Token problems. Retrying is pointless — the merchant must reconnect — but
# they earn their own kind so the hub can say WHY instead of showing a
# generic failure.
#   102  Session key invalid or expired
#   190  Invalid OAuth 2.0 access token
#   463  Access token has expired
#   467  Access token is invalid (password change / logout)
_CREDENTIAL_CODES: frozenset[int] = frozenset({102, 190, 463, 467})

# CAPI's payload-validation subcode family (missing user_data, bad currency,
# malformed content_ids, …). Distinct from rate limiting, which shares the
# same HTTP 400.
_PAYLOAD_SUBCODE_RANGE = range(2_804_000, 2_805_000)


def classify_status(status: int, body: Any = None) -> FailureKind | None:
    """Classify one CAPI HTTP response. ``None`` means success.

    ``body`` is Meta's parsed JSON when we have it; classification still
    works without it, just more coarsely.
    """
    if 200 <= status < 300:
        return None
    if status == 429:
        return FailureKind.RATE_LIMITED
    if status >= 500:
        return FailureKind.SERVER_ERROR

    code, subcode, err_type = _error_fields(body)

    if code in _RATE_LIMIT_CODES:
        return FailureKind.RATE_LIMITED
    if code in _TRANSIENT_CODES:
        return FailureKind.SERVER_ERROR
    if code in _CREDENTIAL_CODES or err_type == "OAuthException":
        # OAuthException also covers permission errors (code 200/10), which
        # are equally unfixable by retrying and equally a "reconnect Meta"
        # message to the merchant.
        return FailureKind.INVALID_CREDENTIALS
    if status in (401, 403):
        return FailureKind.INVALID_CREDENTIALS
    if code == 100 or (subcode is not None and subcode in _PAYLOAD_SUBCODE_RANGE):
        return FailureKind.INVALID_PAYLOAD
    if status == 400:
        return FailureKind.INVALID_PAYLOAD
    return FailureKind.PERMANENT


def _error_fields(body: Any) -> tuple[int | None, int | None, str | None]:
    """Pull ``(code, error_subcode, type)`` out of a Meta error envelope."""
    if not isinstance(body, dict):
        return None, None, None
    error = body.get("error")
    if not isinstance(error, dict):
        error = body
    err_type = error.get("type")
    return (
        _as_int(error.get("code")),
        _as_int(error.get("error_subcode")),
        err_type if isinstance(err_type, str) else None,
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────
# Priority
# ──────────────────────────────────────────────────────────────────────

# Lower sorts first, both in the sweep's ORDER BY and as the stored value.
PRIORITY_CONVERSION = 0
PRIORITY_STANDARD = 1
PRIORITY_BULK = 2

# Revenue. A lost one is a lost sale the merchant will optimise ad spend
# against; nothing else in the funnel has that property.
CONVERSION_EVENTS: frozenset[str] = frozenset({
    "Purchase",
    "DeliveredOrder",  # the true COD conversion moment
    "Subscribe",
    "StartTrial",
    "Refund",
})

# Volume. One per pageview per pixel, and the browser pixel carries them
# independently, so the server leg is corroboration rather than the only
# record.
BULK_EVENTS: frozenset[str] = frozenset({
    "PageView",
})

# Celery queue names. Separate queues rather than message priorities:
# Kombu's Redis transport round-robins across the queues a worker consumes,
# so a 100k-deep PageView backlog cannot starve a Purchase. Redis message
# priority, by contrast, is emulated with per-priority sub-queues and does
# not compose cleanly with ``visibility_timeout`` redelivery.
QUEUE_CONVERSION = "capi_priority"
QUEUE_STANDARD = "capi"


def priority_for(event_name: str) -> int:
    if event_name in CONVERSION_EVENTS:
        return PRIORITY_CONVERSION
    if event_name in BULK_EVENTS:
        return PRIORITY_BULK
    return PRIORITY_STANDARD


def queue_for(event_name: str) -> str:
    """Conversions get their own queue; everything else shares one."""
    if priority_for(event_name) == PRIORITY_CONVERSION:
        return QUEUE_CONVERSION
    return QUEUE_STANDARD


# ──────────────────────────────────────────────────────────────────────
# Backoff
# ──────────────────────────────────────────────────────────────────────

# The ladder the DB-driven sweep walks, in seconds, AFTER Celery's own
# in-broker retries are spent. Celery covers the blip (six attempts, <=300s
# apart, all inside ~10 minutes); this covers the outage.
#
# 5m -> 20m -> 1h -> 4h -> 12h sums to ~17.4h, leaving >30h of headroom
# inside the 48h dedup window — so even the last attempt for the last event
# of a long incident is still a merge rather than a double-count. Each rung
# is ~4x the last, so a short outage costs two cheap attempts and a long one
# does not spin.
RETRY_LADDER: tuple[int, ...] = (300, 1_200, 3_600, 14_400, 43_200)

# Attempts the sweep will make. Equal to the ladder length by construction —
# named separately because the ceiling is the contract and the ladder is one
# implementation of it.
MAX_SWEEP_ATTEMPTS = len(RETRY_LADDER)

# +/-20%. Enough to break up the thundering herd when an outage ends and
# thousands of rows come due in the same second; small enough that the
# ladder's shape still holds.
_JITTER_FRACTION = 0.2


def next_attempt_delay(sweep_attempt: int, *, jitter_seed: int = 0) -> int | None:
    """Seconds to wait before sweep attempt ``sweep_attempt`` (1-based).

    ``None`` once the budget is spent — the caller must dead-letter.

    Jitter is derived from ``jitter_seed`` (the row's id, in practice)
    rather than drawn randomly, so the same row always computes the same
    delay. A sweep that recomputed a different value on every pass could not
    converge, and this number lands in a DB column that must not drift on
    re-read.
    """
    if sweep_attempt < 1 or sweep_attempt > len(RETRY_LADDER):
        return None
    base = RETRY_LADDER[sweep_attempt - 1]
    # Map the seed deterministically onto [-1, 1].
    offset = ((jitter_seed % 2001) - 1000) / 1000.0
    return max(1, int(base * (1 + _JITTER_FRACTION * offset)))


def backoff_from_retry_after(header_value: Any, fallback: int) -> int:
    """Honour Meta's ``Retry-After`` when it sends one, else ``fallback``.

    Capped at an hour: a pathological header must not park a row past the
    point where the next rung of the ladder would have fired anyway.
    """
    seconds = _as_int(header_value)
    if seconds and seconds > 0:
        return min(seconds, 3_600)
    return fallback


# ──────────────────────────────────────────────────────────────────────
# Expiry
# ──────────────────────────────────────────────────────────────────────


def expires_at_for(event_time: datetime) -> datetime:
    """The last instant this event may be sent.

    Measured from ``event_time`` — the moment the conversion happened, and
    the value Meta stamps the event with — NOT from row creation. A row the
    orphan sweep creates hours after the sale is already hours into its
    window, and pretending otherwise is how a "recovered" conversion becomes
    a duplicated one.
    """
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=UTC)
    return event_time + DEDUP_WINDOW


def is_expired(event_time: datetime, *, now: datetime | None = None) -> bool:
    """True when sending this event would create a duplicate or be refused."""
    now = now or datetime.now(UTC)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=UTC)
    # Either bound disqualifies: past the dedup window a resend
    # double-counts, past MAX_EVENT_AGE Meta refuses it outright.
    return now >= expires_at_for(event_time) or now - event_time >= MAX_EVENT_AGE
