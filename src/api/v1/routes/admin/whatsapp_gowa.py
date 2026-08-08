"""Admin routes for the GOWA WhatsApp transport (SUPER_ADMIN only).

Staff switch an individual merchant between the Meta Cloud transport and GOWA,
and pair the WhatsApp account GOWA will send as.

## Why this is staff-only

GOWA is unofficial: it links as a companion device on a real WhatsApp account
and sends automated traffic from it, which is what WhatsApp bans numbers for.
For a BYO merchant that is the number their customers already know. Switching a
merchant onto it is therefore an attributable decision made by a person who has
had the conversation, not a self-serve toggle — ``acknowledge_risk`` is recorded
against the admin user who flipped it.

## Pairing is a live, human-paced operation

WhatsApp issues a short-lived pairing code for a SPECIFIC phone number, so the
order is fixed: staff take the merchant's number, generate a code, and read it
out while the merchant is on the phone. The code cannot be produced in advance
or emailed. ``/status`` exists so the operator sees the session flip to
connected without refreshing.

The QR alternative is proxied deliberately: GOWA returns a link whose host is
hardcoded ``127.0.0.1``, and its own port is bound to loopback and firewalled to
this API, so an admin browser can never fetch it directly. We fetch the PNG
server-side and inline it.
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.config.settings import settings
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
    WhatsAppGowaDeviceRepository,
)

router = APIRouter(
    prefix="/whatsapp/gowa",
    tags=["Admin - WhatsApp GOWA"],
    dependencies=[Depends(require_admin)],
)

_TIMEOUT = 20.0


# ── schemas ────────────────────────────────────────────────────────────────


class ProviderUpdate(BaseModel):
    provider: Literal["meta_cloud", "gowa"]
    # Required to move a store ONTO gowa. Named for what it is: the operator
    # confirming they understand the merchant's number can be banned.
    acknowledge_risk: bool = False


class PairRequest(BaseModel):
    # E.164, the number GOWA will link to and send as.
    phone: str = Field(min_length=6, max_length=20)
    method: Literal["code", "qr"] = "code"
    acknowledge_risk: bool = False


class PairResponse(BaseModel):
    device_id: str
    method: Literal["code", "qr"]
    # Present for method="code" — what staff read out to the merchant.
    pair_code: str | None = None
    # Present for method="qr" — a data: URI, because the raw link is loopback.
    qr_data_uri: str | None = None
    expires_in_seconds: int | None = None


class DeviceStatus(BaseModel):
    provider: str
    paired: bool
    device_id: str | None = None
    phone: str | None = None
    status: str | None = None
    is_connected: bool | None = None
    is_logged_in: bool | None = None
    last_seen_at: str | None = None
    last_error: str | None = None


# ── helpers ────────────────────────────────────────────────────────────────


def _auth() -> tuple[str, str] | None:
    raw = settings.gowa_basic_auth or ""
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    return (user, password)


def _base_url() -> str:
    base = (settings.gowa_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOWA is not configured on this environment.",
        )
    return base


async def _gowa(
    method: str, path: str, *, device_id: str | None = None, **kwargs
) -> dict:
    """Call GOWA, raising a clean HTTP error rather than leaking transport noise."""
    headers = {"X-Device-Id": device_id} if device_id else {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{_base_url()}{path}",
                headers=headers,
                auth=_auth(),
                **kwargs,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GOWA unreachable: {exc}",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GOWA error {response.status_code}: {response.text[:300]}",
        )
    try:
        return response.json()
    except ValueError:
        return {}


async def _load_store(db: AsyncSession, store_id: UUID) -> StoreModel:
    store = (
        await db.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found."
        )
    return store


# ── routes ─────────────────────────────────────────────────────────────────


@router.put("/{store_id}/provider", operation_id="admin_set_whatsapp_provider")
async def set_provider(
    store_id: UUID,
    body: ProviderUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[UserModel, Depends(require_admin)],
) -> SuccessResponse[dict]:
    """Choose which WhatsApp transport this store sends through."""
    if body.provider == "gowa" and not body.acknowledge_risk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Switching a store to GOWA requires acknowledge_risk=true: it "
                "sends from a real WhatsApp account that WhatsApp can ban."
            ),
        )

    store = await _load_store(db, store_id)
    # Copy-then-assign: SQLAlchemy does not track in-place mutation of a JSONB
    # dict, so mutating `store.settings` directly would not persist.
    current = dict(store.settings or {})
    whatsapp = dict(current.get("whatsapp") or {})
    whatsapp["provider"] = body.provider
    current["whatsapp"] = whatsapp
    store.settings = current
    await db.commit()

    return SuccessResponse(
        data={"store_id": str(store_id), "provider": body.provider},
        message=f"WhatsApp provider set to {body.provider}.",
    )


@router.post("/{store_id}/pair", operation_id="admin_pair_gowa_device")
async def pair_device(
    store_id: UUID,
    body: PairRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[UserModel, Depends(require_admin)],
) -> SuccessResponse[PairResponse]:
    """Create a device and return the pairing code (or QR) for the merchant.

    Short-lived by nature — generate this with the merchant on the phone.
    """
    if not body.acknowledge_risk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Pairing requires acknowledge_risk=true: the merchant's own "
                "WhatsApp number is what gets banned if WhatsApp objects."
            ),
        )

    store = await _load_store(db, store_id)
    repo = WhatsAppGowaDeviceRepository(db)

    created = await _gowa(
        "POST", "/devices", json={"name": f"numu-{store.subdomain or store_id}"}
    )
    device_id = str((created.get("results") or {}).get("id") or "")
    if not device_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GOWA did not return a device id.",
        )

    # Record BEFORE handing out the code: if the merchant links immediately, the
    # inbound webhook must already be able to resolve the device to this store.
    await repo.create_pending(
        tenant_id=store.tenant_id,
        store_id=store_id,
        device_id=device_id,
        acknowledged_by=admin.id,
    )
    await db.commit()

    digits = "".join(ch for ch in body.phone if ch.isdigit())
    if body.method == "code":
        result = (
            await _gowa(
                "GET", f"/app/login-with-code?phone={digits}", device_id=device_id
            )
        ).get("results") or {}
        return SuccessResponse(
            data=PairResponse(
                device_id=device_id,
                method="code",
                pair_code=result.get("pair_code"),
            ),
            message="Read this code to the merchant now — it expires quickly.",
        )

    result = (await _gowa("GET", "/app/login", device_id=device_id)).get(
        "results"
    ) or {}
    qr_link = str(result.get("qr_link") or "")
    return SuccessResponse(
        data=PairResponse(
            device_id=device_id,
            method="qr",
            qr_data_uri=await _fetch_qr(qr_link),
            expires_in_seconds=result.get("qr_duration"),
        ),
        message="Show this QR to the merchant now — it expires quickly.",
    )


async def _fetch_qr(qr_link: str) -> str | None:
    """Inline GOWA's QR PNG as a data URI.

    GOWA builds the link against ``127.0.0.1`` and its port is loopback-bound
    and firewalled to this API, so the browser cannot load it. Only the PATH is
    reusable; the host is replaced with the configured base URL.
    """
    if not qr_link:
        return None
    path = qr_link.split("/", 3)[-1] if "//" in qr_link else qr_link.lstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{_base_url()}/{path}", auth=_auth())
        if response.status_code >= 400:
            return None
    except httpx.HTTPError:
        return None
    encoded = base64.b64encode(response.content).decode()
    return f"data:image/png;base64,{encoded}"


@router.get("/{store_id}/status", operation_id="admin_gowa_device_status")
async def device_status(
    store_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[DeviceStatus]:
    """Live pairing state, so an operator can watch it connect."""
    from src.infrastructure.external_services.whatsapp import resolve_provider_name

    store = await _load_store(db, store_id)
    provider = resolve_provider_name(store.settings)
    device = await WhatsAppGowaDeviceRepository(db).get_active_for_store(store_id)
    if not device:
        return SuccessResponse(data=DeviceStatus(provider=provider, paired=False))

    # Best-effort live probe. A GOWA outage must not hide the stored state,
    # which is what tells the operator whether the merchant ever paired.
    is_connected = is_logged_in = None
    try:
        live = (await _gowa("GET", f"/devices/{device.device_id}/status")).get(
            "results"
        ) or {}
        is_connected = live.get("is_connected")
        is_logged_in = live.get("is_logged_in")
    except HTTPException:
        pass

    return SuccessResponse(
        data=DeviceStatus(
            provider=provider,
            paired=True,
            device_id=device.device_id,
            phone=device.phone,
            status=device.status,
            is_connected=is_connected,
            is_logged_in=is_logged_in,
            last_seen_at=device.last_seen_at.isoformat()
            if device.last_seen_at
            else None,
            last_error=device.last_error,
        )
    )


@router.post("/{store_id}/unpair", operation_id="admin_unpair_gowa_device")
async def unpair_device(
    store_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[dict]:
    """Unlink the merchant's number and retire the device row.

    The GOWA-side delete is best-effort: the store must stop sending through a
    device we no longer consider theirs even if GOWA is unreachable, so the
    local deactivation always happens.
    """
    device = await WhatsAppGowaDeviceRepository(db).get_active_for_store(store_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This store has no paired GOWA device.",
        )
    try:
        await _gowa("DELETE", f"/devices/{device.device_id}")
    except HTTPException:
        pass

    await WhatsAppGowaDeviceRepository(db).deactivate_for_store(store_id)
    await db.commit()
    return SuccessResponse(data={"store_id": str(store_id)}, message="Device unpaired.")
