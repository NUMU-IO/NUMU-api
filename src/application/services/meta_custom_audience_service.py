"""Wave 3 Phase 24 — Meta Custom Audiences push (Shopify Audiences equivalent).

Pushes hashed customer segments to Meta as Custom Audiences via the
Marketing API. Merchants then target / exclude these audiences in
their Meta ad campaigns without ever uploading a CSV manually.

Three prebuilt segments ship in v1:

  * ``high_ltv``         — customers with lifetime spend ≥ X EGP
  * ``cart_abandoners``  — customers with cart_activity within 30 days
                           but no purchase in that window
  * ``lapsed``           — customers with last purchase > 90 days ago

The segment-builder logic is pure Postgres SQL — no Meta API
required. The Marketing API push is the gated half (Phase 17 OAuth).

**Gates:**
  * Segment builder works locally now (no external API).
  * Marketing API push is gated on Phase 17 OAuth landing.
  * Phone-based audiences additionally require Meta-side approval
    of the merchant's Business for "Custom Audiences from a Customer
    File" (separate from app-level Review — merchant-by-merchant).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.hashing import (
    _h,
    _normalize_mena_phone,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class HashedAudienceMember:
    """One member of a Custom Audience — hashed PII only.

    Meta's Marketing API ``customaudiences/users`` endpoint accepts
    arrays of these. We hash everything here so the rest of the
    pipeline never sees plaintext PII.
    """

    email_sha256: str | None
    phone_sha256: str | None
    external_id_sha256: str | None  # NUMU customer UUID hashed


@dataclass(frozen=True)
class AudienceSegment:
    """A built segment ready to push to Meta."""

    segment_key: str  # "high_ltv" / "cart_abandoners" / "lapsed"
    members: list[HashedAudienceMember]
    built_at: datetime


def _hash_customer_row(row: dict[str, Any]) -> HashedAudienceMember:
    """Build a HashedAudienceMember from a customers-table row.

    Phone goes through the MENA normalizer (Phase 14) before hashing
    so the same customer matches whether they entered +20 or 01...
    """
    email = (row.get("email") or "").strip().lower() or None
    phone = (row.get("phone") or "").strip()
    customer_id = str(row.get("id") or "") or None
    return HashedAudienceMember(
        email_sha256=_h(email) if email else None,
        phone_sha256=_h(_normalize_mena_phone(phone)) if phone else None,
        external_id_sha256=_h(customer_id) if customer_id else None,
    )


class MetaCustomAudienceService:
    """Builds + pushes Meta Custom Audiences for a store.

    Stateless. Construct with a session, call ``build_segment``,
    optionally call ``push_to_meta`` if OAuth is connected.
    """

    HIGH_LTV_THRESHOLD_CENTS = 5_000_00  # EGP 5,000 default
    LAPSED_DAYS = 90
    CART_ABANDON_DAYS = 30

    def __init__(self, session: Any) -> None:
        self._session = session

    async def build_segment(
        self, *, store_id: UUID, segment_key: str
    ) -> AudienceSegment:
        """Build the hashed-PII member list for one segment.

        Pure Postgres — no Meta API. Safe to call any time, including
        before Phase 17 OAuth lands.
        """
        if segment_key == "high_ltv":
            members = await self._build_high_ltv(store_id)
        elif segment_key == "cart_abandoners":
            members = await self._build_cart_abandoners(store_id)
        elif segment_key == "lapsed":
            members = await self._build_lapsed(store_id)
        else:
            raise ValueError(f"Unknown segment_key: {segment_key}")

        logger.info(
            "custom_audience_segment_built",
            extra={
                "store_id": str(store_id),
                "segment_key": segment_key,
                "member_count": len(members),
            },
        )
        return AudienceSegment(
            segment_key=segment_key,
            members=members,
            built_at=datetime.now(UTC),
        )

    async def _build_high_ltv(self, store_id: UUID) -> list[HashedAudienceMember]:
        from sqlalchemy import text

        rows = await self._session.execute(
            text(
                """
                SELECT c.id, c.email, c.phone
                FROM public.customers c
                WHERE c.store_id = :sid
                  AND c.total_spent_cents >= :threshold
                """
            ),
            {"sid": str(store_id), "threshold": self.HIGH_LTV_THRESHOLD_CENTS},
        )
        return [_hash_customer_row(dict(r._mapping)) for r in rows.fetchall()]

    async def _build_cart_abandoners(
        self, store_id: UUID
    ) -> list[HashedAudienceMember]:
        from sqlalchemy import text

        cutoff = datetime.now(UTC) - timedelta(days=self.CART_ABANDON_DAYS)
        # Customers whose latest cart_activity is within the window
        # AND whose latest order (if any) is OLDER than the cart event.
        rows = await self._session.execute(
            text(
                """
                SELECT DISTINCT c.id, c.email, c.phone
                FROM public.customers c
                JOIN public.cart_activity ca
                  ON ca.customer_id = c.id
                 AND ca.store_id = c.store_id
                WHERE c.store_id = :sid
                  AND ca.created_at >= :cutoff
                  AND NOT EXISTS (
                      SELECT 1 FROM public.orders o
                      WHERE o.customer_id = c.id
                        AND o.store_id = c.store_id
                        AND o.created_at >= ca.created_at
                  )
                """
            ),
            {"sid": str(store_id), "cutoff": cutoff},
        )
        return [_hash_customer_row(dict(r._mapping)) for r in rows.fetchall()]

    async def _build_lapsed(self, store_id: UUID) -> list[HashedAudienceMember]:
        from sqlalchemy import text

        cutoff = datetime.now(UTC) - timedelta(days=self.LAPSED_DAYS)
        rows = await self._session.execute(
            text(
                """
                SELECT c.id, c.email, c.phone
                FROM public.customers c
                WHERE c.store_id = :sid
                  AND EXISTS (
                      SELECT 1 FROM public.orders o
                      WHERE o.customer_id = c.id
                        AND o.store_id = c.store_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM public.orders o
                      WHERE o.customer_id = c.id
                        AND o.store_id = c.store_id
                        AND o.created_at >= :cutoff
                  )
                """
            ),
            {"sid": str(store_id), "cutoff": cutoff},
        )
        return [_hash_customer_row(dict(r._mapping)) for r in rows.fetchall()]

    async def push_to_meta(
        self,
        *,
        segment: AudienceSegment,
        audience_id: str,
        access_token: str,
    ) -> bool:
        """Push the segment to a pre-existing Meta Custom Audience.

        **Gated on Phase 17 OAuth** — caller passes an access_token
        obtained from the OAuth flow.

        Meta's ``customaudiences/{id}/users`` endpoint accepts a
        ``payload`` with ``schema`` listing the hash fields and ``data``
        as a 2D array of hashed values aligned to the schema.

        Returns True on success, False on Marketing-API error
        (caller logs + can retry via Celery).
        """
        import httpx

        from src.config import settings

        api_version = getattr(settings, "meta_graph_api_version", "v21.0")
        url = f"https://graph.facebook.com/{api_version}/{audience_id}/users"

        # Schema + data rows aligned. Each member contributes one row
        # with email/phone/external_id (Meta accepts mixed-completeness).
        schema = ["EMAIL_SHA256", "PHONE_SHA256", "EXTERN_ID"]
        data = [
            [
                m.email_sha256 or "",
                m.phone_sha256 or "",
                m.external_id_sha256 or "",
            ]
            for m in segment.members
        ]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "payload": {"schema": schema, "data": data},
                        "access_token": access_token,
                    },
                )
            if resp.status_code >= 400:
                logger.warning(
                    "custom_audience_push_failed",
                    extra={
                        "audience_id": audience_id,
                        "status": resp.status_code,
                        "body": resp.text[:300],
                    },
                )
                return False
            logger.info(
                "custom_audience_pushed",
                extra={
                    "audience_id": audience_id,
                    "segment_key": segment.segment_key,
                    "member_count": len(segment.members),
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001 — fail-open for caller retry
            logger.warning(
                "custom_audience_push_exception",
                extra={
                    "audience_id": audience_id,
                    "error": str(exc),
                },
            )
            return False

    async def create_audience(
        self,
        *,
        ad_account_id: str,
        access_token: str,
        name: str,
        description: str | None = None,
    ) -> str | None:
        """Create an empty Custom Audience on Meta, return its id.

        Required before ``push_to_meta`` can deposit hashed members —
        Meta separates audience creation from member upload so the
        same audience can be refreshed without recreating.

        Returns the new ``customaudiences/{id}`` value on success,
        ``None`` on Marketing-API error (caller logs + retries).
        ``customer_file_source=USER_PROVIDED_ONLY`` is required for
        audiences that get populated via the hashed-PII upload path.
        """
        import httpx

        from src.config import settings as _app_settings

        api_version = getattr(_app_settings, "meta_graph_api_version", "v21.0")
        # Strip any leading "act_" the caller might pass — Meta accepts
        # the bare ad account id on this path and rejects double prefixes.
        clean_act_id = ad_account_id.removeprefix("act_")
        url = (
            f"https://graph.facebook.com/{api_version}/act_{clean_act_id}/"
            f"customaudiences"
        )

        payload: dict[str, Any] = {
            "name": name[:128],  # Meta caps name at 128 chars
            "subtype": "CUSTOM",
            "customer_file_source": "USER_PROVIDED_ONLY",
            "access_token": access_token,
        }
        if description:
            payload["description"] = description[:255]

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, data=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "custom_audience_create_failed",
                    extra={
                        "ad_account_id": clean_act_id,
                        "name": name,
                        "status": resp.status_code,
                        "body": resp.text[:300],
                    },
                )
                return None
            body = resp.json() if resp.content else {}
            return str(body.get("id")) if body.get("id") else None
        except Exception as exc:  # noqa: BLE001 — fail-open for caller retry
            logger.warning(
                "custom_audience_create_exception",
                extra={
                    "ad_account_id": clean_act_id,
                    "name": name,
                    "error": str(exc),
                },
            )
            return None
