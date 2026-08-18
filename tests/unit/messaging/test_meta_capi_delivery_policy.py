"""The rules that decide whether a Meta CAPI event is retried, and when.

These are pure functions on purpose — the decisions most likely to be wrong
are the ones that would otherwise need a Postgres fixture and a live Celery
worker to exercise, which is another way of saying they would not be tested.

The invariant behind almost every case here: Meta merges a repeated
``(pixel_id, event_name, event_id)`` for 48 hours and treats anything later
as a NEW event. So "retry" and "double-count the merchant's revenue" are the
same operation performed at different times, and the boundary between them
is the whole point.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.services import meta_delivery_policy as policy

# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class TestClassifyStatus:
    """What Meta's answer means for whether we try again."""

    def test_2xx_is_not_a_failure(self):
        assert policy.classify_status(200) is None
        assert policy.classify_status(204, {"events_received": 1}) is None

    def test_5xx_is_retryable(self):
        for status in (500, 502, 503, 504):
            kind = policy.classify_status(status)
            assert kind is policy.FailureKind.SERVER_ERROR
            assert kind.retryable

    def test_429_is_retryable(self):
        assert policy.classify_status(429).retryable

    @pytest.mark.parametrize("code", [4, 17, 32, 341, 613, 80004])
    def test_meta_sends_rate_limits_as_http_400_and_they_are_retryable(self, code):
        """The defect this classifier exists to fix.

        Meta returns throttling as a **400** with the reason in the body, not
        as a 429. The old rule was "4xx is permanent", so every event a store
        sent while being rate-limited was discarded and never retried — and a
        store gets rate-limited precisely when it has the most traffic worth
        measuring.
        """
        kind = policy.classify_status(400, {"error": {"code": code}})
        assert kind is policy.FailureKind.RATE_LIMITED
        assert kind.retryable

    @pytest.mark.parametrize("code", [1, 2])
    def test_metas_own_transient_codes_are_retryable(self, code):
        kind = policy.classify_status(400, {"error": {"code": code}})
        assert kind is policy.FailureKind.SERVER_ERROR
        assert kind.retryable

    @pytest.mark.parametrize("code", [102, 190, 463, 467])
    def test_dead_tokens_are_permanent_and_named(self, code):
        """Retrying cannot fix a revoked token — a human reconnecting can.

        Its own kind, rather than a generic failure, so the merchant panel
        can say "reconnect Meta" instead of "something went wrong".
        """
        kind = policy.classify_status(
            400, {"error": {"code": code, "type": "OAuthException"}}
        )
        assert kind is policy.FailureKind.INVALID_CREDENTIALS
        assert not kind.retryable

    def test_403_is_a_credentials_problem(self):
        assert policy.classify_status(403) is policy.FailureKind.INVALID_CREDENTIALS

    def test_invalid_parameter_is_permanent(self):
        kind = policy.classify_status(400, {"error": {"code": 100}})
        assert kind is policy.FailureKind.INVALID_PAYLOAD
        assert not kind.retryable

    def test_capi_validation_subcodes_are_permanent(self):
        kind = policy.classify_status(
            400, {"error": {"code": 100, "error_subcode": 2804050}}
        )
        assert kind is policy.FailureKind.INVALID_PAYLOAD

    def test_unparseable_body_still_classifies_by_status(self):
        """Meta occasionally answers with HTML through a proxy."""
        assert policy.classify_status(503, {"raw": "<html>502</html>"}).retryable
        assert not policy.classify_status(400, {"raw": "nope"}).retryable

    def test_bare_error_object_without_wrapper_is_understood(self):
        assert (
            policy.classify_status(400, {"code": 613})
            is policy.FailureKind.RATE_LIMITED
        )


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


class TestPriority:
    """A lost Purchase is a lost sale; a lost PageView is not."""

    def test_conversions_outrank_everything(self):
        for name in ("Purchase", "DeliveredOrder", "Subscribe", "Refund"):
            assert policy.priority_for(name) == policy.PRIORITY_CONVERSION
            assert policy.queue_for(name) == policy.QUEUE_CONVERSION

    def test_pageview_is_bulk(self):
        assert policy.priority_for("PageView") == policy.PRIORITY_BULK

    def test_funnel_events_are_standard(self):
        for name in ("ViewContent", "AddToCart", "InitiateCheckout", "Lead"):
            assert policy.priority_for(name) == policy.PRIORITY_STANDARD

    def test_bulk_and_standard_share_a_queue_but_not_the_conversion_one(self):
        """Separate queues are what stop a PageView flood delaying a sale.

        Kombu's Redis transport round-robins across the queues a worker
        consumes, so a 100k-deep bulk backlog cannot starve the conversion
        queue — which one shared FIFO ``default`` queue absolutely did.
        """
        assert policy.queue_for("PageView") == policy.QUEUE_STANDARD
        assert policy.queue_for("ViewContent") == policy.QUEUE_STANDARD
        assert policy.QUEUE_CONVERSION != policy.QUEUE_STANDARD

    def test_unknown_event_is_standard_not_bulk(self):
        """An event we have never seen is more likely meaningful than noise."""
        assert policy.priority_for("SomeCustomEvent") == policy.PRIORITY_STANDARD


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


