"""Meta Custom Audiences hub-facing routes.

Surfaces ``MetaCustomAudienceService`` to the merchant hub. The service
itself was shipped earlier (Wave 4 Phase 22) but had no API route, so
the hub couldn't trigger a sync without dropping into the DB shell.

v1 supports the 3 prebuilt segments (``high_ltv`` / ``cart_abandoners``
/ ``lapsed``) — saved CustomerSegments will land when feature 003's
segment table ships.

Sync flow per call:
  1. Read Meta config from ``store.settings.tracking.meta``.
  2. Decrypt CAPI access token from ``service_credentials``.
  3. Look up the cached ``custom_audiences[segment_key].audience_id``
     on store settings; create the audience on Meta if not present.
  4. Build the hashed-PII member list via ``build_segment``.
  5. Push the members to the audience via ``push_to_meta``.
  6. Write the audience_id + last_synced_at + member_count back to
     ``store.settings.tracking.meta.custom_audiences[segment_key]``
     so the next call updates instead of recreating.

Synchronous v1 — fine while audiences are <50k members (Meta's API
absorbs the whole batch in one POST). Async Celery dispatch is the
follow-up once segments grow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified as _flag_modified

from src.api.dependencies import get_current_store, verify_store_ownership
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService, EventType
from src.application.services.meta_custom_audience_service import (
    MetaCustomAudienceService,
)
from src.core.entities.store import Store
from src.infrastructure.database.connection import AsyncSessionLocal, get_db

router = APIRouter(
    prefix="/{store_id}/marketing/audiences",
    tags=["Marketing Audiences"],
    dependencies=[Depends(verify_store_ownership)],
)


# ── Schemas ──────────────────────────────────────────────────────


SegmentKey = Literal["high_ltv", "cart_abandoners", "lapsed"]

# Static metadata for the prebuilt segments so the hub can render the
# list without a separate "segments catalog" call.
_PREBUILT_SEGMENTS: dict[str, dict[str, str]] = {
    "high_ltv": {
        "label_en": "High LTV customers",
        "label_ar": "العملاء الأكثر إنفاقاً",
        "description_en": "Customers who spent EGP 5,000+ lifetime.",
        "description_ar": "العملاء الذين أنفقوا أكثر من 5,000 جنيه.",
    },
    "cart_abandoners": {
        "label_en": "Cart abandoners (30 days)",
        "label_ar": "تركوا السلة (30 يوم)",
        "description_en": "Added to cart in the last 30 days, no purchase yet.",
        "description_ar": "أضافوا للسلة في الـ30 يوم الماضية ولم يشتروا.",
    },
    "lapsed": {
        "label_en": "Lapsed customers (90 days)",
        "label_ar": "عملاء غير نشطين (90 يوم)",
        "description_en": "Bought once but inactive for 90+ days.",
        "description_ar": "اشتروا مرة واحدة وغير نشطين لأكثر من 90 يوم.",
    },
}


class AudienceStatus(BaseModel):
    segment_key: SegmentKey
    label_en: str
    label_ar: str
    description_en: str
    description_ar: str
    # None when never synced. Populated from
    # ``store.settings.tracking.meta.custom_audiences[segment_key]`` cache.
    meta_audience_id: str | None = None
    last_synced_at: str | None = None
    member_count: int | None = None


class ListAudiencesResponse(BaseModel):
    audiences: list[AudienceStatus]
    # ``False`` when the merchant hasn't connected Meta — hub can
    # render the "Connect Meta" empty state without inferring from
    # specific 4xx codes.
    meta_connected: bool


class SyncAudienceResponse(BaseModel):
    segment_key: SegmentKey
    meta_audience_id: str
    member_count: int
    synced_at: str


# ── Helpers ──────────────────────────────────────────────────────


def _meta_tracking_cfg(store: Store) -> dict:
    return ((store.settings or {}).get("tracking") or {}).get("meta") or {}


def _custom_audiences_cache(store: Store) -> dict:
    """Local mirror of which segments are synced + their meta_audience_id."""
    return _meta_tracking_cfg(store).get("custom_audiences") or {}


async def _get_capi_token(db: AsyncSession, tenant_id: UUID) -> str | None:
    """Decrypt the on-file CAPI access token, or None if not set."""
    from sqlalchemy import select as _select

    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    q = (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.META_CAPI)
        .where(ServiceCredential.is_active.is_(True))
    )
    cred = (await db.execute(q)).scalar_one_or_none()
    if cred is None:
        return None
    try:
        sm = get_secrets_manager()
        decrypted = await sm.decrypt(cred.credentials_encrypted, cred.encryption_key_id)
        return (decrypted or {}).get("access_token")
    except Exception:
        return None


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[ListAudiencesResponse],
    summary="List Custom Audience sync status for prebuilt segments",
    operation_id="list_meta_custom_audiences",
)
async def list_audiences(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Surface what's been synced so the hub can render a Sync/Resync row."""
    meta_cfg = _meta_tracking_cfg(store)
    token = await _get_capi_token(db, store.tenant_id)
    meta_connected = bool(token and meta_cfg.get("ad_account_id"))

    cache = _custom_audiences_cache(store)
    audiences = [
        AudienceStatus(
            segment_key=key,
            label_en=meta["label_en"],
            label_ar=meta["label_ar"],
            description_en=meta["description_en"],
            description_ar=meta["description_ar"],
            meta_audience_id=(cache.get(key) or {}).get("audience_id"),
            last_synced_at=(cache.get(key) or {}).get("last_synced_at"),
            member_count=(cache.get(key) or {}).get("member_count"),
        )
        for key, meta in _PREBUILT_SEGMENTS.items()
    ]

    return SuccessResponse(
        data=ListAudiencesResponse(audiences=audiences, meta_connected=meta_connected),
        message="Audiences listed",
    )


