"""The outbox mechanics: claiming, leasing, batching and replaying.

The policy module decides *what should happen*; this covers the machinery
that carries it out — the parts where a mistake shows up as a duplicated
conversion or a silently dropped one rather than as an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.core.services import meta_delivery_policy as policy
from src.infrastructure.messaging.tasks import meta_capi as m


def _row(**over):
    """An outbox row as the sweep hands it to the batch sender."""
    base = {
        "id": uuid4(),
        "store_id": uuid4(),
        "pixel_id": "123456789012345",
        "event_id": "evt-1",
        "event_name": "Purchase",
        "event_time": datetime.now(UTC) - timedelta(minutes=5),
        "attempt_count": 1,
        "priority": policy.PRIORITY_CONVERSION,
        "status": policy.DeliveryStatus.RETRYING,
        "expires_at": None,
        "request_payload": {
            "event_name": "Purchase",
            "event_time": 1_755_000_000,
            "event_source_url": "https://shop.example/thank-you",
            "action_source": "website",
            "custom_data": {"value": 250.0, "currency": "EGP"},
            "user_data": {"em": "a" * 64, "ph": "b" * 64},
            "test_event_code": None,
        },
    }
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# The Celery / sweep boundary
# ---------------------------------------------------------------------------


class TestRetryHandoff:
    """Which of the two retry mechanisms owns the event right now."""

    def test_celery_owns_the_early_attempts(self):
        for retries in range(m._CELERY_MAX_RETRIES):
            assert m._celery_will_retry(
                SimpleNamespace(request=SimpleNamespace(retries=retries))
            )

    def test_sweep_takes_over_when_celery_is_spent(self):
        task = SimpleNamespace(request=SimpleNamespace(retries=m._CELERY_MAX_RETRIES))
        assert not m._celery_will_retry(task)

    def test_a_task_with_no_retry_history_is_on_its_first_attempt(self):
        """Absent means zero, and zero means Celery still has its budget."""
        assert m._celery_will_retry(SimpleNamespace())

    def test_an_uninterpretable_retry_count_hands_off_rather_than_promising(self):
        """False is the safe answer: the sweep will schedule it.

        True would promise a Celery retry that nothing performs, and the
        event would be buried by the catch-all handler — which is the bug
        the whole outbox exists to fix.
        """
        assert not m._celery_will_retry(
            SimpleNamespace(request=SimpleNamespace(retries="lots"))
        )

    def test_ladder_starts_at_rung_one_the_moment_celery_gives_up(self):
        assert m._sweep_attempt_for(m.CELERY_ATTEMPT_BUDGET) == 1

    def test_each_sweep_claim_advances_one_rung(self):
        assert m._sweep_attempt_for(m.CELERY_ATTEMPT_BUDGET + 1) == 2
        assert m._sweep_attempt_for(m.CELERY_ATTEMPT_BUDGET + 2) == 3

    def test_budget_is_exhausted_exactly_at_the_end_of_the_ladder(self):
        last = m._sweep_attempt_for(m.MAX_TOTAL_ATTEMPTS - 1)
        assert policy.next_attempt_delay(last) is not None
        spent = m._sweep_attempt_for(m.MAX_TOTAL_ATTEMPTS)
        assert policy.next_attempt_delay(spent) is None

    def test_a_sweep_dispatched_retry_never_restarts_the_ladder(self):
        """The defect this arithmetic exists to prevent.

        A sweep-dispatched task starts with ``request.retries == 0``. If the
        rung were computed from that, every handoff would reset to a
        five-minute delay and the event would retry on a loop until it
        expired, instead of backing off toward a dead letter.
        """
        assert m._sweep_attempt_for(1) == 1
        assert m._sweep_attempt_for(m.CELERY_ATTEMPT_BUDGET + 3) == 4


# ---------------------------------------------------------------------------
# Replay fidelity
# ---------------------------------------------------------------------------


class TestCapiEntryFromStoredRow:
    """A replay must send what was recorded, not re-derive it."""

    def test_event_id_comes_from_the_row(self):
        row = _row(event_id="order-abc")
        assert m._capi_entry(row)["event_id"] == "order-abc"

    def test_event_time_is_the_conversion_not_now(self):
        """Re-stamping the time would move the sale and break attribution."""
        row = _row()
        assert m._capi_entry(row)["event_time"] == 1_755_000_000

    def test_stored_user_data_is_passed_through_unhashed_again(self):
        """It was hashed when the row was written.

        Hashing a hash produces a well-formed match key belonging to nobody,
        which Meta accepts and never matches — a silent EMQ collapse.
        """
        row = _row()
        entry = m._capi_entry(row)
        assert entry["user_data"] == row.request_payload["user_data"]

    def test_value_and_currency_survive_the_round_trip(self):
        entry = m._capi_entry(_row())
        assert entry["custom_data"] == {"value": 250.0, "currency": "EGP"}

    def test_event_source_url_only_when_present(self):
        assert "event_source_url" in m._capi_entry(_row())
        bare = _row(
            request_payload={
                "event_time": 1,
                "user_data": {},
                "custom_data": {},
            }
        )
        assert "event_source_url" not in m._capi_entry(bare)

    def test_opt_out_survives(self):
        payload = dict(_row().request_payload, opt_out=True)
        assert m._capi_entry(_row(request_payload=payload))["opt_out"] is True

    def test_falls_back_to_the_row_timestamp_when_payload_lacks_one(self):
        when = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
        row = _row(
            event_time=when,
            request_payload={"user_data": {}, "custom_data": {}},
        )
        assert m._capi_entry(row)["event_time"] == int(when.timestamp())


# ---------------------------------------------------------------------------
# Enqueue routing
# ---------------------------------------------------------------------------


class TestEnqueueRouting:
    @pytest.mark.asyncio
    async def test_browse_events_are_not_persisted_before_enqueue(self):
        """A PageView costs a synchronous INSERT per pixel on the hottest
        path in the platform, and the browser pixel already carries it."""
        with patch.object(m.meta_capi_send_event, "apply_async") as send:
            await m.enqueue_capi_event(
                session=None,
                store=None,
                tenant_id=None,
                store_id=str(uuid4()),
                pixel_id="1",
                event_name="PageView",
                event_id="e1",
                event_time=1_755_000_000,
                event_source_url=None,
                user_data={},
            )
        assert send.call_args.kwargs["queue"] == policy.QUEUE_STANDARD
        assert "log_id" not in send.call_args.kwargs["kwargs"]

    @pytest.mark.asyncio
    async def test_conversions_are_persisted_first_and_carry_their_row(self):
        row_id = uuid4()
        with (
            patch.object(m, "_persist_outbox_row", AsyncMock(return_value=row_id)),
            patch.object(m.meta_capi_send_event, "apply_async") as send,
        ):
            await m.enqueue_capi_event(
                session=MagicMock(),
                store=SimpleNamespace(tenant_id=uuid4(), country="EG"),
                tenant_id=uuid4(),
                store_id=str(uuid4()),
                pixel_id="1",
                event_name="Purchase",
                event_id="order-1",
                event_time=1_755_000_000,
                event_source_url=None,
                user_data={},
            )
        assert send.call_args.kwargs["queue"] == policy.QUEUE_CONVERSION
        assert send.call_args.kwargs["kwargs"]["log_id"] == str(row_id)

    @pytest.mark.asyncio
    async def test_an_already_delivered_conversion_is_not_re_enqueued(self):
        """A webhook redelivery must not become a second conversion."""
        with (
            patch.object(
                m,
                "_persist_outbox_row",
                AsyncMock(return_value=m._ALREADY_DELIVERED),
            ),
            patch.object(m.meta_capi_send_event, "apply_async") as send,
        ):
            await m.enqueue_capi_event(
                session=MagicMock(),
                store=SimpleNamespace(tenant_id=uuid4()),
                tenant_id=uuid4(),
                store_id=str(uuid4()),
                pixel_id="1",
                event_name="Purchase",
                event_id="order-1",
                event_time=1_755_000_000,
                event_source_url=None,
                user_data={},
            )
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_conversion_without_a_session_still_goes_out(self):
        """The orphan sweep and the test-event endpoint deliberately pass
        no session; losing durability must not mean losing the event."""
        with patch.object(m.meta_capi_send_event, "apply_async") as send:
            await m.enqueue_capi_event(
                session=None,
                store=None,
                tenant_id=None,
                store_id=str(uuid4()),
                pixel_id="1",
                event_name="Purchase",
                event_id="order-1",
                event_time=1_755_000_000,
                event_source_url=None,
                user_data={},
            )
        assert send.call_count == 1
        assert send.call_args.kwargs["queue"] == policy.QUEUE_CONVERSION


# ---------------------------------------------------------------------------
# The delivery sweep
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, claimed):
        self._claimed = claimed
        self.expired = 0
        self.claim_kwargs = None

    async def expire_overdue(self, *, now, limit=5_000):
        self.expired = 3
        return 3

    async def claim_due(self, *, now, lease_until, limit=200):
        self.claim_kwargs = {"now": now, "lease_until": lease_until, "limit": limit}
        return self._claimed


class _FakeSession:
    def __init__(self, repo):
        self._repo = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


def _sweep_env(claimed):
    repo = _FakeRepo(claimed)
    return repo, [
        patch.object(m, "_run_async", lambda coro: coro),
        patch(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            lambda: _FakeSession(repo),
        ),
        patch(
            "src.infrastructure.repositories.meta_event_log_repository."
            "MetaEventLogRepository",
            lambda session: repo,
        ),
        patch("src.infrastructure.tenancy.rls.enable_rls_bypass", AsyncMock()),
    ]


async def _run_sweep(claimed, send_mock):
    repo, patches = _sweep_env(claimed)
    for p in patches:
        p.start()
    try:
        with patch.object(m.meta_capi_send_batch, "apply_async", send_mock):
            stats = await m._deliver_due()
    finally:
        for p in patches:
            p.stop()
    return repo, stats


class TestDeliverDue:
    @pytest.mark.asyncio
    async def test_expiry_runs_before_anything_is_claimed(self):
        """Ordering is the correctness property, not an optimisation.

        A row past its window must never become claimable — sending it lands
        outside Meta's dedup window as a SECOND conversion, which inflates
        the merchant's reported revenue in the direction that looks like
        success.
        """
        send = MagicMock()
        repo, stats = await _run_sweep([], send)
        assert repo.expired == 3
        assert stats["expired"] == 3
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_claim_takes_a_lease_into_the_future(self):
        send = MagicMock()
        repo, _ = await _run_sweep([], send)
        lease = repo.claim_kwargs["lease_until"] - repo.claim_kwargs["now"]
        assert lease == policy.CLAIM_LEASE

    @pytest.mark.asyncio
    async def test_rows_for_one_pixel_are_sent_as_one_request(self):
        store, pixel = str(uuid4()), "999"
        rows = [
            _row(store_id=UUID(store), pixel_id=pixel, event_id=f"e{i}")
            for i in range(5)
        ]
        send = MagicMock()
        _, stats = await _run_sweep(rows, send)
        assert stats["batches"] == 1
        assert len(send.call_args.kwargs["kwargs"]["row_ids"]) == 5

    @pytest.mark.asyncio
    async def test_different_pixels_never_share_a_request(self):
        """Each CAPI request targets one pixel's endpoint and is signed by
        one token — mixing them is not a batch, it is a wrong send."""
        store = uuid4()
        rows = [
            _row(store_id=store, pixel_id="a"),
            _row(store_id=store, pixel_id="b"),
        ]
        send = MagicMock()
        _, stats = await _run_sweep(rows, send)
        assert stats["batches"] == 2

    @pytest.mark.asyncio
    async def test_different_stores_never_share_a_request(self):
        rows = [_row(pixel_id="same"), _row(pixel_id="same")]
        send = MagicMock()
        _, stats = await _run_sweep(rows, send)
        assert stats["batches"] == 2

    @pytest.mark.asyncio
    async def test_test_event_codes_never_share_a_request(self):
        """``test_event_code`` is top-level in Meta's envelope, so two rows
        with different codes cannot be expressed in one request."""
        store, pixel = uuid4(), "1"
        rows = []
        for code in ("TEST1", "TEST2"):
            payload = dict(_row().request_payload, test_event_code=code)
            rows.append(_row(store_id=store, pixel_id=pixel, request_payload=payload))
        send = MagicMock()
        _, stats = await _run_sweep(rows, send)
        assert stats["batches"] == 2

    @pytest.mark.asyncio
    async def test_a_large_group_is_split_at_the_batch_ceiling(self):
        store, pixel = uuid4(), "1"
        rows = [_row(store_id=store, pixel_id=pixel) for _ in range(250)]
        send = MagicMock()
        _, stats = await _run_sweep(rows, send)
        assert stats["batches"] == 3
        sizes = [c.kwargs["kwargs"]["row_ids"] for c in send.call_args_list]
        assert [len(s) for s in sizes] == [m._SEND_BATCH, m._SEND_BATCH, 50]

    @pytest.mark.asyncio
    async def test_a_batch_holding_a_conversion_rides_the_priority_queue(self):
        store, pixel = uuid4(), "1"
        rows = [
            _row(store_id=store, pixel_id=pixel, priority=policy.PRIORITY_BULK),
            _row(store_id=store, pixel_id=pixel, priority=policy.PRIORITY_CONVERSION),
        ]
        send = MagicMock()
        await _run_sweep(rows, send)
        assert send.call_args.kwargs["queue"] == policy.QUEUE_CONVERSION

    @pytest.mark.asyncio
    async def test_a_batch_of_browse_events_stays_on_the_general_queue(self):
        store, pixel = uuid4(), "1"
        rows = [
            _row(store_id=store, pixel_id=pixel, priority=policy.PRIORITY_BULK)
            for _ in range(2)
        ]
        send = MagicMock()
        await _run_sweep(rows, send)
        assert send.call_args.kwargs["queue"] == policy.QUEUE_STANDARD


# ---------------------------------------------------------------------------
# Adopting a row this task was handed
# ---------------------------------------------------------------------------


class _AdoptSession:
    def __init__(self, row):
        self._row = row
        self.committed = False

    async def execute(self, *a, **k):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._row)
        return result

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None


class TestAdoptOwnedRow:
    @pytest.mark.asyncio
    async def test_a_missing_row_is_skipped_not_sent_blind(self):
        out = await m._adopt_owned_row(
            session=_AdoptSession(None),
            log_id=uuid4(),
            request_payload={},
            store_id=str(uuid4()),
            event_id="e",
            event_name="Purchase",
        )
        assert out.result == {"status": "skipped", "reason": "row_missing"}

    @pytest.mark.asyncio
    async def test_an_already_settled_row_is_not_sent_again(self):
        """Between claim and execution another worker may have settled it."""
        row = _row(status=policy.DeliveryStatus.SENT, attempt_count=2)
        out = await m._adopt_owned_row(
            session=_AdoptSession(row),
            log_id=row.id,
            request_payload={},
            store_id=str(uuid4()),
            event_id="e",
            event_name="Purchase",
        )
        assert out.result["status"] == "duplicate"

    @pytest.mark.asyncio
    async def test_a_row_that_expired_in_the_queue_is_retired_not_delivered(self):
        row = _row(
            event_time=datetime.now(UTC) - timedelta(hours=60),
            status=policy.DeliveryStatus.PENDING,
        )
        session = _AdoptSession(row)
        with patch.object(m, "_settle_row", AsyncMock()) as settle:
            out = await m._adopt_owned_row(
                session=session,
                log_id=row.id,
                request_payload={},
                store_id=str(uuid4()),
                event_id="e",
                event_name="Purchase",
            )
        assert out.result == {"status": "expired"}
        assert settle.call_args.kwargs["status"] == policy.DeliveryStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_a_live_row_is_re_leased_and_its_payload_refreshed(self):
        """The payload is rebuilt from the task kwargs on every attempt, so a
        resend carries the identity the platform knows NOW — that is what
        makes the enrichment path work."""
        row = _row(status=policy.DeliveryStatus.RETRYING, attempt_count=4)
        before = datetime.now(UTC)
        out = await m._adopt_owned_row(
            session=_AdoptSession(row),
            log_id=row.id,
            request_payload={"user_data": {"em": "c" * 64}},
            store_id=str(uuid4()),
            event_id="e",
            event_name="Purchase",
        )
        assert out.result is None
        assert out.attempt_count == 4
        assert row.status == policy.DeliveryStatus.PENDING
        assert row.next_retry_at >= before + policy.CLAIM_LEASE - timedelta(seconds=5)
        assert row.request_payload == {"user_data": {"em": "c" * 64}}

    @pytest.mark.asyncio
    async def test_a_row_predating_the_lifecycle_gets_a_deadline(self):
        row = _row(status=policy.DeliveryStatus.PENDING, expires_at=None)
        await m._adopt_owned_row(
            session=_AdoptSession(row),
            log_id=row.id,
            request_payload={},
            store_id=str(uuid4()),
            event_id="e",
            event_name="Purchase",
        )
        assert row.expires_at == policy.expires_at_for(row.event_time)


# ---------------------------------------------------------------------------
# Batch delivery outcomes
# ---------------------------------------------------------------------------


class _BatchSession:
    """Stands in for the two sessions ``_send_batch`` opens."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=self._rows)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        return result

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _batch_patches(rows, store, post_result):
    return [
        patch(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            lambda: _BatchSession(rows),
        ),
        patch(
            "src.infrastructure.repositories.store_repository.StoreRepository",
            lambda s: SimpleNamespace(get_by_id=AsyncMock(return_value=store)),
        ),
        patch("src.infrastructure.tenancy.rls.enable_rls_bypass", AsyncMock()),
        patch("src.infrastructure.tenancy.rls.narrow_to_tenant", AsyncMock()),
        patch.object(m, "_decrypt_capi_token", AsyncMock(return_value="tok")),
        patch.object(m, "_post_capi_batch", AsyncMock(return_value=post_result)),
    ]


