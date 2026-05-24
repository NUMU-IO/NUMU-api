"""Auto-match rule evaluator — feature 002 US4.

Called at funnel-event ingest AFTER short_code resolution (explicit URLs
win). Evaluates all of a store's rule groups in priority order against
the incoming event's UTM dimensions; first match wins.

Performance: the rule set is fetched ONCE per request via the
caller-scoped LRU cache helper (`prefetch_rules`). For ingest hot
path on a store with ~100 rules, the total in-memory eval is < 1ms
(linear scan + str ops, no regex compilation).
"""

from __future__ import annotations

from uuid import UUID

from src.infrastructure.repositories.campaign_auto_match_repository import (
    CampaignAutoMatchRepository,
    RuleGroup,
)

# Field name → reader. Centralized so adding utm_term/utm_content later
# is a one-liner. The keys match the DB CHECK constraint
# ``ck_camr_field`` exactly.
_FIELD_GETTERS = {
    "utm_source": lambda utms: utms.get("utm_source"),
    "utm_medium": lambda utms: utms.get("utm_medium"),
    "utm_campaign": lambda utms: utms.get("utm_campaign"),
}


def _evaluate_condition(
    field: str,
    operator: str,
    value: str,
    utms: dict[str, str | None],
) -> bool:
    """One row's truth value against the incoming UTMs.

    The merchant-provided ``value`` was lowercased + trimmed at write
    time (server-side normalization in the POST handler) so we compare
    against the UTM string in the same normalized shape — UTMs are
    already lowercased / trimmed by `sanitize_utm` at ingest.
    """
    getter = _FIELD_GETTERS.get(field)
    if getter is None:
        return False
    incoming = getter(utms)
    if incoming is None:
        return False
    if operator == "equals":
        return incoming == value
    if operator == "starts_with":
        return incoming.startswith(value)
    if operator == "contains":
        return value in incoming
    return False


def _evaluate_group(group: RuleGroup, utms: dict[str, str | None]) -> bool:
    """Combine all of a group's conditions per its combinator.

    Empty conditions list (shouldn't happen in valid data — POST
    validation requires 1-10) returns False to avoid false-positive
    matches.
    """
    if not group.conditions:
        return False
    results = (
        _evaluate_condition(c.field, c.operator, c.value, utms)
        for c in group.conditions
    )
    if group.combinator == "AND":
        return all(results)
    if group.combinator == "OR":
        return any(results)
    return False


def match(
    rule_groups: list[RuleGroup],
    utms: dict[str, str | None],
) -> UUID | None:
    """Return the campaign_id of the first matching rule group.

    ``rule_groups`` is the store's full rule set already ordered by
    priority ASC. First-match-wins per FR-020 (store-global precedence).

    Empty utms (no UTM data at all on the request) returns None
    immediately — every rule's conditions reference utm_* fields, so an
    empty payload can't match anything.
    """
    if not any(utms.values()):
        return None
    for group in rule_groups:
        if _evaluate_group(group, utms):
            return group.campaign_id
    return None


async def resolve_via_auto_match(
    session,
    store_id: UUID,
    utms: dict[str, str | None],
) -> UUID | None:
    """Convenience wrapper: fetch + match in one call.

    Hot path callers (funnel-event ingest) should cache the
    list_for_store result per request to avoid the round trip on every
    event. This helper is fine for tests / single-event paths.
    """
    repo = CampaignAutoMatchRepository(session)
    rule_groups = await repo.list_for_store(store_id)
    return match(rule_groups, utms)
