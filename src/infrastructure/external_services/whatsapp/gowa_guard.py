"""Send guard for the GOWA transport — pacing, warm-up, scope and health.

Meta enforces its own limits: templates are reviewed, marketing is frequency
capped, and a number that misbehaves gets rate limited rather than destroyed.
GOWA has none of that. It sends from a real WhatsApp account, so the only thing
standing between an over-eager notification loop and a banned merchant number is
this module.

Every control here targets a *specific* thing WhatsApp reacts to:

* **Pacing + jitter** — automation is recognisable by its timing. Bursts of
  perfectly-spaced messages look like nothing a human does. Per-minute and
  per-hour caps bound the burst; jitter breaks the rhythm.
* **Warm-up** — a freshly linked device that immediately sends hundreds of
  messages is the classic ban shape. The daily allowance is a function of how
  long the session has existed, so volume earns its way up.
* **Scope** — the freedom GOWA gives (no template review, no frequency cap) is
  exactly what gets abused. Order-lifecycle messages go to someone who just
  bought and are near-zero report risk; bulk marketing to a cold list is the
  opposite. Stores declare which message types may use this transport.
* **Health** — a session that keeps dropping, or a run of failures, is a signal
  to stop rather than push harder.

Reputation lives on the phone NUMBER, not the session, so none of this is
recoverable by re-pairing — which is why the guard runs before every send.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime

from src.core.interfaces.services.messaging_service import MessageType
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ALLOWED_TYPES",
    "GowaSendGuard",
    "GuardDecision",
    "warmup_daily_cap",
]

# Per-device ceilings. Deliberately well under what WhatsApp tolerates: the cost
# of a notification arriving a minute late is nil, the cost of a banned merchant
# number is their entire customer channel.
MAX_PER_MINUTE = 12
MAX_PER_HOUR = 250

# Spacing between sends, seconds. Randomised so the gap never forms a pattern —
# a fixed sleep is just a slower machine gun.
JITTER_MIN_SECONDS = 1.5
JITTER_MAX_SECONDS = 6.0

# Warm-up ladder: (minimum session age in days, messages/day).
# A number that has been sending steadily for a fortnight is an established
# sender; one linked an hour ago is not, and WhatsApp treats them differently.
WARMUP_LADDER: tuple[tuple[int, int], ...] = (
    (0, 50),
    (3, 150),
    (7, 300),
    (14, 600),
)

# Message types allowed over GOWA unless a store says otherwise.
#
# This is FULL PARITY with what the platform already sends over Meta, because
# GOWA is a transport swap: a merchant moved onto it should keep the behaviour
# they had, not silently lose their recovery nudges. Excluding a type here does
# not merely skip it — the send fails, and the merchant sees an error on a
# button that used to work.
#
# ABANDONED_CART and COD_RECOVERY_OFFER carry materially more risk than the
# rest: they are marketing-shaped, they go to people who did NOT complete a
# purchase, and they are exactly what Meta's 131049 frequency cap exists to
# limit. They are included for parity, but they are the first thing to remove
# via `whatsapp.gowa_allowed_types` if a number starts attracting reports —
# report rate is what gets a number banned, and these generate it fastest.
DEFAULT_ALLOWED_TYPES: frozenset[str] = frozenset({
    str(MessageType.ORDER_CONFIRMATION),
    str(MessageType.ORDER_CONFIRMATION_REQUEST),
    str(MessageType.ORDER_SHIPPED),
    str(MessageType.OUT_FOR_DELIVERY),
    str(MessageType.ORDER_DELIVERED),
    str(MessageType.PAYMENT_RECEIVED),
    str(MessageType.DELIVERY_CHECK),
    str(MessageType.SHIP_DIGEST),
    # Higher risk than the rest — see the note above.
    str(MessageType.ABANDONED_CART),
    str(MessageType.COD_RECOVERY_OFFER),
})

# Consecutive failures before the device is treated as unhealthy.
FAILURE_STREAK_PAUSE = 5

# ── platform-device ceilings ───────────────────────────────────────────────
#
# The shared NUMU number carries traffic for EVERY store on the shared path, so
# the per-merchant numbers above are wrong twice over.
#
# Too low: a fleet-wide 50/day warm-up would strand every store's order
# notifications for the first three days. The platform number is also an
# established account with real history, not a cold one, so the warm-up
# reasoning that protects a freshly linked merchant number doesn't apply.
#
# Too high is the real danger, and it is worth being blunt about: on the BYO
# path a ban costs one merchant. Here it takes WhatsApp away from every store at
# once. These ceilings are therefore generous enough to carry the fleet and
# still far below anything that reads as bulk, and the jitter still applies —
# volume is not what gets numbers banned, unsolicited volume is.
PLATFORM_MAX_PER_MINUTE = 20
PLATFORM_MAX_PER_HOUR = 600
PLATFORM_MAX_PER_DAY = 4000


@dataclass(frozen=True)
class GuardDecision:
    """Whether a send may proceed, and how long to wait first."""

    allowed: bool
    #: Seconds to sleep before sending. Always > 0 when allowed, by design.
    delay_seconds: float = 0.0
    #: Machine-readable refusal reason, for logs and the admin UI.
    reason: str | None = None
    detail: str | None = None


def warmup_daily_cap(paired_at: datetime | None, now: datetime | None = None) -> int:
    """Messages/day permitted for a session of this age.

    An unknown pairing time is treated as brand new: assuming a device is
    established when we cannot show that it is would defeat the point.
    """
    now = now or datetime.now(UTC)
    if paired_at is None:
        return WARMUP_LADDER[0][1]
    if paired_at.tzinfo is None:
        paired_at = paired_at.replace(tzinfo=UTC)
    age_days = max(0, (now - paired_at).days)
    cap = WARMUP_LADDER[0][1]
    for min_days, allowance in WARMUP_LADDER:
        if age_days >= min_days:
            cap = allowance
    return cap


def allowed_types_for(store_settings: dict | None) -> frozenset[str]:
    """Message types this store may send over GOWA.

    ``settings.whatsapp.gowa_allowed_types`` overrides the transactional
    default. An empty list is honoured as "nothing" rather than falling back —
    an operator disabling every type must not silently re-enable them all.
    """
    node = (store_settings or {}).get("whatsapp")
    if not isinstance(node, dict):
        return DEFAULT_ALLOWED_TYPES
    configured = node.get("gowa_allowed_types")
    if configured is None:
        return DEFAULT_ALLOWED_TYPES
    if not isinstance(configured, list):
        return DEFAULT_ALLOWED_TYPES
    return frozenset(str(t) for t in configured)


class GowaSendGuard:
    """Consulted before every GOWA send."""

    def __init__(self, cache: RedisCacheService | None = None) -> None:
        self.cache = cache or RedisCacheService()

    @staticmethod
    def _keys(device_id: str, now: datetime) -> tuple[str, str, str]:
        return (
            f"gowa:rate:{device_id}:m:{now.strftime('%Y%m%d%H%M')}",
            f"gowa:rate:{device_id}:h:{now.strftime('%Y%m%d%H')}",
            f"gowa:rate:{device_id}:d:{now.strftime('%Y%m%d')}",
        )

    async def check(
        self,
        *,
        device_id: str,
        message_type: str | None,
        paired_at: datetime | None,
        device_status: str | None,
        store_settings: dict | None = None,
        is_platform: bool = False,
    ) -> GuardDecision:
        """Decide whether this send may proceed.

        Ordered cheapest-and-most-absolute first: a logged-out session or an
        out-of-scope message type can never be fixed by waiting, so those are
        rejected before any counter is touched.
        """
        # 1. Health. A dead session cannot deliver, and hammering it produces
        #    a stream of failures that looks worse than silence.
        if device_status in {"logged_out", "banned"}:
            return GuardDecision(
                allowed=False,
                reason="device_unhealthy",
                detail=f"Device status is '{device_status}'; re-pairing is required.",
            )

        # 2. Scope.
        if message_type is not None:
            allowed = allowed_types_for(store_settings)
            if message_type not in allowed:
                return GuardDecision(
                    allowed=False,
                    reason="type_not_allowed",
                    detail=(
                        f"'{message_type}' is not permitted over GOWA for this "
                        "store. Transactional messages only by default; enable "
                        "it explicitly via whatsapp.gowa_allowed_types."
                    ),
                )

        now = datetime.now(UTC)
        minute_key, hour_key, day_key = self._keys(device_id, now)

        # 3+4. Failure streak and counters. Both live inside one try: Redis
        # being unavailable must never stop a merchant's order notifications.
        # Pacing is a risk control, not a correctness one — so we log loudly and
        # fall through to an allow-with-jitter rather than failing the send.
        # (The streak read used to sit outside this block, which meant a Redis
        # outage raised straight out of the guard and broke every send.)
        try:
            streak = await self.cache.get(f"gowa:fail:{device_id}") or 0
            if int(streak) >= FAILURE_STREAK_PAUSE:
                return GuardDecision(
                    allowed=False,
                    reason="failure_streak",
                    detail=(
                        f"{streak} consecutive send failures; paused pending "
                        "investigation."
                    ),
                )

            # Counters are incremented up front so concurrent workers cannot
            # both read "just under the cap" and both send.
            per_minute = await self.cache.increment(minute_key)
            per_hour = await self.cache.increment(hour_key)
            per_day = await self.cache.increment(day_key)
            # Expire slightly beyond the window so a counter can never outlive
            # its bucket and permanently block a device.
            await self.cache.set(minute_key, per_minute, ttl=120)
            await self.cache.set(hour_key, per_hour, ttl=7200)
            await self.cache.set(day_key, per_day, ttl=172800)
        except Exception:
            # Redis being down must not stop a merchant's order notifications.
            # Pacing is a risk control, not a correctness one — log loudly and
            # let the send through with jitter still applied.
            logger.exception("gowa_guard_counter_unavailable")
            return GuardDecision(allowed=True, delay_seconds=self._jitter())

        # The shared platform device carries the whole fleet, so per-merchant
        # ceilings would throttle every store at once; see PLATFORM_MAX_*.
        max_minute = PLATFORM_MAX_PER_MINUTE if is_platform else MAX_PER_MINUTE
        max_hour = PLATFORM_MAX_PER_HOUR if is_platform else MAX_PER_HOUR

        if per_minute > max_minute:
            return GuardDecision(
                allowed=False,
                reason="rate_limited_minute",
                detail=f"{per_minute - 1}/{max_minute} sent this minute.",
            )
        if per_hour > max_hour:
            return GuardDecision(
                allowed=False,
                reason="rate_limited_hour",
                detail=f"{per_hour - 1}/{max_hour} sent this hour.",
            )

        # No warm-up ramp on the platform number: it is an established account
        # with real history, and ramping it would strand every store's order
        # notifications for days.
        daily_cap = (
            PLATFORM_MAX_PER_DAY if is_platform else warmup_daily_cap(paired_at, now)
        )
        if per_day > daily_cap:
            return GuardDecision(
                allowed=False,
                reason="warmup_cap",
                detail=(
                    f"{per_day - 1}/{daily_cap} sent today; this session is "
                    "still warming up."
                ),
            )

        return GuardDecision(allowed=True, delay_seconds=self._jitter())

    @staticmethod
    def _jitter() -> float:
        """A randomised gap. Never zero — instant sends are the tell."""
        return random.uniform(JITTER_MIN_SECONDS, JITTER_MAX_SECONDS)

    async def record_failure(self, device_id: str) -> int:
        """Count a consecutive failure; trips the pause at the threshold."""
        try:
            streak = await self.cache.increment(f"gowa:fail:{device_id}")
            await self.cache.set(f"gowa:fail:{device_id}", streak, ttl=3600)
            return int(streak)
        except Exception:
            logger.exception("gowa_guard_failure_record_unavailable")
            return 0

    async def record_success(self, device_id: str) -> None:
        """Clear the streak. Only an unbroken run should pause a device."""
        try:
            await self.cache.delete(f"gowa:fail:{device_id}")
        except Exception:
            logger.exception("gowa_guard_success_record_unavailable")
