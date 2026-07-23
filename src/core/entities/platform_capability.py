"""Platform capability registry — the control plane for what may extend NUMU.

ADR-0 established that the platform already had five capability-shaped
mechanisms (dynamic sources, theme sections, layout injections, app embeds,
metafields) each with its own ad-hoc control surface and no single record.
ADR-6 then required that record to express *who* may hold each capability,
because a partner platform whose permissions are self-declared is not a
permission system — the theme fleet's `supported_features` is declared by the
developer, shown to merchants, and cross-checked against nothing.

This module is the registry's core: the vocabulary, and the grant decision.
It has no external dependencies by design — the grant rule is the thing most
worth being able to read, test, and reason about in isolation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ExtensionTier(StrEnum):
    """Who is asking for a capability.

    Ordered: a tier may hold anything its own tier or below can hold. Compare
    with :func:`tier_rank`, never with ``<`` on the enum itself.
    """

    PARTNER = "partner"  # third-party developers
    VERIFIED = "verified"  # audited partners under a data-processing agreement
    FIRST_PARTY = "first_party"  # NUMU itself


_TIER_ORDER: dict[str, int] = {
    ExtensionTier.PARTNER.value: 0,
    ExtensionTier.VERIFIED.value: 1,
    ExtensionTier.FIRST_PARTY.value: 2,
}


def tier_rank(tier: ExtensionTier | str) -> int:
    """Rank a tier for comparison. Unknown tiers rank lowest (fail closed)."""
    value = tier.value if isinstance(tier, ExtensionTier) else str(tier)
    return _TIER_ORDER.get(value, -1)


class DataClassification(StrEnum):
    """What a capability exposes, which is what decides the floor tier.

    ``CROSS_MERCHANT_AGGREGATE`` is the reason this enum exists. The Trust
    Network's value is reputation computed across many merchants, and that is
    exactly the data a partner must never reach. An extension may receive a
    derived score for an identifier; it may never receive the rows behind it.
    """

    PUBLIC = "public"  # already visible to any shopper
    TENANT_SCOPED = "tenant_scoped"  # the installing merchant's own data
    TENANT_PRIVATE = "tenant_private"  # merchant data the shopper never sees
    CROSS_MERCHANT_AGGREGATE = "cross_merchant_aggregate"  # first-party only


# The floor tier implied by a classification. A capability may set a HIGHER
# min_tier than its classification requires, never a lower one -- enforced in
# `PlatformCapability.effective_min_tier`.
_CLASSIFICATION_FLOOR: dict[str, ExtensionTier] = {
    DataClassification.PUBLIC.value: ExtensionTier.PARTNER,
    DataClassification.TENANT_SCOPED.value: ExtensionTier.PARTNER,
    DataClassification.TENANT_PRIVATE.value: ExtensionTier.VERIFIED,
    DataClassification.CROSS_MERCHANT_AGGREGATE.value: ExtensionTier.FIRST_PARTY,
}


class CapabilityKind(StrEnum):
    """The five capability-shaped mechanisms from ADR-0, plus guarantees."""

    DYNAMIC_SOURCE = "dynamic_source"
    SECTION = "section"
    LAYOUT_INJECTION = "layout_injection"
    APP_EMBED = "app_embed"
    DATA_TYPE = "data_type"
    GUARANTEE = "guarantee"


class LifecycleState(StrEnum):
    DRAFT = "draft"
    PILOT = "pilot"
    GA = "ga"
    DEPRECATED = "deprecated"
    RETIRED = "retired"
    SUSPENDED = "suspended"


#: Lifecycle states in which a capability may be granted at all. Retired and
#: suspended are hard stops; suspension is the platform's kill switch and must
#: take effect at grant time, not only at render time.
_GRANTABLE_STATES = frozenset({
    LifecycleState.PILOT.value,
    LifecycleState.GA.value,
    LifecycleState.DEPRECATED.value,
})


class UnavailableBehavior(StrEnum):
    """What happens when the capability cannot be served.

    Default is FAIL_OPEN. An extension that is slow, erroring or suspended must
    not take a storefront or a checkout down with it. FAIL_CLOSED exists for the
    cases where proceeding without the capability is worse than not proceeding
    (a fraud check a merchant genuinely wants blocking) and is the merchant's
    explicit risk decision, never a platform default.

    This mirrors the split the checksum work surfaced: fail-closed is right for
    integrity (a tampered bundle must not run), dangerous for availability (a
    stale digest must not blank a store).
    """

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class GrantDecision(BaseEntity):
    """The outcome of asking for one capability."""

    capability_slug: str
    granted: bool
    reason: str | None = None


class PlatformCapability(BaseEntity):
    """One governed capability record."""

    slug: str = Field(max_length=128)
    kind: CapabilityKind
    owner: str = Field(max_length=128)
    lifecycle_state: LifecycleState = LifecycleState.DRAFT
    data_classification: DataClassification = DataClassification.TENANT_SCOPED
    # May raise the floor implied by the classification, never lower it.
    min_tier: ExtensionTier = ExtensionTier.PARTNER
    unavailable_behavior: UnavailableBehavior = UnavailableBehavior.FAIL_OPEN
    active_version: str | None = None
    supported_versions: list[str] = Field(default_factory=list)
    placements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None

    @property
    def effective_min_tier(self) -> ExtensionTier:
        """The tier actually required, honouring the classification floor.

        A misconfigured row that asks for a lower tier than its data allows is
        corrected upward here rather than trusted. Getting this backwards is
        how a registry silently stops being a permission system.
        """
        floor = _CLASSIFICATION_FLOOR.get(
            self.data_classification.value, ExtensionTier.FIRST_PARTY
        )
        return self.min_tier if tier_rank(self.min_tier) > tier_rank(floor) else floor

    def evaluate(self, tier: ExtensionTier) -> GrantDecision:
        """Decide whether ``tier`` may hold this capability."""
        if self.lifecycle_state.value not in _GRANTABLE_STATES:
            return GrantDecision(
                capability_slug=self.slug,
                granted=False,
                reason=(
                    f"capability '{self.slug}' is {self.lifecycle_state.value} "
                    "and cannot be granted"
                ),
            )

        required = self.effective_min_tier
        if tier_rank(tier) < tier_rank(required):
            return GrantDecision(
                capability_slug=self.slug,
                granted=False,
                reason=(
                    f"capability '{self.slug}' requires tier '{required.value}' "
                    f"({self.data_classification.value} data); requester is "
                    f"'{tier.value if isinstance(tier, ExtensionTier) else tier}'"
                ),
            )

        return GrantDecision(capability_slug=self.slug, granted=True)


class ScopeGrant(BaseEntity):
    """The result of evaluating an extension's whole manifest."""

    extension_id: UUID | None = None
    tier: ExtensionTier
    granted: list[str] = Field(default_factory=list)
    denied: list[GrantDecision] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.denied


