"""Entitlements and feature flags: the rules, with no I/O.

Two questions, kept apart on purpose:

* entitlement: may this tenant use feature X, and how much of it?
  (plans, add-ons, admin overrides, the global kill switch)
* flag: is release Y switched on for this tenant yet?
  (master switch, per-tenant targets, percentage rollout)

A route that needs both asks both. Nothing here reads the database, Redis or
the clock, so every rule can be tested as a plain function call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

#: The only non-numeric limit value. Never -1: a negative number still reads
#: as a limit, so `used < limit` quietly fails for every unlimited tenant.
UNLIMITED: Final = "unlimited"

Value = bool | int | Literal["unlimited"]
Kind = Literal["boolean", "limit"]


@dataclass(frozen=True)
class Feature:
    key: str
    kind: Kind
    default: Value
    #: False is the global kill switch: nobody may use it, whatever they paid.
    enabled: bool = True


@dataclass(frozen=True)
class Grant:
    """One layer that offers a value: a plan, an add-on, an override."""

    #: plan | addon | override | default
    source: str
    source_id: str | None
    value: Value
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Resolved:
    key: str
    value: Value
    source: str
    source_id: str | None
    #: When this value next changes on its own, if ever.
    expires_at: datetime | None
    available: bool
    #: disabled_globally | blocked | not_in_plan, or None when available.
    reason: str | None
    #: Layers that lost to an override, so "why" can show the whole stack.
    shadowed: tuple[Grant, ...] = ()


def check_value(kind: Kind, value: object) -> Value:
    """The value, if it is valid for the kind. Raises ValueError otherwise.

    Booleans are excluded from limits on purpose: ``True`` is an ``int`` in
    Python, so a bare ``isinstance(value, int)`` would accept it as 1.
    """
    if kind == "boolean" and isinstance(value, bool):
        return value
    if kind == "limit" and value == UNLIMITED:
        return UNLIMITED
    if (
        kind == "limit"
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    ):
        return value
    raise ValueError(f"{value!r} is not a valid {kind} value")


def is_on(kind: Kind, value: Value) -> bool:
    if kind == "boolean":
        return value is True
    return value == UNLIMITED or value > 0  # type: ignore[operator]


def active(grant: Grant, now: datetime) -> bool:
    return (grant.starts_at is None or grant.starts_at <= now) and (
        grant.expires_at is None or now < grant.expires_at
    )


def _combine(kind: Kind, a: Value, b: Value) -> Value:
    if kind == "boolean":
        return a is True or b is True
    if UNLIMITED in (a, b):
        return UNLIMITED
    return int(a) + int(b)


def resolve(
    feature: Feature,
    *,
    bundles: list[Grant],
    override: Grant | None,
    now: datetime,
) -> Resolved:
    """One feature for one tenant.

    Order: a live override wins outright (it can grant or deny); otherwise the
    plan and every live add-on merge (booleans OR, limits add up, unlimited
    absorbs); otherwise the feature default. The kill switch is not a layer in
    that order: it gates the result and leaves the value visible for "why".
    """
    live = [g for g in bundles if active(g, now)]
    shadowed: tuple[Grant, ...] = ()
    if override is not None and active(override, now):
        value: Value = override.value
        winners = [override]
        shadowed = tuple(live)
    elif live:
        value = live[0].value
        for grant in live[1:]:
            value = _combine(feature.kind, value, grant.value)
        winners = [
            g for g in live if feature.kind == "limit" or g.value is True
        ] or live[:1]
    else:
        value = feature.default
        winners = [Grant("default", None, feature.default)]

    ends = [g.expires_at for g in winners]
    if feature.kind == "boolean" and value is True:
        # Stays on while any grant still gives it.
        expires_at = None if None in ends else max(e for e in ends if e)
    else:
        # A limit changes when any contributor drops out.
        expires_at = min((e for e in ends if e), default=None)

    on = is_on(feature.kind, value)
    if not feature.enabled:
        reason: str | None = "disabled_globally"
    elif not on:
        reason = "blocked" if winners[0].source == "override" else "not_in_plan"
    else:
        reason = None
    return Resolved(
        key=feature.key,
        value=value,
        source=winners[0].source,
        source_id=winners[0].source_id,
        expires_at=expires_at,
        available=feature.enabled and on,
        reason=reason,
        shadowed=shadowed,
    )


def next_change(grants: list[Grant], now: datetime) -> datetime | None:
    """The earliest future moment any grant starts or ends.

    A cached snapshot must not outlive it, which is what makes expiry a
    WHERE clause instead of a cron job.
    """
    return min(
        (t for g in grants for t in (g.starts_at, g.expires_at) if t and t > now),
        default=None,
    )


# ─── Flags ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Flag:
    key: str
    #: Master switch. False turns the release off for everyone, targets too.
    enabled: bool
    #: 0-100, for tenants without a target.
    rollout_percent: int


@dataclass(frozen=True)
class FlagTarget:
    enabled: bool
    expires_at: datetime | None = None


def bucket(flag_key: str, tenant_id: str) -> int:
    """A stable slot in 0-9999 for (flag, tenant).

    sha256, not hash(): Python salts hash() per process, so one tenant would
    land in a different bucket on every worker. The flag key is part of the
    input so each rollout samples a different slice of tenants, instead of
    the same unlucky 10% getting every beta.
    """
    digest = hashlib.sha256(f"{flag_key}:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


def flag_on(
    flag: Flag | None,
    tenant_id: str | None,
    target: FlagTarget | None,
    now: datetime,
) -> tuple[bool, str]:
    """Whether the flag is on for this tenant, and why."""
    if flag is None:
        return False, "unknown_flag"
    if not flag.enabled:
        return False, "flag_off"
    if target is not None and (target.expires_at is None or now < target.expires_at):
        return target.enabled, "targeted"
    if flag.rollout_percent >= 100:
        return True, "everyone"
    if tenant_id and bucket(flag.key, tenant_id) < flag.rollout_percent * 100:
        return True, "rollout"
    return False, "not_in_rollout"
