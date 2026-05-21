"""Automation rule execution engine.

Evaluates automation rules against order context and resolves conflicts
using the "most restrictive action wins" strategy:

    CANCEL > HOLD > WHATSAPP_CONFIRM > AUTO_APPROVE

Non-conflicting actions (add_tag, add_note, send_notification) coexist
with the winning exclusive action.

Safety constraints:
- ``cancel_order`` ONLY executes when ``score_type == "final"``.
- Auto-cancel is blocked during the first 30 days after installation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ── Action priority (higher = more restrictive) ─────────────────────────────

_EXCLUSIVE_ACTIONS = {
    "cancel_order": 40,
    "hold_order": 30,
    "whatsapp_confirm": 20,
    "auto_approve": 10,
}

_NON_CONFLICTING_ACTIONS = {"add_tag", "add_note", "send_notification"}


@dataclass
class OrderContext:
    """Data available for condition matching."""

    risk_score: int = 0
    risk_level: str = "low"
    score_type: str = "preliminary"  # preliminary | final
    payment_method: str = "unknown"
    total_cents: int = 0
    customer_total_orders: int = 0
    customer_cancellation_rate: float | None = None
    installed_at: datetime | None = None  # for 30-day safety gate


@dataclass
class ResolvedAction:
    """An action to execute after conflict resolution."""

    action_type: str
    params: dict = field(default_factory=dict)
    source_rule_id: str = ""
    source_rule_name: str = ""


# ── Condition matching ───────────────────────────────────────────────────────


def _matches_conditions(conditions: dict, ctx: OrderContext) -> bool:
    """Check whether a rule's conditions match the given order context.

    Supported condition keys:
    - ``risk_score_gte``: int — risk score must be >= value
    - ``risk_score_lte``: int — risk score must be <= value
    - ``risk_level``: str or list[str] — risk level must match
    - ``payment_method``: str — payment method must match
    - ``amount_gte_cents``: int — order total must be >= value
    - ``min_previous_orders``: int — customer order count must be >= value
    - ``previous_cancel_rate_lt``: float — cancellation rate must be < value
    """
    if not conditions:
        return True

    if "risk_score_gte" in conditions:
        if ctx.risk_score < conditions["risk_score_gte"]:
            return False

    if "risk_score_lte" in conditions:
        if ctx.risk_score > conditions["risk_score_lte"]:
            return False

    if "risk_level" in conditions:
        expected = conditions["risk_level"]
        if isinstance(expected, list):
            if ctx.risk_level not in expected:
                return False
        elif ctx.risk_level != expected:
            return False

    if "payment_method" in conditions:
        if ctx.payment_method != conditions["payment_method"]:
            return False

    if "amount_gte_cents" in conditions:
        if ctx.total_cents < conditions["amount_gte_cents"]:
            return False

    if "min_previous_orders" in conditions:
        if ctx.customer_total_orders < conditions["min_previous_orders"]:
            return False

    if "previous_cancel_rate_lt" in conditions:
        rate = ctx.customer_cancellation_rate
        if rate is not None and rate >= conditions["previous_cancel_rate_lt"]:
            return False

    return True


# ── Conflict resolution ──────────────────────────────────────────────────────


def _resolve_conflicts(
    matched_actions: list[ResolvedAction],
    ctx: OrderContext,
) -> list[ResolvedAction]:
    """Resolve conflicts among matched actions.

    Strategy:
    1. Among exclusive actions (cancel, hold, whatsapp_confirm, auto_approve),
       the most restrictive one wins.
    2. Non-conflicting actions (add_tag, add_note, send_notification) all
       coexist with the winning exclusive action.
    3. ``cancel_order`` is suppressed if ``score_type != "final"``.
    4. ``cancel_order`` is suppressed if installation is < 30 days old.
    """
    exclusive: list[ResolvedAction] = []
    non_conflicting: list[ResolvedAction] = []

    for action in matched_actions:
        if action.action_type in _EXCLUSIVE_ACTIONS:
            exclusive.append(action)
        elif action.action_type in _NON_CONFLICTING_ACTIONS:
            non_conflicting.append(action)
        else:
            # Unknown action type — treat as non-conflicting
            non_conflicting.append(action)

    result: list[ResolvedAction] = list(non_conflicting)

    if exclusive:
        # Sort by priority descending (most restrictive first)
        exclusive.sort(
            key=lambda a: _EXCLUSIVE_ACTIONS.get(a.action_type, 0), reverse=True
        )
        winner = exclusive[0]

        # Safety gate: cancel_order only on final score
        if winner.action_type == "cancel_order" and ctx.score_type != "final":
            logger.info(
                "cancel_order suppressed: score_type=%s (requires final)",
                ctx.score_type,
            )
            # Fall through to next most restrictive
            for fallback in exclusive[1:]:
                if fallback.action_type != "cancel_order":
                    winner = fallback
                    break
            else:
                # All exclusive actions were cancel_order — no exclusive action
                winner = None  # type: ignore[assignment]

        # Safety gate: cancel_order blocked during first 30 days
        if (
            winner
            and winner.action_type == "cancel_order"
            and ctx.installed_at is not None
        ):
            now = datetime.now(UTC)
            installed = (
                ctx.installed_at
                if ctx.installed_at.tzinfo
                else ctx.installed_at.replace(tzinfo=UTC)
            )
            days_since_install = (now - installed).days
            if days_since_install < 30:
                logger.info(
                    "cancel_order suppressed: installation is %d days old (requires 30+)",
                    days_since_install,
                )
                # Fall through to next
                for fallback in exclusive[1:]:
                    if fallback.action_type != "cancel_order":
                        winner = fallback
                        break
                else:
                    winner = None  # type: ignore[assignment]

        if winner:
            result.append(winner)

    return result


# ── Main entry point ─────────────────────────────────────────────────────────


def evaluate_rules(
    rules: list,
    ctx: OrderContext,
    trigger_event: str,
) -> list[ResolvedAction]:
    """Evaluate automation rules against an order context.

    Parameters
    ----------
    rules:
        List of ``AutomationRuleModel`` instances (or any object with
        ``.is_active``, ``.trigger_event``, ``.conditions``, ``.actions``,
        ``.id``, ``.name`` attributes).
    ctx:
        The order context to match against.
    trigger_event:
        The current trigger event (e.g., ``"order.created"``, ``"risk_scored"``).

    Returns
    -------
    list[ResolvedAction]
        The resolved list of actions to execute (after conflict resolution).
    """
    matched_actions: list[ResolvedAction] = []

    for rule in rules:
        if not rule.is_active:
            continue
        if rule.trigger_event != trigger_event:
            continue
        if not _matches_conditions(rule.conditions or {}, ctx):
            continue

        # Rule matched — collect all its actions
        for action_def in rule.actions or []:
            action_type = action_def.get("type", "")
            if not action_type:
                continue
            params = {k: v for k, v in action_def.items() if k != "type"}
            matched_actions.append(
                ResolvedAction(
                    action_type=action_type,
                    params=params,
                    source_rule_id=str(rule.id),
                    source_rule_name=rule.name,
                )
            )

    if not matched_actions:
        return []

    return _resolve_conflicts(matched_actions, ctx)
