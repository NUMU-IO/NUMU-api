"""Carrier management for the merchant hub — registry-driven.

``GET/PUT/DELETE /stores/{store_id}/shipments/carriers[/{slug}/...]``

The hub used to hardcode its own carrier array, its own inline SVG logos,
a ``PageView = "hub" | "bosta"`` union, and a per-carrier credentials
panel. Adding a carrier meant a frontend commit. These endpoints serve the
registry instead, so the hub renders whatever the backend knows about.

**The verification problem this fixes.** ``save_bosta_credentials`` set
``is_configured: True`` without ever calling Bosta, so a typo'd API key
showed a green "Live" badge. The hub worked around it client-side by
probing ``/shipments/bosta/cities`` after a save and caching the answer in
``localStorage`` — per browser, lost on clear, invisible to support.

Verification now happens **server-side** and is persisted with the
credentials, so every browser and every teammate sees the same truth.
Carriers with no safe read-only call report ``can_verify: false`` and the
UI says "configured, not verified" rather than showing a badge it cannot
justify.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P3.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_store
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.carrier_credentials import (
    carrier_settings,
    clear_credentials,
    has_credentials,
    store_credentials,
    validate_credentials,
)
from src.application.services.carrier_resolver import (
    CarrierCapabilityError,
    UnknownCarrierError,
    capability,
    carrier_catalog,
    carrier_name,
    service_for_carrier,
    spec_for,
)
from src.core.entities.store import Store
from src.core.interfaces.services.shipping_provider import CarrierApiError
from src.core.logging import get_logger
from src.infrastructure.repositories import StoreRepository

router = APIRouter(prefix="/{store_id}/shipments/carriers")
logger = get_logger(__name__)


class SaveCarrierCredentialsRequest(BaseModel):
    """Credential values, keyed by the registry's ``credential_fields``.

    Deliberately a free-form map rather than named fields: the hub
    generates the form from the registry, so the payload shape follows
    whatever that carrier declares.
    """

    credentials: dict[str, str] = Field(default_factory=dict)
    auto_create_shipment: bool | None = None


def _bad_carrier(e: UnknownCarrierError) -> HTTPException:
    return HTTPException(status_code=400, detail=e.as_detail())


def _carrier_status(store: Store, slug: str) -> dict[str, Any]:
    """Per-store connection state. **Never includes credential values.**"""
    entry = carrier_settings(store.settings, slug)
    return {
        "is_configured": has_credentials(store.settings, slug),
        "enabled": bool(entry.get("enabled", False)),
        "verified": entry.get("verified"),
        "verified_at": entry.get("verified_at"),
        "verification_error": entry.get("verification_error"),
        "last_configured": entry.get("last_configured"),
        "auto_create_shipment": bool(entry.get("auto_create_shipment", False)),
    }


@router.get(
    "",
    summary="Carrier catalog with this store's connection state",
    operation_id="list_store_carriers",
)
async def list_store_carriers(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Every selectable carrier, its capabilities, and this store's state.

    One call gives the hub everything it needs to render the carrier list
    and generate connect-forms — names in both languages, brand colour,
    tier, declared capabilities, credential field descriptors, and whether
    this store has connected and verified each one.

    The catalog half answers from the registry alone (no credentials, no
    network), so this works for a store that has connected nothing.
    """
    entries = carrier_catalog()
    for entry in entries:
        entry["status"] = _carrier_status(store, entry["slug"])
    return SuccessResponse(data=entries, message="Carriers retrieved")


@router.put(
    "/{slug}/credentials",
    summary="Save credentials for a carrier",
    operation_id="save_carrier_credentials",
)
async def save_carrier_credentials(
    request: SaveCarrierCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    slug: Annotated[str, Path(description="Carrier slug")],
):
    """Encrypt and store a carrier's credentials, then verify them.

    Verification runs inline and its result is persisted, so the response
    already tells the merchant whether the keys actually work. Saving no
    longer implies "connected" — that was the bug behind the green badge
    for a typo'd key.

    A carrier is **not** auto-enabled on save. Enabling stays an explicit
    action in shipping settings.
    """
    try:
        spec = spec_for(slug)
    except UnknownCarrierError as e:
        raise _bad_carrier(e) from e

    _, missing = validate_credentials(spec.slug, request.credentials)
    if missing:
        labels_en = ", ".join(
            f.label_en for f in spec.credential_fields if f.key in missing
        )
        labels_ar = ", ".join(
            f.label_ar for f in spec.credential_fields if f.key in missing
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_CARRIER_CREDENTIALS",
                "message_en": f"Missing required fields: {labels_en}.",
                "message_ar": f"البيانات دي ناقصة: {labels_ar}.",
                "carrier": spec.slug,
                "missing": missing,
            },
        )

    settings = await store_credentials(store.settings, spec.slug, request.credentials)
    entry = settings["shipping"][spec.slug]
    entry["last_configured"] = datetime.now(UTC).isoformat()
    if request.auto_create_shipment is not None:
        entry["auto_create_shipment"] = request.auto_create_shipment

    verified, error = await _run_verification(spec.slug, settings)
    entry["verified"] = verified
    entry["verified_at"] = datetime.now(UTC).isoformat() if verified else None
    entry["verification_error"] = error

    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data={"carrier": spec.slug, **_carrier_status(store, spec.slug)},
        message="Credentials saved",
    )