async def _run_batch(rows, post_result, *, capi_enabled=True):
    store = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        settings={"tracking": {"meta": {"capi_enabled": capi_enabled}}},
    )
    settle, reschedule = AsyncMock(), AsyncMock()
    patches = _batch_patches(rows, store, post_result) + [
        patch.object(m, "_settle_many", settle),
        patch.object(m, "_reschedule_many", reschedule),
    ]
    for p in patches:
        p.start()
    try:
        with patch.object(m.meta_capi_send_batch, "apply_async") as resend:
            out = await m._send_batch(
                store_id=str(store.id),
                pixel_id="1",
                row_ids=[str(r.id) for r in rows],
                test_event_code=None,
            )
    finally:
        for p in patches:
            p.stop()
    return out, settle, reschedule, resend


_OK = (200, {"events_received": 2}, "trace-1", None, None)


class TestSendBatch:
    @pytest.mark.asyncio
    async def test_a_2xx_settles_every_row_as_sent(self):
        rows = [_row(), _row()]
        out, settle, _, _ = await _run_batch(rows, _OK)
        assert out == {"status": "sent", "sent": 2}
        assert settle.call_args.kwargs["status"] == policy.DeliveryStatus.SENT

    @pytest.mark.asyncio
    async def test_a_transport_failure_reschedules_rather_than_losing_them(self):
        rows = [_row()]
        out, _, reschedule, _ = await _run_batch(
            rows, (0, None, None, None, "ConnectTimeout: boom")
        )
        assert out["status"] == "failed"
        assert reschedule.call_args.kwargs["kind"] == policy.FailureKind.TRANSPORT

    @pytest.mark.asyncio
    async def test_a_rate_limit_reschedules_and_carries_retry_after(self):
        """Meta sends this as a 400. Treating it as permanent — the old
        behaviour — discarded the whole batch."""
        rows = [_row(), _row()]
        out, _, reschedule, _ = await _run_batch(
            rows, (400, {"error": {"code": 4}}, "t", "120", None)
        )
        assert out["status"] == "retry_scheduled"
        assert reschedule.call_args.kwargs["kind"] == policy.FailureKind.RATE_LIMITED
        assert reschedule.call_args.kwargs["retry_after"] == "120"

    @pytest.mark.asyncio
    async def test_a_permanent_rejection_splits_the_batch_instead_of_failing_it(self):
        """Meta rejects a batch as a unit, so one malformed event fails its
        neighbours. Failing all of them would discard good conversions
        because of a bad one — so they are re-sent individually and the
        poison is isolated to its own row."""
        rows = [_row(), _row(), _row()]
        out, settle, reschedule, resend = await _run_batch(
            rows, (400, {"error": {"code": 100}}, "t", None, None)
        )
        assert out["status"] == "split"
        settle.assert_not_called()
        assert resend.call_count == 3
        assert all(
            len(c.kwargs["kwargs"]["row_ids"]) == 1 for c in resend.call_args_list
        )
        assert reschedule.call_args.kwargs["due_now"] is True

    @pytest.mark.asyncio
    async def test_a_lone_row_that_is_permanently_rejected_is_failed_not_split(self):
        """Splitting one row would loop forever."""
        rows = [_row()]
        out, settle, _, resend = await _run_batch(
            rows, (400, {"error": {"code": 100}}, "t", None, None)
        )
        assert out["status"] == "failed"
        resend.assert_not_called()
        assert settle.call_args.kwargs["status"] == policy.DeliveryStatus.FAILED
        assert settle.call_args.kwargs["failure_kind"] == (
            policy.FailureKind.INVALID_PAYLOAD
        )

    @pytest.mark.asyncio
    async def test_capi_switched_off_mid_flight_skips_rather_than_sends(self):
        rows = [_row()]
        out, settle, _, _ = await _run_batch(rows, _OK, capi_enabled=False)
        assert out["reason"] == "capi_disabled"
        assert settle.call_args.kwargs["status"] == policy.DeliveryStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_rows_that_expired_in_the_queue_are_dropped_from_the_request(self):
        """Between claim and execution a row can cross its window. Sending it
        then would double-count the conversion."""
        stale = _row(event_time=datetime.now(UTC) - timedelta(hours=60))
        out, settle, _, _ = await _run_batch([stale], _OK)
        assert out["reason"] == "nothing_live"
        assert settle.call_args.kwargs["status"] == policy.DeliveryStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_rows_already_settled_are_silently_ignored(self):
        rows = [_row(status=policy.DeliveryStatus.SENT), _row()]
        out, _, _, _ = await _run_batch(rows, _OK)
        assert out == {"status": "sent", "sent": 1}

    @pytest.mark.asyncio
    async def test_a_missing_token_fails_the_rows_visibly(self):
        rows = [_row()]
        store = SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            settings={"tracking": {"meta": {"capi_enabled": True}}},
        )
        settle = AsyncMock()
        patches = _batch_patches(rows, store, _OK) + [
            patch.object(m, "_decrypt_capi_token", AsyncMock(return_value=None)),
            patch.object(m, "_settle_many", settle),
        ]
        for p in patches:
            p.start()
        try:
            out = await m._send_batch(
                store_id=str(store.id),
                pixel_id="1",
                row_ids=[str(rows[0].id)],
                test_event_code=None,
            )
        finally:
            for p in patches:
                p.stop()
        assert out["reason"] == "credential_missing"
        assert settle.call_args.kwargs["failure_kind"] == (
            policy.FailureKind.INVALID_CREDENTIALS
        )


