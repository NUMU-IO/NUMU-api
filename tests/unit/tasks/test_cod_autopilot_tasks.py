"""Unit tests for the COD Autopilot Celery wiring (004-cod-autopilot).

Guards the two failure modes that killed beat tasks before (see
test_celery_beat_registration.py): a task name mismatch and a module
missing from ``conf.imports``. Also pins the schedule invariants the
feature's correctness depends on — most importantly that the
assumed-delivered sweep runs AFTER the auto-RTO sweep (FR-018).
"""

from __future__ import annotations

from src.infrastructure.messaging.celery_app import celery_app

celery_app.loader.import_default_modules()

_TASK_NAMES = (
    "tasks.cod_autopilot_send_digests",
    "tasks.cod_autopilot_delivery_checks",
    "tasks.cod_autopilot_assumed_delivered",
)

_BEAT_KEYS = (
    "cod-autopilot-send-digests",
    "cod-autopilot-delivery-checks",
    "cod-autopilot-assumed-delivered",
)


def test_all_autopilot_tasks_registered() -> None:
    for name in _TASK_NAMES:
        assert name in celery_app.tasks, f"{name} not registered"


def test_module_in_imports() -> None:
    assert (
        "src.infrastructure.messaging.tasks.cod_autopilot_tasks"
        in celery_app.conf.imports
    )


def test_beat_entries_reference_registered_tasks() -> None:
    schedule = celery_app.conf.beat_schedule
    for key, task_name in zip(_BEAT_KEYS, _TASK_NAMES, strict=True):
        assert key in schedule, f"beat entry {key} missing"
        assert schedule[key]["task"] == task_name


def test_assumed_delivered_runs_after_rto_sweep() -> None:
    """FR-018: RTO precedence is enforced by schedule ordering — the
    fallback closure must fire after the 03:00 auto-RTO sweep."""
    schedule = celery_app.conf.beat_schedule
    rto = schedule["auto-rto-stale-shipped-orders"]["schedule"]
    fallback = schedule["cod-autopilot-assumed-delivered"]["schedule"]
    # Both are crontab instances; compare their hour/minute sets.
    assert rto.hour == {3} and rto.minute == {0}
    assert fallback.hour == {3} and fallback.minute == {30}


def test_hourly_sweeps_are_hourly() -> None:
    """Digest + delivery-check sweeps must fire every hour so per-store
    local digest hours (R-02) and retry cadences are honoured."""
    schedule = celery_app.conf.beat_schedule
    for key in ("cod-autopilot-send-digests", "cod-autopilot-delivery-checks"):
        entry = schedule[key]["schedule"]
        assert len(entry.hour) == 24, f"{key} must run every hour"
