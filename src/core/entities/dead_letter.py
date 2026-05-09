"""Celery dead-letter entity (Phase 5.3).

Tasks that exhaust their retry budget currently disappear with only a
warning log line. The DLQ table persists exhausted invocations so:

  - Operators can see "what's been failing this week" in the hub
  - Failed tasks can be manually retried from the UI without writing
    a custom replay script
  - Audit trail for SEV1 post-mortems

Schema decisions:
  - `task_name` + `args` + `kwargs` reproduce the call site for
    manual retry. We don't store the full Celery message envelope
    (correlation ids, etag) because those are recreated on retry
    anyway.
  - `last_error` truncated to ~4KB to bound the row size; full
    tracebacks land in the structured logs and are correlated by
    request_id.
  - Status moves only forward: pending → retried → resolved (or
    abandoned). Manual retry creates a new task invocation but
    doesn't delete the DLQ row — operators want to see "I retried
    this and it succeeded" for compliance.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class DeadLetterStatus(StrEnum):
    PENDING = "pending"  # Awaiting operator action
    RETRIED = "retried"  # Manual retry queued
    RESOLVED = "resolved"  # Manual retry succeeded
    ABANDONED = "abandoned"  # Operator marked as unsalvageable


class DeadLetterEntry(BaseEntity):
    """One exhausted-after-retries Celery task invocation."""

    # Tenant scoping (nullable when the failed task wasn't tenant-bound,
    # e.g. platform-level cleanup tasks).
    tenant_id: UUID | None = None
    store_id: UUID | None = None

    task_name: str = Field(..., max_length=200)
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    queue: str | None = None

    status: DeadLetterStatus = DeadLetterStatus.PENDING

    last_error: str | None = Field(None, max_length=4000)
    attempts: int = 0
    first_failed_at: datetime
    last_failed_at: datetime

    # Filled when an operator clicks Retry; the new task id ties the
    # DLQ row to its replay so the UI can show "you retried this 2
    # minutes ago, status: succeeded".
    retried_at: datetime | None = None
    retried_by_user_id: UUID | None = None
    retry_task_id: str | None = None

    # Free-form notes operators leave on the entry.
    operator_note: str | None = Field(None, max_length=2000)