@router.post(
    "/{segment_key}/sync",
    response_model=SuccessResponse[SyncAudienceResponse],
    status_code=status.HTTP_200_OK,
    summary="Build + push a Custom Audience to Meta",
    operation_id="sync_meta_custom_audience",
)
async def sync_audience(
    segment_key: SegmentKey,
    store: Annotated[Store, Depends(get_current_store)],
):
    """End-to-end: build segment → create-if-needed → push members.

    Gated on:
      - Meta config has ``ad_account_id`` (set via the OAuth picker).
      - An active CAPI access_token is on file.
      - Token has ``ads_management`` scope (Meta will 4xx otherwise —
        we surface the error verbatim so the hub can re-prompt OAuth
        with the expanded scope).
    """
    meta_cfg = _meta_tracking_cfg(store)
    ad_account_id = meta_cfg.get("ad_account_id")
    if not ad_account_id:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=(
                "Meta ad account is not connected. Complete the Meta "
                "OAuth flow first via Settings → Integrations → Meta."
            ),
        )

    async with AsyncSessionLocal() as db:
        token = await _get_capi_token(db, store.tenant_id)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Meta access token is missing or expired.",
            )

        svc = MetaCustomAudienceService(db)

        # Build the hashed member list. Empty segments are fine — push
        # still works but contributes zero matches. Surface the count
        # so the hub can warn about the 100-member Lookalike threshold.
        segment = await svc.build_segment(store_id=store.id, segment_key=segment_key)

        # Reuse the cached audience_id when one exists; create otherwise.
        cache = _custom_audiences_cache(store)
        cached = cache.get(segment_key) or {}
        audience_id = cached.get("audience_id")
        if not audience_id:
            audience_name = (
                f"NUMU · {store.name or store.subdomain or 'store'} · "
                f"{_PREBUILT_SEGMENTS[segment_key]['label_en']}"
            )
            audience_id = await svc.create_audience(
                ad_account_id=ad_account_id,
                access_token=token,
                name=audience_name,
                description=_PREBUILT_SEGMENTS[segment_key]["description_en"],
            )
            if not audience_id:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Meta refused to create the Custom Audience. "
                        "Check that the connected token has "
                        "ads_management scope and the ad account "
                        "exists. See server logs for Meta's response."
                    ),
                )

        # Push members. Failures here aren't fatal at v1 — the audience
        # exists on Meta with zero members and the merchant can retry
        # by clicking Resync. Returning a 502 communicates the issue.
        pushed = await svc.push_to_meta(
            segment=segment,
            audience_id=audience_id,
            access_token=token,
        )
        if not pushed:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Meta rejected the member upload. The Custom "
                    "Audience exists but is empty. Retry with Resync."
                ),
            )

        synced_at = datetime.now(UTC).isoformat()

        # Persist the cache. Mutate-in-place + flag_modified so SQLAlchemy
        # actually emits an UPDATE on the JSONB column.
        settings_dict: dict = store.settings or {}
        tracking = settings_dict.get("tracking") or {}
        meta_cfg_dict = tracking.get("meta") or {}
        ca_cache = meta_cfg_dict.get("custom_audiences") or {}
        ca_cache[segment_key] = {
            "audience_id": audience_id,
            "last_synced_at": synced_at,
            "member_count": len(segment.members),
        }
        meta_cfg_dict["custom_audiences"] = ca_cache
        tracking["meta"] = meta_cfg_dict
        settings_dict["tracking"] = tracking
        store.settings = settings_dict
        try:
            _flag_modified(store, "settings")
        except Exception:
            pass

        # Audit trail. Summary-only — per-recipient hashes are not logged
        # (would be unmanageable at scale and the privacy-sensitive
        # event is "this segment was synced" not "this hash uploaded").
        try:
            await AuditService(db).log(
                event_type=EventType.ADMIN_CONFIG_CHANGE,
                action="meta_audience_sync",
                resource_type="meta_custom_audience",
                resource_id=audience_id,
                store_id=store.id,
                tenant_id=store.tenant_id,
                new_value={
                    "segment_key": segment_key,
                    "member_count": len(segment.members),
                    "ad_account_id": ad_account_id,
                },
            )
        except Exception:
            pass  # Audit log failure must not break the sync response.

        await db.commit()

    return SuccessResponse(
        data=SyncAudienceResponse(
            segment_key=segment_key,
            meta_audience_id=audience_id,
            member_count=len(segment.members),
            synced_at=synced_at,
        ),
        message="Custom Audience synced",
    )