def evaluate_manifest(
    *,
    tier: ExtensionTier,
    requested_slugs: list[str],
    registry: dict[str, PlatformCapability],
    extension_id: UUID | None = None,
) -> ScopeGrant:
    """Evaluate every capability an extension's manifest requests.

    A manifest is a *request*, never a grant. Two rules make that real:

    1. An unknown capability is DENIED, not ignored. Silently dropping an
       unrecognised slug is how a typo becomes a capability an extension
       believes it holds.
    2. A denied capability is a hard failure for the whole manifest (callers
       check ``ok``), not a silent downgrade to a smaller permission set. An
       extension that asked for cross-merchant data and quietly got tenant-only
       data would behave incorrectly rather than fail loudly.
    """
    granted: list[str] = []
    denied: list[GrantDecision] = []

    for slug in requested_slugs:
        capability = registry.get(slug)
        if capability is None:
            denied.append(
                GrantDecision(
                    capability_slug=slug,
                    granted=False,
                    reason=f"unknown capability '{slug}' — not in the registry",
                )
            )
            continue

        decision = capability.evaluate(tier)
        if decision.granted:
            granted.append(slug)
        else:
            denied.append(decision)

    return ScopeGrant(
        extension_id=extension_id, tier=tier, granted=granted, denied=denied
    )