# ---------------------------------------------------------------------------
# Observability must not eat the exception it is reporting
# ---------------------------------------------------------------------------


class TestNetworkErrorReachesCelery:
    """A transport failure has to leave the task as a transport failure.

    Regression for a live defect: the handler called
    ``sentry_sdk.capture_message(..., fingerprints=[...])`` — the kwarg is
    ``fingerprint``, singular — so reporting the error raised ``TypeError``
    *inside* the ``except`` block. The ``raise`` beneath it never ran, the
    TypeError escaped instead, and ``autoretry_for=(NetworkError,
    TimeoutException)`` did not match it. Every network failure to Meta was
    therefore a permanent, unretried loss, and the same bug sat in the TikTok
    task. It reported nothing to Sentry either.
    """

    def test_a_transport_error_propagates_unchanged(self):
        import httpx

        boom = httpx.ConnectTimeout("connect timed out")

        def explode(_coro):
            raise boom

        with patch.object(m, "_run_async", explode):
            with pytest.raises(httpx.TimeoutException) as caught:
                m.meta_capi_send_event(
                    store_id=str(uuid4()),
                    pixel_id="1",
                    event_name="Purchase",
                    event_id="e1",
                    event_time=1_755_000_000,
                    event_source_url=None,
                    user_data={},
                )
        assert caught.value is boom

    def test_the_sentry_call_in_that_path_is_actually_callable(self):
        """Belt and braces: the kwarg name is the whole bug."""
        import inspect

        import sentry_sdk.scope as scope

        params = inspect.signature(scope.Scope.update_from_kwargs).parameters
        assert "fingerprint" in params
        assert "fingerprints" not in params


