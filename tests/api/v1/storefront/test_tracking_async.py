"""Tests for the Step 09 async-tracking handler logic.

Drives ``_emit_funnel_event`` directly with stubs in place of the
Celery task / Redis dedupe / repo. The full FastAPI app is not
booted — the helper is the entire decision tree we care about:

* When ``settings.analytics_async_enabled`` is True (default):
  * fresh ``event_id`` → claim succeeds → Celery push
  * duplicate ``event_id`` → claim fails → no Celery push
  * no ``event_id`` → server generates one, claim succeeds, push fires
* When ``settings.analytics_async_enabled`` is False (kill switch):
  * skips Celery and Redis entirely; falls back to
    ``funnel_repo.create(...)`` (legacy sync path)

Sync ``def test_*`` + private asyncio loop pattern (Step 04 lesson).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

from src.api.v1.routes.storefront.tracking import _emit_funnel_event


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class StubIdempotency:
    """Records claim calls. Returns True on first call per key, False after."""

    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.calls: list[str] = []

    async def claim(self, key: str) -> bool:
        self.calls.append(key)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True


class StubIdempotencyAlwaysDuplicate:
    """Always reports a key has been claimed already."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def claim(self, key: str) -> bool:
        self.calls.append(key)
        return False


class StubFunnelRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)


# ---------------------------------------------------------------- #
# Async path                                                        #
# ---------------------------------------------------------------- #


def _common_args(idempotency: Any, repo: Any) -> dict[str, Any]:
    return {
        "funnel_repo": repo,
        "idempotency": idempotency,
        "tenant_id": uuid4(),
        "store_id": uuid4(),
        "step": "page_view",
        "session_fingerprint": "fp-1",
        "customer_id": None,
        "step_data": {"path": "/"},
    }


def test_async_path_enqueues_celery_task_on_fresh_event_id() -> None:
    idem = StubIdempotency()
    repo = StubFunnelRepo()
    event_id = uuid4()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
    ) as mock_task:
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=event_id))

    assert mock_task.apply_async.call_count == 1, (
        "expected exactly one Celery push on fresh event_id"
    )
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["queue"] == "analytics"
    pushed = kwargs["kwargs"]["event"]
    assert pushed["event_id"] == str(event_id)
    assert pushed["step"] == "page_view"
    assert repo.created == [], "sync repo must NOT be called on the async path"


def test_async_path_skips_celery_on_duplicate_event_id() -> None:
    idem = StubIdempotencyAlwaysDuplicate()
    repo = StubFunnelRepo()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
    ) as mock_task:
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=uuid4()))

    assert mock_task.apply_async.call_count == 0
    assert repo.created == []
    # claim was still consulted — that's the gate
    assert len(idem.calls) == 1


def test_async_path_generates_event_id_when_caller_did_not_supply_one() -> None:
    idem = StubIdempotency()
    repo = StubFunnelRepo()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
    ) as mock_task:
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=None))

    assert mock_task.apply_async.call_count == 1
    pushed_event_id = mock_task.apply_async.call_args.kwargs["kwargs"]["event"][
        "event_id"
    ]
    # Round-trip parseable as UUID
    UUID(pushed_event_id)


def test_two_distinct_event_ids_both_get_enqueued() -> None:
    idem = StubIdempotency()
    repo = StubFunnelRepo()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
    ) as mock_task:
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=uuid4()))
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=uuid4()))

    assert mock_task.apply_async.call_count == 2


def test_async_path_does_not_call_sync_repo() -> None:
    """Regression guard for the kill-switch contract: when async is
    enabled, the legacy sync writer must not run."""
    idem = StubIdempotency()
    repo = StubFunnelRepo()

    with patch(
        "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
    ):
        _run(_emit_funnel_event(**_common_args(idem, repo), event_id=uuid4()))

    assert repo.created == []


# ---------------------------------------------------------------- #
# Kill switch — sync fallback                                       #
# ---------------------------------------------------------------- #


def test_kill_switch_off_falls_back_to_sync_funnel_repo_create() -> None:
    idem = StubIdempotency()
    repo = StubFunnelRepo()
    event_id = uuid4()

    with patch(
        "src.api.v1.routes.storefront.tracking.settings.analytics_async_enabled", False
    ):
        with patch(
            "src.infrastructure.messaging.tasks.analytics_ingest_task.ingest_funnel_event"
        ) as mock_task:
            _run(_emit_funnel_event(**_common_args(idem, repo), event_id=event_id))

    assert mock_task.apply_async.call_count == 0, (
        "kill switch off must NOT push to Celery"
    )
    assert len(repo.created) == 1, "kill switch off must call sync funnel_repo.create"
    created = repo.created[0]
    assert created["event_id"] == event_id
    assert created["step"] == "page_view"
    # idempotency claim should NOT be consulted when async is disabled —
    # the dedupe story is irrelevant for synchronous writes.
    assert idem.calls == []