class TestBackoff:
    def test_ladder_is_strictly_increasing(self):
        assert list(policy.RETRY_LADDER) == sorted(policy.RETRY_LADDER)
        assert len(set(policy.RETRY_LADDER)) == len(policy.RETRY_LADDER)

    def test_whole_ladder_fits_inside_the_dedup_window(self):
        """The load-bearing property of the entire retry design.

        If the attempts summed past 48h, the last one would arrive after Meta
        stopped deduplicating and would be counted as a SECOND conversion.
        The ladder is only safe because it finishes with hours to spare.
        """
        total = sum(policy.RETRY_LADDER) * (1 + 0.2)  # worst-case jitter
        assert total < policy.DEDUP_WINDOW.total_seconds()

    def test_delay_grows_with_attempt(self):
        delays = [
            policy.next_attempt_delay(n)
            for n in range(1, policy.MAX_SWEEP_ATTEMPTS + 1)
        ]
        assert all(d is not None for d in delays)
        assert delays == sorted(delays)

    def test_budget_runs_out(self):
        assert policy.next_attempt_delay(policy.MAX_SWEEP_ATTEMPTS) is not None
        assert policy.next_attempt_delay(policy.MAX_SWEEP_ATTEMPTS + 1) is None
        assert policy.next_attempt_delay(0) is None

    def test_jitter_is_deterministic_per_row(self):
        """A row must compute the same next attempt every time it is read.

        The value is persisted in ``next_retry_at``; a random one would make
        the schedule drift on every pass and the ladder would never converge.
        """
        first = policy.next_attempt_delay(2, jitter_seed=12345)
        assert first == policy.next_attempt_delay(2, jitter_seed=12345)
        assert first != policy.next_attempt_delay(2, jitter_seed=999)

    def test_jitter_stays_within_twenty_percent(self):
        base = policy.RETRY_LADDER[2]
        for seed in range(0, 5000, 97):
            delay = policy.next_attempt_delay(3, jitter_seed=seed)
            assert 0.8 * base <= delay <= 1.2 * base

    def test_retry_after_header_wins_when_meta_sends_one(self):
        assert policy.backoff_from_retry_after("90", 300) == 90

    def test_retry_after_is_capped(self):
        """A pathological header must not park a row past its own ladder."""
        assert policy.backoff_from_retry_after("999999", 300) == 3600

    def test_absent_or_junk_retry_after_falls_back(self):
        for value in (None, "", "soon", "-5", "0"):
            assert policy.backoff_from_retry_after(value, 300) == 300


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expiry_is_measured_from_the_conversion_not_the_row(self):
        """A recovered event is already partway through its window.

        The orphan sweep can create a row hours after the sale. Measuring the
        window from row creation would hand it a fresh 48h and let a resend
        land outside Meta's dedup window as a second conversion.
        """
        happened = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
        assert policy.expires_at_for(happened) == happened + timedelta(hours=48)

    def test_fresh_event_is_not_expired(self):
        now = datetime.now(UTC)
        assert not policy.is_expired(now - timedelta(minutes=5), now=now)

    def test_event_past_the_dedup_window_is_expired(self):
        now = datetime.now(UTC)
        assert policy.is_expired(now - timedelta(hours=49), now=now)

    def test_boundary_is_inclusive(self):
        """At exactly 48h Meta has stopped merging. Do not send."""
        now = datetime.now(UTC)
        assert policy.is_expired(now - policy.DEDUP_WINDOW, now=now)

    def test_naive_timestamps_are_treated_as_utc(self):
        """``event_time`` round-trips through columns that can lose tzinfo."""
        naive = datetime.utcnow() - timedelta(hours=1)
        assert not policy.is_expired(naive)

    def test_max_event_age_is_the_outer_bound(self):
        """Meta refuses a CAPI event whose event_time is over 7 days old."""
        assert policy.MAX_EVENT_AGE == timedelta(days=7)
        assert policy.DEDUP_WINDOW < policy.MAX_EVENT_AGE


# ---------------------------------------------------------------------------
# Lifecycle vocabulary
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_terminal_statuses_are_not_claimable(self):
        assert policy.DeliveryStatus.PENDING not in policy.TERMINAL_STATUSES
        assert policy.DeliveryStatus.RETRYING not in policy.TERMINAL_STATUSES
        for status in (
            policy.DeliveryStatus.SENT,
            policy.DeliveryStatus.FAILED,
            policy.DeliveryStatus.DEAD_LETTER,
            policy.DeliveryStatus.EXPIRED,
            policy.DeliveryStatus.SKIPPED,
            policy.DeliveryStatus.LEGACY,
        ):
            assert status in policy.TERMINAL_STATUSES

    def test_open_and_terminal_partition_the_vocabulary(self):
        """Every state is exactly one of "still owed" or "done".

        A state in neither would be invisible to both the delivery sweep and
        the pending counters — owed forever and reported nowhere.
        """
        every = set(policy.DeliveryStatus)
        assert policy.OPEN_STATUSES | policy.TERMINAL_STATUSES == every
        assert not (policy.OPEN_STATUSES & policy.TERMINAL_STATUSES)

    def test_only_transient_kinds_are_retryable(self):
        retryable = {k for k in policy.FailureKind if k.retryable}
        assert retryable == {
            policy.FailureKind.TRANSPORT,
            policy.FailureKind.RATE_LIMITED,
            policy.FailureKind.SERVER_ERROR,
        }

    def test_legacy_rows_can_never_be_retried(self):
        """Rows predating the lifecycle are all far outside their window."""
        assert policy.DeliveryStatus.LEGACY in policy.TERMINAL_STATUSES
