"""The staff-access beat tasks must be plain functions.

They were ``async def`` Celery tasks, so the worker got a coroutine back and
failed to JSON-encode it every 15 minutes without running a line: expired
temporary grants were never revoked and kept their permissions.
"""

from __future__ import annotations

import inspect
import json

import pytest

from src.infrastructure.messaging.tasks import (
    detect_suspicious_activity,
    expire_temporary_grants,
)

TASKS = [
    (expire_temporary_grants, "expire_temporary_grants"),
    (expire_temporary_grants, "expire_access_requests"),
    (expire_temporary_grants, "cleanup_staff_sessions"),
    (detect_suspicious_activity, "detect_suspicious_activity"),
    (detect_suspicious_activity, "compute_staff_risk_scores"),
]


@pytest.mark.parametrize(("module", "name"), TASKS)
def test_task_runs_its_body_and_returns_json(monkeypatch, module, name):
    task = getattr(module, name)
    assert not inspect.iscoroutinefunction(task.run)

    calls = []

    async def body(*args):
        calls.append(args)
        return {"ran": name}

    monkeypatch.setattr(module, f"_{name}", body)

    result = task()

    assert calls, "the async body must actually run"
    assert json.loads(json.dumps(result)) == {"ran": name}
