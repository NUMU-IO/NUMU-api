"""Grant-rule tests for the platform capability registry.

The rule these protect: a manifest is a request, never a grant. The theme
fleet's `supported_features` is the counter-example -- declared by the
developer, shown to merchants, verified against nothing.
"""

import pytest

from src.core.entities.platform_capability import (
    CapabilityKind,
    DataClassification,
    ExtensionTier,
    LifecycleState,
    PlatformCapability,
    UnavailableBehavior,
    evaluate_manifest,
    tier_rank,
)


def cap(**over) -> PlatformCapability:
    base = {
        "slug": "test.capability",
        "kind": CapabilityKind.DATA_TYPE,
        "owner": "platform",
        "lifecycle_state": LifecycleState.GA,
    }
    base.update(over)
    return PlatformCapability(**base)


class TestTierOrdering:
    def test_tiers_are_ordered(self):
        assert tier_rank(ExtensionTier.PARTNER) < tier_rank(ExtensionTier.VERIFIED)
        assert tier_rank(ExtensionTier.VERIFIED) < tier_rank(ExtensionTier.FIRST_PARTY)

    def test_unknown_tier_ranks_lowest_so_it_fails_closed(self):
        # A tier string that isn't in the enum must never out-rank a real one.
        assert tier_rank("superuser") < tier_rank(ExtensionTier.PARTNER)


class TestClassificationFloor:
    def test_cross_merchant_data_is_first_party_only(self):
        # The load-bearing rule. Trust Network's cross-merchant reputation is
        # exactly what a partner must never reach.
        c = cap(data_classification=DataClassification.CROSS_MERCHANT_AGGREGATE)
        assert c.effective_min_tier is ExtensionTier.FIRST_PARTY
        assert not c.evaluate(ExtensionTier.PARTNER).granted
        assert not c.evaluate(ExtensionTier.VERIFIED).granted
        assert c.evaluate(ExtensionTier.FIRST_PARTY).granted

    def test_a_row_cannot_lower_its_own_floor(self):
        # A misconfigured row asking for partner access to cross-merchant data
        # is corrected upward, not trusted. Getting this backwards is how a
        # registry silently stops being a permission system.
        c = cap(
            data_classification=DataClassification.CROSS_MERCHANT_AGGREGATE,
            min_tier=ExtensionTier.PARTNER,
        )
        assert c.effective_min_tier is ExtensionTier.FIRST_PARTY
        assert not c.evaluate(ExtensionTier.PARTNER).granted

    def test_a_row_may_raise_its_floor(self):
        c = cap(
            data_classification=DataClassification.TENANT_SCOPED,
            min_tier=ExtensionTier.VERIFIED,
        )
        assert c.effective_min_tier is ExtensionTier.VERIFIED
        assert not c.evaluate(ExtensionTier.PARTNER).granted
        assert c.evaluate(ExtensionTier.VERIFIED).granted

    def test_private_tenant_data_needs_verified(self):
        c = cap(data_classification=DataClassification.TENANT_PRIVATE)
        assert not c.evaluate(ExtensionTier.PARTNER).granted
        assert c.evaluate(ExtensionTier.VERIFIED).granted

    def test_public_and_tenant_scoped_are_open_to_partners(self):
        for classification in (
            DataClassification.PUBLIC,
            DataClassification.TENANT_SCOPED,
        ):
            assert (
                cap(data_classification=classification)
                .evaluate(ExtensionTier.PARTNER)
                .granted
            )


class TestLifecycleGating:
    @pytest.mark.parametrize(
        "state",
        [LifecycleState.PILOT, LifecycleState.GA, LifecycleState.DEPRECATED],
    )
    def test_grantable_states(self, state):
        assert cap(lifecycle_state=state).evaluate(ExtensionTier.PARTNER).granted

    @pytest.mark.parametrize(
        "state",
        [LifecycleState.DRAFT, LifecycleState.RETIRED, LifecycleState.SUSPENDED],
    )
    def test_ungrantable_states(self, state):
        decision = cap(lifecycle_state=state).evaluate(ExtensionTier.FIRST_PARTY)
        assert not decision.granted
        assert state.value in decision.reason

    def test_suspension_blocks_even_first_party(self):
        # Suspension is the platform's kill switch; it has to bite at grant
        # time, not only at render time.
        c = cap(lifecycle_state=LifecycleState.SUSPENDED)
        assert not c.evaluate(ExtensionTier.FIRST_PARTY).granted


class TestManifestEvaluation:
    def test_unknown_capability_is_denied_not_ignored(self):
        # Silently dropping an unrecognised slug is how a typo becomes a
        # capability an extension believes it holds.
        grant = evaluate_manifest(
            tier=ExtensionTier.PARTNER, requested_slugs=["nope.typo"], registry={}
        )
        assert not grant.ok
        assert "unknown capability" in grant.denied[0].reason

    def test_denial_is_a_whole_manifest_failure_not_a_downgrade(self):
        registry = {
            "orders.read": cap(slug="orders.read"),
            "network.reputation": cap(
                slug="network.reputation",
                data_classification=DataClassification.CROSS_MERCHANT_AGGREGATE,
            ),
        }
        grant = evaluate_manifest(
            tier=ExtensionTier.PARTNER,
            requested_slugs=["orders.read", "network.reputation"],
            registry=registry,
        )
        # The permitted scope is still reported, but the manifest as a whole
        # fails -- an extension that asked for cross-merchant data and quietly
        # got tenant-only data would behave incorrectly rather than fail loudly.
        assert grant.granted == ["orders.read"]
        assert not grant.ok
        assert grant.denied[0].capability_slug == "network.reputation"

    def test_first_party_gets_everything_in_the_registry(self):
        registry = {
            "a": cap(slug="a", data_classification=DataClassification.PUBLIC),
            "b": cap(slug="b", data_classification=DataClassification.TENANT_PRIVATE),
            "c": cap(
                slug="c",
                data_classification=DataClassification.CROSS_MERCHANT_AGGREGATE,
            ),
        }
        grant = evaluate_manifest(
            tier=ExtensionTier.FIRST_PARTY,
            requested_slugs=["a", "b", "c"],
            registry=registry,
        )
        assert grant.ok
        assert grant.granted == ["a", "b", "c"]

    def test_empty_manifest_is_trivially_ok(self):
        grant = evaluate_manifest(
            tier=ExtensionTier.PARTNER, requested_slugs=[], registry={}
        )
        assert grant.ok
        assert grant.granted == []


class TestDefaults:
    def test_availability_defaults_to_fail_open(self):
        # A failing extension must not take a storefront or checkout down.
        assert cap().unavailable_behavior is UnavailableBehavior.FAIL_OPEN

    def test_lifecycle_defaults_to_draft_so_new_rows_grant_nothing(self):
        c = PlatformCapability(
            slug="fresh", kind=CapabilityKind.SECTION, owner="platform"
        )
        assert c.lifecycle_state is LifecycleState.DRAFT
        assert not c.evaluate(ExtensionTier.FIRST_PARTY).granted
