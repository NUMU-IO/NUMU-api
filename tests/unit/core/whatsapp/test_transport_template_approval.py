"""``requires_template_approval`` — which stores the Meta approval gate binds.

The send-guard runs before the transport is resolved, so this predicate is the
only thing telling it whether the store will send a template *reference* (Meta,
where an unapproved name is a hard 400) or finished text (GOWA, where Meta's
verdict is irrelevant). Getting it wrong in the strict direction silently drops
a GOWA store's order notifications — the bug these tests exist to prevent.
"""

import pytest

from src.config.settings import settings
from src.infrastructure.external_services.whatsapp import requires_template_approval


@pytest.mark.parametrize(
    "store_settings",
    [
        None,
        {},
        {"whatsapp": {}},
        {"whatsapp": {"provider": "meta_cloud"}},
        # An unrecognised value must NOT be read as GOWA — see
        # resolve_provider_name: a typo can never route a store onto the
        # unofficial transport while the platform default is Meta.
        {"whatsapp": {"provider": "gowaa"}},
        {"whatsapp": {"provider": ""}},
    ],
)
def test_meta_stores_still_require_approval(store_settings: dict | None) -> None:
    assert requires_template_approval(store_settings) is True


def test_gowa_store_does_not_require_approval() -> None:
    """The fix: a store deliberately switched to GOWA is not gated on Meta."""
    assert requires_template_approval({"whatsapp": {"provider": "gowa"}}) is False


def test_platform_default_moves_unset_stores_off_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follows the fleet default, exactly as the resolver does.

    With GOWA_PLATFORM_DEFAULT on, a store that has expressed no preference
    sends over GOWA — so the guard must judge it by GOWA's rules or it would
    block sends the resolver is about to route away from Meta entirely.
    """
    monkeypatch.setattr(settings, "gowa_enabled", True)
    monkeypatch.setattr(settings, "gowa_platform_default", True)

    assert requires_template_approval({}) is False
    # An explicit per-store choice still wins over the fleet default.
    assert requires_template_approval({"whatsapp": {"provider": "meta_cloud"}}) is True


def test_platform_default_needs_gowa_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``gowa_platform_default`` alone is inert — the transport must be on."""
    monkeypatch.setattr(settings, "gowa_enabled", False)
    monkeypatch.setattr(settings, "gowa_platform_default", True)

    assert requires_template_approval({}) is True
