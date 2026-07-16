"""Regression: every Celery beat-schedule entry must reference a registered task.

A beat entry whose ``"task"`` name isn't registered fails on every tick with
"Received unregistered task" and silently never runs. Two ways that happened in
this codebase, both fixed:

- Name mismatch: the five staff-permission sweeps were ``@celery_app.task(
  name="X")`` while beat + the event handler send ``"tasks.X"``.
- Missing import: ``ai_insights_tasks`` and ``onboarding_nudge_tasks`` weren't in
  ``celery_app.conf.imports``, so the worker never loaded them to register.

This suite loads ``conf.include``/``imports`` the way the worker does and asserts
the whole schedule is wired, so either failure mode is caught before prod.
"""

from __future__ import annotations

import pytest

from src.infrastructure.messaging.celery_app import celery_app

# A bare ``import celery_app`` does NOT import the task modules listed in
# ``conf.imports`` — the worker imports those at startup via
# ``import_default_modules()``. Do the same here so the task registry is
# complete before we assert against it.
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


def _unregistered_beat_tasks() -> set[str]:
    registered = set(celery_app.tasks)
    scheduled = {
        entry["task"]
        for entry in celery_app.conf.beat_schedule.values()
        if "task" in entry
    }
    return scheduled - registered


def test_every_beat_task_is_registered() -> None:
    """No beat entry may reference an unregistered task."""
    unregistered = sorted(_unregistered_beat_tasks())
    assert not unregistered, (
        "beat_schedule references tasks with no matching registration "
        f"('Received unregistered task' every tick): {unregistered}"
    )


@pytest.mark.parametrize("task_name", STAFF_PERMISSION_TASKS)
def test_staff_permission_task_registered(task_name: str) -> None:
    assert task_name in celery_app.tasks
