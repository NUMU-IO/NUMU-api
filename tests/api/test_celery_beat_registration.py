"""Regression: every Celery beat-schedule entry must reference a registered task.

A ``@celery_app.task(name=...)`` whose name doesn't match the ``"task"`` string
in ``beat_schedule`` means the scheduled job fails on every tick with "Received
unregistered task" and silently never runs. That is exactly how the five
staff-permission sweeps were dead in production: each was registered without the
``tasks.`` prefix that its beat entry (and the staff event handler) reference.
"""

from __future__ import annotations

import pytest

from src.infrastructure.messaging.celery_app import celery_app

# A bare ``import celery_app`` does NOT import the task modules listed in
# ``conf.include`` — the worker imports those at startup via
# ``import_default_modules()``. Do the same here so the task registry is
# complete before we assert against it (otherwise every ``include``-only task,
# including the staff sweeps, looks "unregistered" for the wrong reason).
celery_app.loader.import_default_modules()

# The staff-permission sweeps that were registered without the ``tasks.`` prefix
# their callers use — all five were unregistered/dead until this fix.
STAFF_PERMISSION_TASKS = (
    "tasks.expire_temporary_grants",
    "tasks.expire_access_requests",
    "tasks.cleanup_staff_sessions",
    "tasks.detect_suspicious_activity",
    "tasks.compute_staff_risk_scores",
)

# Pre-existing beat entries whose task name is not registered — the SAME bug
# class as the staff sweeps above, but in separate task families that are out of
# scope for this fix. Tracked here so the invariant below still guards against
# NEW breakage while tolerating this known (reported) debt. Shrink this set as
# each is fixed.
KNOWN_UNREGISTERED_BEAT_TASKS = frozenset({
    "tasks.generate_ai_insights",
    "tasks.send_inactive_merchant_nudges",
    "tasks.send_trial_expiry_warnings",
})


def _unregistered_beat_tasks() -> set[str]:
    registered = set(celery_app.tasks)
    scheduled = {
        entry["task"]
        for entry in celery_app.conf.beat_schedule.values()
        if "task" in entry
    }
    return scheduled - registered


def test_no_new_unregistered_beat_tasks() -> None:
    """Every beat task must be registered, except the documented known-broken
    set. A NEW mismatch (e.g. a task renamed without updating its beat entry)
    fails here instead of silently never running in production."""
    new_breakage = sorted(_unregistered_beat_tasks() - KNOWN_UNREGISTERED_BEAT_TASKS)
    assert not new_breakage, (
        "beat_schedule references newly-unregistered tasks "
        f"('Received unregistered task' every tick): {new_breakage}"
    )


def test_fixed_staff_sweeps_are_no_longer_broken() -> None:
    """The five staff sweeps must not regress back into the unregistered set."""
    assert not (set(STAFF_PERMISSION_TASKS) & _unregistered_beat_tasks())


@pytest.mark.parametrize("task_name", STAFF_PERMISSION_TASKS)
def test_staff_permission_task_registered(task_name: str) -> None:
    assert task_name in celery_app.tasks
