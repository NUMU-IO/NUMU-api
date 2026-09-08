"""Provider-neutral view of the ad-platform conversion rails.

NUMU sends the same internal commerce funnel to more than one ad platform.
Each platform needs the same five things done, differently:

  1. name the event         — ``order_completed`` was ``CompletePayment`` on
                               TikTok and ``Purchase`` on Meta until
                               2026-09-08; the maps drift and must not
  2. hash the identity      — Meta hashes bare E.164 digits, TikTok hashes
                               the E.164 string WITH its ``+``
  3. shape the payload      — Meta consumes ``custom_data`` as built; TikTok
                               needs it remapped into ``properties``
  4. resolve the targets    — which of the store's pixels are enabled for the
                               server rail
  5. read the answer        — both vendors hide failures behind a 200

Until now those five lived as parallel pairs of functions with no name for
the thing they were parallel *about*, and the call sites branched on a
``platform: str``. This module gives the set a name and a registry.

**It deliberately does NOT unify the enqueue paths.** ``_maybe_enqueue_meta_capi``
and ``_maybe_enqueue_tiktok_capi`` carry substantial vendor-specific identity
enrichment; merging them would be a large rewrite of the platform's hottest
path with no behavioural change to show for it. This is the seam, not the
migration — a third provider plugs in here, and the existing rails keep
working exactly as they do.

Imports are deferred into the accessors so that importing this module never
drags in Celery, the ORM or httpx — the registry is safe to read from
anywhere, including schema and validation code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConversionProvider:
    """One ad platform's conversion rail.

    ``key`` is the string the existing call sites already branch on
    (``"meta"`` / ``"tiktok"``), so a provider can be swapped in wherever
    that string is passed today without changing any stored value.
    """

    key: str
    label: str
    #: funnel step -> vendor event name, or None when the step is not sent
    event_name_for_step: Callable[[str], str | None]
    #: raw NUMU user-data dict -> the vendor's hashed identity object
    hash_user_data: Callable[[dict], dict[str, Any]]
    #: Meta-shaped custom_data -> the vendor's payload object
    map_properties: Callable[[dict[str, Any]], dict[str, Any]]
    #: (http_status, business_code, body) -> failure kind, or None if
    #: delivered. Meta accepts and ignores ``business_code``.
    classify_response: Callable[..., Any]
    #: (order, store) -> raw user_data for the order-based Purchase. Takes
    #: ``store`` on both providers even though only Meta reads it (for the
    #: event-source host): a uniform signature is the point — if the caller
    #: still has to branch, the abstraction has not earned its place.
    build_user_data_from_order: Callable[[Any, Any], dict[str, Any]]
    #: (order, catalog_ids) -> Meta-shaped custom_data for that order
    build_custom_data_from_order: Callable[..., dict[str, Any]]


def _meta() -> ConversionProvider:
    from src.application.services.meta_capi_purchase_dispatcher import (
        _build_custom_data_from_order,
        _build_user_data_from_order,
        _store_host,
    )
    from src.core.services.meta_delivery_policy import classify_status
    from src.infrastructure.external_services.meta.hashing import hash_user_data
    from src.infrastructure.messaging.tasks.meta_capi import (
        FUNNEL_STEP_TO_META_EVENT,
    )

    return ConversionProvider(
        key="meta",
        label="Meta",
        event_name_for_step=FUNNEL_STEP_TO_META_EVENT.get,
        hash_user_data=hash_user_data,
        # Meta's CAPI takes `custom_data` as the funnel builds it — the
        # identity mapping is the only transform, so this is deliberately
        # the identity function rather than a no-op wrapper pretending to
        # be symmetric with TikTok's.
        map_properties=lambda custom_data: custom_data,
        # Meta has no business-code dimension: it reports failure through
        # the HTTP status plus an error body, so `code` is accepted and
        # ignored to keep one signature across providers. TikTok's whole
        # policy module exists because it DOES need that third argument.
        classify_response=lambda http_status, code=None, body=None: classify_status(
            http_status, body
        ),
        build_user_data_from_order=lambda order, store: _build_user_data_from_order(
            order, host=_store_host(store)
        ),
        build_custom_data_from_order=_build_custom_data_from_order,
    )


def _tiktok() -> ConversionProvider:
    from src.application.services.tiktok_capi_purchase_dispatcher import (
        _build_custom_data_from_order,
        _build_user_data_from_order,
    )
    from src.core.services.tiktok_delivery_policy import classify_response
    from src.infrastructure.external_services.tiktok.hashing import (
        hash_tiktok_user_data,
    )
    from src.infrastructure.messaging.tasks.tiktok_capi import (
        FUNNEL_STEP_TO_TIKTOK_EVENT,
        _to_tiktok_properties,
    )

    return ConversionProvider(
        key="tiktok",
        label="TikTok",
        event_name_for_step=FUNNEL_STEP_TO_TIKTOK_EVENT.get,
        hash_user_data=hash_tiktok_user_data,
        map_properties=_to_tiktok_properties,
        classify_response=classify_response,
        # `store` is accepted and unused — TikTok's builder reads the click-id
        # snapshot off the order itself and needs no event-source host.
        build_user_data_from_order=lambda order, store: _build_user_data_from_order(
            order
        ),
        build_custom_data_from_order=_build_custom_data_from_order,
    )


_BUILDERS: dict[str, Callable[[], ConversionProvider]] = {
    "meta": _meta,
    "tiktok": _tiktok,
}

_CACHE: dict[str, ConversionProvider] = {}


def get_provider(key: str) -> ConversionProvider:
    """Return the provider for ``key`` (``"meta"`` / ``"tiktok"``).

    Raises ``KeyError`` for an unknown key rather than falling back to a
    default: silently sending a shopper's hashed identity to the wrong ad
    platform is worse than a 500.
    """
    if key not in _CACHE:
        _CACHE[key] = _BUILDERS[key]()
    return _CACHE[key]


def all_providers() -> tuple[ConversionProvider, ...]:
    """Every configured provider, in a stable order."""
    return tuple(get_provider(k) for k in _BUILDERS)


PROVIDER_KEYS: tuple[str, ...] = tuple(_BUILDERS)