@router.delete(
    "/{slug}/credentials",
    summary="Disconnect a carrier",
    operation_id="delete_carrier_credentials",
)
async def delete_carrier_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    slug: Annotated[str, Path(description="Carrier slug")],
):
    """Remove a carrier's credentials and disable it.

    Disabling is part of disconnecting on purpose — a carrier left enabled
    with no credentials shows a live badge for something that cannot
    authenticate.
    """
    try:
        spec = spec_for(slug)
    except UnknownCarrierError as e:
        raise _bad_carrier(e) from e

    settings = clear_credentials(store.settings, spec.slug)
    entry = settings["shipping"][spec.slug]
    for key in ("verified", "verified_at", "verification_error"):
        entry.pop(key, None)

    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data={"carrier": spec.slug, **_carrier_status(store, spec.slug)},
        message="Carrier disconnected",
    )


async def _run_verification(
    slug: str, settings: dict
) -> tuple[bool | None, str | None]:
    """Call the carrier to prove its credentials work.

    Returns ``(verified, error)``. ``verified`` is None when the carrier
    declares no safe read-only call — "unknown", never a false green.
    """
    spec = spec_for(slug)
    if not spec.verification_operation:
        return None, None

    try:
        service = await service_for_carrier(slug, settings)
        probe = capability(service, spec.verification_operation, slug)
        await probe()
    except CarrierCapabilityError as e:
        return None, str(e)
    except CarrierApiError as e:
        # The carrier answered, so *why* decides who is at fault.
        if e.is_auth_failure:
            logger.info(
                "carrier_verification_rejected",
                carrier=slug,
                status=e.status_code,
            )
            return (
                False,
                f"The carrier rejected these credentials (HTTP {e.status_code}).",
            )

        # Rate limiting, an outage, or anything else we cannot attribute
        # to the credentials. Reporting this as a rejection would tell a
        # merchant their keys are wrong because the carrier is having a
        # bad day — and they would go and change working keys.
        logger.info(
            "carrier_verification_inconclusive",
            carrier=slug,
            status=e.status_code,
        )
        return None, (
            f"The carrier is not answering right now (HTTP {e.status_code}). "
            f"Your credentials have not been checked."
        )

    except Exception as e:
        # Never reached the carrier at all — a timeout, a DNS failure, or
        # Cloudflare 403'ing a non-browser user agent, which this platform
        # already sees on api.numueg.app. "We could not check", not "the
        # carrier rejected these keys".
        if isinstance(e, httpx.TimeoutException | httpx.TransportError):
            logger.info("carrier_verification_unreachable", carrier=slug, error=str(e))
            return None, f"Could not reach the carrier: {str(e)[:400]}"

        # An unexpected failure in our own code is not evidence about the
        # merchant's credentials either.
        logger.warning("carrier_verification_errored", carrier=slug, error=str(e))
        return None, f"Could not check the credentials: {str(e)[:400]}"

    return True, None


@router.post(
    "/{slug}/verify",
    summary="Verify a carrier's stored credentials",
    operation_id="verify_carrier_credentials",
)
async def verify_carrier_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    slug: Annotated[str, Path(description="Carrier slug")],
):
    """Re-check stored credentials against the carrier and persist the result.

    This replaces the hub's per-browser ``localStorage`` probe: the answer
    is stored with the credentials, so every browser, teammate and support
    agent sees the same state.

    Returns 200 with ``verified: null`` for a carrier that declares no
    verification call — an honest "unknown" rather than a green badge.
    """
    try:
        spec = spec_for(slug)
    except UnknownCarrierError as e:
        raise _bad_carrier(e) from e

    if not has_credentials(store.settings, spec.slug):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CARRIER_NOT_CONFIGURED",
                "message_en": (
                    f"{carrier_name(spec.slug, 'en')} has no credentials to verify."
                ),
                "message_ar": (
                    f"{carrier_name(spec.slug, 'ar')} مفيش بيانات ربط نتأكد منها."
                ),
                "carrier": spec.slug,
            },
        )

    verified, error = await _run_verification(spec.slug, store.settings or {})

    settings = dict(store.settings or {})
    shipping = dict(settings.get("shipping", {}))
    entry = dict(shipping.get(spec.slug, {}))
    entry["verified"] = verified
    entry["verified_at"] = datetime.now(UTC).isoformat() if verified else None
    entry["verification_error"] = error
    # Deliberately does NOT disable the carrier.
    #
    # An earlier version did, reasoning that a carrier whose credentials
    # fail would fail every booking anyway. But verification fails for
    # reasons that have nothing to do with the credentials — a carrier
    # outage, a timeout, Cloudflare blocking us — and a merchant who
    # re-saved their key during a blip would have found their shipping
    # switched off. Disabling is destructive and the merchant's call; the
    # badge going red and naming the error is what this owes them.
    shipping[spec.slug] = entry
    settings["shipping"] = shipping

    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data={"carrier": spec.slug, **_carrier_status(store, spec.slug)},
        message="Verification complete",
    )
