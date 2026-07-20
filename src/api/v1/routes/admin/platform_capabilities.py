"""Admin control plane for the platform capability registry (ADR-0 / ADR-6).

This is the operator surface for what may extend NUMU: which capabilities
exist, who may hold each one, and the kill switch that takes one out of
service everywhere at once.

All routes require SUPER_ADMIN. Suspending a capability is a platform-wide
action, so it carries the same 2FA step-up the marketplace review endpoint
uses — a stale admin session should not be able to disable a capability that
live storefronts depend on, nor re-enable one that was suspended for cause.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin, require_admin_2fa
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.platform_capability import (
    CapabilityKind,
    DataClassification,
    ExtensionTier,
    LifecycleState,
    PlatformCapability,
    UnavailableBehavior,
    evaluate_manifest,
)
from src.infrastructure.database.models.tenant.platform_capability import (
    PlatformCapabilityModel,
)

logger = logging.getLogger(__name__)

# Prefix is supplied by the admin package router (see admin/__init__.py).
router = APIRouter(dependencies=[Depends(require_admin)])


# ── Schemas ──────────────────────────────────────────────────────────────────


class CapabilityItem(BaseModel):
    id: str
    slug: str
    kind: str
    owner: str
    description: str | None = None
    lifecycle_state: str
    data_classification: str
    min_tier: str
    #: The tier actually required once the data classification's floor is
    #: applied. Surfaced separately from `min_tier` so an operator can see when
    #: a row's stored value is being overridden upward rather than silently
    #: wondering why a grant failed.
    effective_min_tier: str
    unavailable_behavior: str
    active_version: str | None = None
    supported_versions: list[str] = Field(default_factory=list)
    placements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    grantable: bool


class CapabilityListResponse(BaseModel):
    capabilities: list[CapabilityItem]
    total: int


class CapabilityUpsert(BaseModel):
    slug: str = Field(max_length=128)
    kind: CapabilityKind
    owner: str = Field(max_length=128)
    description: str | None = None
    # Deliberately NOT accepted on create. Allowing it would let a caller
    # create a capability straight into `ga` (live, no review) or `suspended`
    # without the 2FA step-up the lifecycle endpoint exists to enforce —
    # bypassing the gate by taking a different door. New capabilities always
    # start as draft, which grants nothing, and move via /lifecycle.
    data_classification: DataClassification = DataClassification.TENANT_SCOPED
    min_tier: ExtensionTier = ExtensionTier.PARTNER
    unavailable_behavior: UnavailableBehavior = UnavailableBehavior.FAIL_OPEN
    active_version: str | None = None
    supported_versions: list[str] = Field(default_factory=list)
    placements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class CapabilityPatch(BaseModel):
    description: str | None = None
    lifecycle_state: LifecycleState | None = None
    data_classification: DataClassification | None = None
    min_tier: ExtensionTier | None = None
    unavailable_behavior: UnavailableBehavior | None = None
    active_version: str | None = None
    supported_versions: list[str] | None = None
    placements: list[str] | None = None
    dependencies: list[str] | None = None


class LifecycleChange(BaseModel):
    lifecycle_state: LifecycleState
    #: Free text recorded in the response for the operator's own audit trail.
    reason: str | None = Field(default=None, max_length=500)


class GrantCheckRequest(BaseModel):
    """Dry-run a manifest against the registry."""

    tier: ExtensionTier
    requested_slugs: list[str]


class GrantDenialItem(BaseModel):
    """Why one capability was refused. The reason is the point of /check, so
    it gets a real schema rather than an untyped dict that generates `any`."""

    capability_slug: str
    reason: str | None = None


class GrantCheckResponse(BaseModel):
    tier: str
    ok: bool
    granted: list[str]
    denied: list[GrantDenialItem]


# ── Mapping ──────────────────────────────────────────────────────────────────


def _to_entity(row: PlatformCapabilityModel) -> PlatformCapability:
    return PlatformCapability(
        id=row.id,
        slug=row.slug,
        kind=CapabilityKind(row.kind),
        owner=row.owner,
        description=row.description,
        lifecycle_state=LifecycleState(row.lifecycle_state),
        data_classification=DataClassification(row.data_classification),
        min_tier=ExtensionTier(row.min_tier),
        unavailable_behavior=UnavailableBehavior(row.unavailable_behavior),
        active_version=row.active_version,
        supported_versions=list(row.supported_versions or []),
        placements=list(row.placements or []),
        dependencies=list(row.dependencies or []),
        eligibility=dict(row.eligibility or {}),
    )


def _to_item(row: PlatformCapabilityModel) -> CapabilityItem:
    entity = _to_entity(row)
    # "Grantable at all" — independent of any particular requester's tier.
    grantable = entity.evaluate(ExtensionTier.FIRST_PARTY).granted
    return CapabilityItem(
        id=str(row.id),
        slug=row.slug,
        kind=row.kind,
        owner=row.owner,
        description=row.description,
        lifecycle_state=row.lifecycle_state,
        data_classification=row.data_classification,
        min_tier=row.min_tier,
        effective_min_tier=entity.effective_min_tier.value,
        unavailable_behavior=row.unavailable_behavior,
        active_version=row.active_version,
        supported_versions=list(row.supported_versions or []),
        placements=list(row.placements or []),
        dependencies=list(row.dependencies or []),
        grantable=grantable,
    )


async def _get_row(session: AsyncSession, slug: str) -> PlatformCapabilityModel:
    row = (
        await session.execute(
            select(PlatformCapabilityModel).where(PlatformCapabilityModel.slug == slug)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"capability '{slug}' not found",
        )
    return row


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=SuccessResponse[CapabilityListResponse])
async def list_capabilities(
    session: Annotated[AsyncSession, Depends(get_db)],
    kind: str | None = Query(default=None),
    lifecycle_state: str | None = Query(default=None),
):
    """Every registered capability, newest-registered last."""
    stmt = select(PlatformCapabilityModel).order_by(PlatformCapabilityModel.slug)
    if kind:
        stmt = stmt.where(PlatformCapabilityModel.kind == kind)
    if lifecycle_state:
        stmt = stmt.where(PlatformCapabilityModel.lifecycle_state == lifecycle_state)
    rows = (await session.execute(stmt)).scalars().all()
    items = [_to_item(r) for r in rows]
    return SuccessResponse(
        data=CapabilityListResponse(capabilities=items, total=len(items))
    )


@router.get("/vocabulary", response_model=SuccessResponse[dict])
async def get_vocabulary():
    """The enum vocabulary, so the admin UI never hardcodes it.

    Hardcoding these in the client is exactly how the dynamic-source enum ended
    up triplicated across hub, SDK and host.
    """
    return SuccessResponse(
        data={
            "kinds": [k.value for k in CapabilityKind],
            "lifecycle_states": [s.value for s in LifecycleState],
            "tiers": [t.value for t in ExtensionTier],
            "data_classifications": [d.value for d in DataClassification],
            "unavailable_behaviors": [u.value for u in UnavailableBehavior],
        }
    )


@router.post(
    "",
    response_model=SuccessResponse[CapabilityItem],
    status_code=status.HTTP_201_CREATED,
)
async def create_capability(
    body: CapabilityUpsert,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    existing = (
        await session.execute(
            select(PlatformCapabilityModel).where(
                PlatformCapabilityModel.slug == body.slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"capability '{body.slug}' already exists",
        )

    row = PlatformCapabilityModel(
        slug=body.slug,
        kind=body.kind.value,
        owner=body.owner,
        description=body.description,
        # Always draft — see CapabilityUpsert. Promotion is a 2FA-gated action.
        lifecycle_state=LifecycleState.DRAFT.value,
        data_classification=body.data_classification.value,
        min_tier=body.min_tier.value,
        unavailable_behavior=body.unavailable_behavior.value,
        active_version=body.active_version,
        supported_versions=body.supported_versions,
        placements=body.placements,
        dependencies=body.dependencies,
        eligibility={},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SuccessResponse(data=_to_item(row))


@router.patch("/{slug}", response_model=SuccessResponse[CapabilityItem])
async def update_capability(
    slug: str,
    body: CapabilityPatch,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Edit a capability's metadata.

    Lifecycle changes go through the dedicated endpoint instead — suspending a
    capability is a platform-wide action and gets a 2FA step-up, which a
    general-purpose PATCH would quietly bypass.
    """
    row = await _get_row(session, slug)
    # exclude_unset only: an omitted field is left alone, but an explicit null
    # must actually clear the column. Adding exclude_none here would make
    # `{"description": null}` a silent no-op, leaving no way to remove a
    # description or unset an active version through the API at all.
    patch = body.model_dump(exclude_unset=True)
    patch.pop("lifecycle_state", None)

    for key, value in patch.items():
        setattr(row, key, value.value if hasattr(value, "value") else value)

    await session.commit()
    await session.refresh(row)
    return SuccessResponse(data=_to_item(row))


