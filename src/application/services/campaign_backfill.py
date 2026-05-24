"""Manual attribution backfill — feature 002 US5.

Pure helper that builds a SQLAlchemy WHERE clause from a merchant-
provided filter spec. The actual chunked UPDATE runs in the Celery
task `numu_api.marketing.backfill_campaign_attribution`.

SEC-003: all merchant-supplied `value` operands MUST be passed through
SQLAlchemy column operators (which parameterize via bindparam) — never
string-interpolated into raw SQL. The build_update_filter helper
enforces this by construction; the Celery task uses the returned
``ColumnElement`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, and_, or_


@dataclass
class BackfillCondition:
    """One filter row for the backfill UPDATE.

    ``field`` enum is a fixed allowlist — anything else is rejected at
    the route layer before we get here. ``value`` is opaque to this
    module; the column operator handles parameterization.
    """

    field: Literal[
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "referrer",
    ]
    operator: Literal["equals", "starts_with", "contains"]
    value: str


def _column_for_field(model, field: str) -> ColumnElement:
    """Resolve a model attribute by field name.

    The allowlist on ``BackfillCondition.field`` guarantees the
    attribute exists on the model; getattr is safe.
    """
    return getattr(model, field)


def _condition_clause(model, cond: BackfillCondition) -> ColumnElement:
    """Build one WHERE predicate. Uses SQLAlchemy operators (parameterized)
    so the merchant's `value` string is bound, never interpolated.

    `starts_with` / `contains` use SQL LIKE — we escape any wildcard
    characters in the user-supplied value so a leading `%` doesn't
    silently turn a literal-match into a wildcard.
    """
    col = _column_for_field(model, cond.field)
    if cond.operator == "equals":
        return col == cond.value
    # Escape LIKE wildcards in the user-supplied value so `%`, `_`, and
    # `\` are treated literally. The escape character must be passed
    # to the column.like(..., escape=...) call.
    safe = cond.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if cond.operator == "starts_with":
        return col.like(f"{safe}%", escape="\\")
    # contains
    return col.like(f"%{safe}%", escape="\\")


def build_update_filter(
    model,
    store_id,
    conditions: list[BackfillCondition],
    starts_at: datetime,
    ends_at: datetime,
) -> ColumnElement:
    """Compose the WHERE clause for the backfill UPDATE.

    Always includes:
    - store_id match
    - created_at window (starts_at, ends_at)
    - ``campaign_id IS NULL`` (FR-025 — never overwrite a row already
      attributed to a different campaign; this clause guarantees both
      idempotency and the SEC-required non-clobber semantic)

    Conditions are combined with AND. Empty conditions → no extra
    predicate (defaults to "all rows in window with NULL campaign_id"
    which is rare but valid).
    """
    base = and_(
        model.store_id == store_id,
        model.created_at >= starts_at,
        model.created_at <= ends_at,
        model.campaign_id.is_(None),
    )
    if not conditions:
        return base
    user_clauses = [_condition_clause(model, c) for c in conditions]
    if len(user_clauses) == 1:
        return and_(base, user_clauses[0])
    return and_(base, *user_clauses)


# Re-export `or_` for any future caller that wants OR-combined filters
# (current spec is AND-only; this stays here for forward-compat).
__all__ = [
    "BackfillCondition",
    "build_update_filter",
    "or_",
]