class TestLadderActuallyAdvances:
    """End-to-end arithmetic of the backoff ladder across the handoff.

    Regression for a bug in this change: ``update_response`` did not persist
    ``attempt_count``. Celery's retries live in the broker and never touch the
    row, so the column still read 1 when the event reached the sweep. The
    sweep indexes the ladder off that column, so every pass recomputed rung 1
    — a five-minute retry loop running until the event expired, roughly 570
    attempts instead of 5, and a silent violation of the bounded-retry rule.
    """

    def test_rungs_advance_once_per_sweep_claim(self):
        # Celery exhausts its budget: 1 initial + 6 retries.
        attempts = m.CELERY_ATTEMPT_BUDGET
        seen = []
        for _ in range(policy.MAX_SWEEP_ATTEMPTS):
            rung = m._sweep_attempt_for(attempts)
            delay = policy.next_attempt_delay(rung)
            assert delay is not None
            seen.append(delay)
            attempts += 1  # each claim increments the row
        assert seen == sorted(seen)
        assert len(set(seen)) == policy.MAX_SWEEP_ATTEMPTS
        # And the very next claim is out of budget, not another rung 1.
        assert policy.next_attempt_delay(m._sweep_attempt_for(attempts)) is None

    def test_total_attempts_match_the_documented_ceiling(self):
        assert m.MAX_TOTAL_ATTEMPTS == 12
        assert m.CELERY_ATTEMPT_BUDGET == m._CELERY_MAX_RETRIES + 1

    def test_the_whole_ladder_finishes_inside_the_dedup_window(self):
        """If it did not, the last retry would double-count the conversion."""
        attempts = m.CELERY_ATTEMPT_BUDGET
        total = 0
        while True:
            delay = policy.next_attempt_delay(m._sweep_attempt_for(attempts))
            if delay is None:
                break
            total += delay
            attempts += 1
        assert total < policy.DEDUP_WINDOW.total_seconds()