@router.post(
    "/{slug}/lifecycle",
    response_model=SuccessResponse[CapabilityItem],
    # Step-up gate: suspending a capability disables it for every store at
    # once, and un-suspending re-enables code that was stopped for a reason.
    dependencies=[Depends(require_admin_2fa(max_age_seconds=300))],
)
async def set_lifecycle(
    slug: str,
    body: LifecycleChange,
    session: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Move a capability through its lifecycle — including the kill switch.

    Suspension takes effect at grant time, so an extension holding this
    capability stops being granted it immediately rather than only on its next
    render.
    """
    row = await _get_row(session, slug)
    previous = row.lifecycle_state
    row.lifecycle_state = body.lifecycle_state.value
    await session.commit()
    await session.refresh(row)

    # The kill switch has to leave a trail. Who suspended what, when, and why
    # is the first question asked when a capability stops being granted, and
    # accepting `reason` without recording it anywhere would make the operator
    # type into a black hole.
    logger.warning(
        "platform_capability_lifecycle_changed",
        extra={
            "capability_slug": slug,
            "from_state": previous,
            "to_state": row.lifecycle_state,
            "admin_id": str(admin_id),
            "reason": body.reason,
            "grantable_now": _to_item(row).grantable,
        },
    )
    return SuccessResponse(data=_to_item(row))


@router.post("/check", response_model=SuccessResponse[GrantCheckResponse])
async def check_grant(
    body: GrantCheckRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Dry-run a manifest against the live registry.

    Lets an operator answer "would this extension be allowed, and if not
    exactly why" without installing anything.
    """
    rows = (await session.execute(select(PlatformCapabilityModel))).scalars().all()
    registry = {r.slug: _to_entity(r) for r in rows}
    grant = evaluate_manifest(
        tier=body.tier, requested_slugs=body.requested_slugs, registry=registry
    )
    return SuccessResponse(
        data=GrantCheckResponse(
            tier=grant.tier.value,
            ok=grant.ok,
            granted=grant.granted,
            denied=[
                GrantDenialItem(capability_slug=d.capability_slug, reason=d.reason)
                for d in grant.denied
            ],
        )
    )
