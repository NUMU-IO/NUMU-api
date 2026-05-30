"""Repository for marketplace theme operations."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession


def _user_passes_pct_gate(theme: Any, user_id: str) -> bool:
    """Deterministic percentage rollout gate.

    Hashes ``(user_id, theme_slug)`` to a stable 0-99 bucket. The user
    sees the theme iff ``bucket < flags.visible_to_pct``. Same user
    always lands on the same side — no flapping. Different users see
    different themes at different rollout %s.

    When ``visible_to_pct`` is absent or 0, returns True iff the theme
    is already gated visible (catalog_visible or in user_ids allowlist),
    which the SQL layer has already enforced.

    The internal allowlist (``visible_to_user_ids``) bypasses this gate
    entirely — internal users see everything regardless of rollout %.
    """
    flags = getattr(theme, "flags", None) or {}
    if not isinstance(flags, dict):
        return True

    allowlist = flags.get("visible_to_user_ids") or []
    if isinstance(allowlist, list) and user_id in allowlist:
        return True

    pct_raw = flags.get("visible_to_pct")
    if not isinstance(pct_raw, int) or pct_raw <= 0:
        # No percentage gate → respect SQL-side filter (catalog_visible
        # was already true to reach this code path).
        return True
    if pct_raw >= 100:
        return True

    slug = getattr(theme, "slug", "") or ""
    bucket_seed = f"{user_id}:{slug}".encode()
    digest = hashlib.sha256(bucket_seed).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < pct_raw


from src.core.entities.marketplace_theme import (
    MarketplacePurchaseStatus,
    MarketplaceTheme,
    MarketplaceThemeInstallation,
    MarketplaceThemePurchase,
    MarketplaceThemeReview,
    MarketplaceThemeStatus,
    MarketplaceThemeVersion,
    MarketplaceVersionStatus,
)
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeInstallationModel,
    MarketplaceThemeModel,
    MarketplaceThemePurchaseModel,
    MarketplaceThemeReviewModel,
    MarketplaceThemeVersionModel,
)


class MarketplaceRepository:
    """CRUD operations for marketplace themes, versions, and installations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── Mapping ───────────────────────────────────────────────────────────────

    def _theme_to_entity(self, m: MarketplaceThemeModel) -> MarketplaceTheme:
        # ``screenshots``/``highlights``/``feature_tags`` were added in
        # migration 20260527_020000. ``getattr(..., default)`` keeps the
        # mapper happy for any pre-migration test fixtures still using
        # an older ORM session — defensive but cheap.
        return MarketplaceTheme(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            developer_id=m.developer_id,
            name=m.name,
            slug=m.slug,
            description=m.description,
            short_description=m.short_description,
            price_cents=m.price_cents,
            currency=m.currency,
            status=MarketplaceThemeStatus(m.status),
            thumbnail_url=m.thumbnail_url,
            preview_url=m.preview_url,
            demo_store_url=m.demo_store_url,
            tags=copy.deepcopy(m.tags or []),
            category=m.category,
            supported_languages=copy.deepcopy(m.supported_languages or []),
            supported_features=copy.deepcopy(m.supported_features or {}),
            install_count=m.install_count,
            average_rating=m.average_rating,
            review_count=m.review_count,
            flags=copy.deepcopy(m.flags or {}),
            author_name=getattr(m, "author_name", None),
            author_url=getattr(m, "author_url", None),
            screenshots=copy.deepcopy(getattr(m, "screenshots", []) or []),
            highlights=copy.deepcopy(getattr(m, "highlights", []) or []),
            feature_tags=copy.deepcopy(getattr(m, "feature_tags", []) or []),
        )

    def _version_to_entity(
        self, m: MarketplaceThemeVersionModel
    ) -> MarketplaceThemeVersion:
        return MarketplaceThemeVersion(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.created_at,
            theme_id=m.theme_id,
            version_string=m.version_string,
            bundle_url=m.bundle_url,
            css_url=m.css_url,
            settings_schema=copy.deepcopy(m.settings_schema or {}),
            section_schemas=copy.deepcopy(m.section_schemas or {}),
            presets=copy.deepcopy(m.presets or {}),
            release_notes=m.release_notes,
            status=MarketplaceVersionStatus(m.status),
            build_log=m.build_log,
            size_bytes=m.size_bytes,
            checksum=m.checksum,
            source_zip_path=m.source_zip_path,
            review_notes=m.review_notes,
            reviewed_by=m.reviewed_by,
        )

    def _installation_to_entity(
        self, m: MarketplaceThemeInstallationModel
    ) -> MarketplaceThemeInstallation:
        return MarketplaceThemeInstallation(
            id=m.id,
            store_id=m.store_id,
            marketplace_theme_id=m.marketplace_theme_id,
            marketplace_version_id=m.marketplace_version_id,
            is_active=m.is_active,
            installed_at=m.installed_at,
            uninstalled_at=m.uninstalled_at,
        )

    # ── Theme CRUD ────────────────────────────────────────────────────────────

    async def create_theme(self, data: dict[str, Any]) -> MarketplaceTheme:
        model = MarketplaceThemeModel(**data)
        self._session.add(model)
        await self._session.flush()
        return self._theme_to_entity(model)

    async def get_theme_by_id(self, theme_id: UUID) -> MarketplaceTheme | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.id == theme_id)
        )
        m = result.scalar_one_or_none()
        return self._theme_to_entity(m) if m else None

    async def get_theme_by_slug(self, slug: str) -> MarketplaceTheme | None:
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.slug == slug)
        )
        m = result.scalar_one_or_none()
        return self._theme_to_entity(m) if m else None

    async def list_published(
        self,
        page: int = 1,
        per_page: int = 20,
        category: str | None = None,
        user_id: str | None = None,
    ) -> tuple[list[MarketplaceTheme], int]:
        """List themes visible in the public catalog.

        Visibility is gated by ``marketplace_themes.flags`` JSONB on
        top of ``status = 'published'``:

          - ``flags.catalog_visible = true`` (mandatory for public listing)
          - OR ``user_id`` is in ``flags.visible_to_user_ids`` (internal
            allowlist, bypasses catalog_visible)
          - AND a probabilistic visible_to_pct gate (deterministic hash
            of user_id mod 100 < flags.visible_to_pct). When pct is
            absent or 0, only catalog_visible governs visibility.

        Themes with ``flags = {}`` (the default for newly-published
        listings) are INVISIBLE until admin explicitly flips a flag.
        This is the production safety guarantee for sawsaw + rabbit:
        no theme appears in their dashboard until someone with
        super-admin rights consciously enables it.
        """
        base = select(MarketplaceThemeModel).where(
            MarketplaceThemeModel.status == MarketplaceThemeStatus.PUBLISHED.value
        )

        # Visibility filter — either catalog_visible OR user is on the
        # internal allowlist. SQL-side so we don't load 100s of rows
        # only to discard them in Python.
        #
        # `@>` needs the right-hand side typed as JSONB. We can't pass a
        # raw Python string — asyncpg wraps it in extra quotes when
        # encoding, so the JSON ends up double-encoded. Use a Postgres
        # literal cast (`'<json>'::jsonb`) via sa.text(); it stays
        # inline in the SQL and avoids parameter binding altogether.
        visibility_clauses = [
            MarketplaceThemeModel.flags.op("@>")(
                sa.text("'{\"catalog_visible\": true}'::jsonb")
            ),
        ]
        if user_id:
            # JSONB array element existence: `flags->'visible_to_user_ids' ? '<uid>'`.
            # The ``?`` operator name collides with SQLAlchemy's positional
            # parameter marker on some dialects, so we cast first then op.
            visibility_clauses.append(
                MarketplaceThemeModel.flags["visible_to_user_ids"].op("?")(user_id)
            )
        base = base.where(sa.or_(*visibility_clauses))

        if category:
            base = base.where(MarketplaceThemeModel.category == category)

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar() or 0

        q = (
            base
            .order_by(desc(MarketplaceThemeModel.install_count))
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self._session.execute(q)
        themes = [self._theme_to_entity(m) for m in result.scalars().all()]

        # Apply the percentage gate in Python — postgres-side stable
        # hashing isn't worth the complexity for a 100-row catalog. The
        # hash MUST be deterministic so a given (user, theme) pair
        # always lands on the same side of the cutoff (no flapping).
        if user_id:
            themes = [t for t in themes if _user_passes_pct_gate(t, user_id)]
        return themes, len(themes) if user_id else total

    async def list_by_developer(self, developer_id: UUID) -> list[MarketplaceTheme]:
        result = await self._session.execute(
            select(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.developer_id == developer_id)
            .order_by(desc(MarketplaceThemeModel.created_at))
        )
        return [self._theme_to_entity(m) for m in result.scalars().all()]

    async def list_all(self) -> list[MarketplaceTheme]:
        """Admin-only: every marketplace theme regardless of status or
        flags. Used by the admin flag-management UI which must see
        invisible themes to flip them visible."""
        result = await self._session.execute(
            select(MarketplaceThemeModel).order_by(
                desc(MarketplaceThemeModel.created_at)
            )
        )
        return [self._theme_to_entity(m) for m in result.scalars().all()]

    async def merge_flags(self, theme_id: UUID, patch: dict) -> MarketplaceTheme | None:
        """Shallow-merge ``patch`` into the row's ``flags`` JSONB. We
        do the merge in Python and write the full dict back so we get
        deterministic behavior for None-clearing semantics — Postgres'
        `jsonb_set` would also work but only one key at a time and
        doesn't remove keys without an extra `?` operator."""
        result = await self._session.execute(
            select(MarketplaceThemeModel).where(MarketplaceThemeModel.id == theme_id)
        )
        m = result.scalar_one_or_none()
        if m is None:
            return None
        current = dict(m.flags or {})
        current.update(patch)
        m.flags = current
        m.updated_at = datetime.now(UTC)
        await self._session.flush()
        return self._theme_to_entity(m)

    async def list_pending_review(self) -> list[MarketplaceThemeVersion]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(
                MarketplaceThemeVersionModel.status
                == MarketplaceVersionStatus.PENDING_REVIEW.value
            )
            .order_by(MarketplaceThemeVersionModel.created_at)
        )
        return [self._version_to_entity(m) for m in result.scalars().all()]

    async def update_theme(
        self, theme_id: UUID, fields: dict[str, Any]
    ) -> MarketplaceTheme | None:
        await self._session.execute(
            update(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.id == theme_id)
            .values(**fields)
        )
        await self._session.flush()
        return await self.get_theme_by_id(theme_id)

    async def increment_install_count(self, theme_id: UUID, delta: int = 1) -> None:
        await self._session.execute(
            update(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.id == theme_id)
            .values(install_count=MarketplaceThemeModel.install_count + delta)
        )
        await self._session.flush()

    # ── Version CRUD ──────────────────────────────────────────────────────────

    async def create_version(self, data: dict[str, Any]) -> MarketplaceThemeVersion:
        model = MarketplaceThemeVersionModel(**data)
        self._session.add(model)
        await self._session.flush()
        return self._version_to_entity(model)

    async def get_version_by_id(
        self, version_id: UUID
    ) -> MarketplaceThemeVersion | None:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel).where(
                MarketplaceThemeVersionModel.id == version_id
            )
        )
        m = result.scalar_one_or_none()
        return self._version_to_entity(m) if m else None

    async def get_latest_published_version(
        self, theme_id: UUID
    ) -> MarketplaceThemeVersion | None:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(
                MarketplaceThemeVersionModel.theme_id == theme_id,
                MarketplaceThemeVersionModel.status
                == MarketplaceVersionStatus.PUBLISHED.value,
            )
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
            .limit(1)
        )
        m = result.scalar_one_or_none()
        return self._version_to_entity(m) if m else None

    async def get_latest_installable_version(
        self, theme_id: UUID
    ) -> MarketplaceThemeVersion | None:
        """Latest version that has a bundle_url, regardless of status.

        Used by the developer self-install path: a developer who built a
        theme should be able to install it on their own stores even
        when the listing is `draft` and the version is `pending_review`
        or `approved` (admin hasn't published yet).

        Build/Failed/Rejected versions are still skipped — they have no
        usable bundle. We filter on `bundle_url IS NOT NULL` instead of
        whitelisting statuses so newly-introduced statuses (post-MVP)
        Just Work as long as they end up populating bundle_url.
        """
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(
                MarketplaceThemeVersionModel.theme_id == theme_id,
                MarketplaceThemeVersionModel.bundle_url.isnot(None),
            )
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
            .limit(1)
        )
        m = result.scalar_one_or_none()
        return self._version_to_entity(m) if m else None

    async def list_versions(self, theme_id: UUID) -> list[MarketplaceThemeVersion]:
        result = await self._session.execute(
            select(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.theme_id == theme_id)
            .order_by(desc(MarketplaceThemeVersionModel.created_at))
        )
        return [self._version_to_entity(m) for m in result.scalars().all()]

    async def update_version(
        self, version_id: UUID, fields: dict[str, Any]
    ) -> MarketplaceThemeVersion | None:
        await self._session.execute(
            update(MarketplaceThemeVersionModel)
            .where(MarketplaceThemeVersionModel.id == version_id)
            .values(**fields)
        )
        await self._session.flush()
        return await self.get_version_by_id(version_id)

    # ── Installation CRUD ─────────────────────────────────────────────────────

    async def get_installation(
        self, store_id: UUID, marketplace_theme_id: UUID
    ) -> MarketplaceThemeInstallation | None:
        result = await self._session.execute(
            select(MarketplaceThemeInstallationModel).where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
        )
        m = result.scalar_one_or_none()
        return self._installation_to_entity(m) if m else None

    async def list_installations(
        self, store_id: UUID, include_uninstalled: bool = False
    ) -> list[MarketplaceThemeInstallation]:
        q = select(MarketplaceThemeInstallationModel).where(
            MarketplaceThemeInstallationModel.store_id == store_id
        )
        if not include_uninstalled:
            q = q.where(MarketplaceThemeInstallationModel.uninstalled_at.is_(None))
        q = q.order_by(desc(MarketplaceThemeInstallationModel.installed_at))
        result = await self._session.execute(q)
        return [self._installation_to_entity(m) for m in result.scalars().all()]

    async def create_or_reactivate_installation(
        self,
        store_id: UUID,
        marketplace_theme_id: UUID,
        marketplace_version_id: UUID,
    ) -> MarketplaceThemeInstallation:
        """Insert a new install row, or reactivate an existing one if the
        store had uninstalled this theme before."""
        existing = await self._session.execute(
            select(MarketplaceThemeInstallationModel).where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
        )
        m = existing.scalar_one_or_none()
        now = datetime.now(UTC)
        if m:
            m.marketplace_version_id = marketplace_version_id
            m.uninstalled_at = None
            m.installed_at = now
        else:
            m = MarketplaceThemeInstallationModel(
                store_id=store_id,
                marketplace_theme_id=marketplace_theme_id,
                marketplace_version_id=marketplace_version_id,
                is_active=False,
                installed_at=now,
            )
            self._session.add(m)
        await self._session.flush()
        return self._installation_to_entity(m)

    async def set_active_installation(
        self, store_id: UUID, marketplace_theme_id: UUID | None
    ) -> None:
        """Make the given installation the active one (or none).

        Toggles `is_active` so only the named theme is active. Pass
        `marketplace_theme_id=None` to deactivate all marketplace
        installations for the store.
        """
        await self._session.execute(
            update(MarketplaceThemeInstallationModel)
            .where(MarketplaceThemeInstallationModel.store_id == store_id)
            .values(is_active=False)
        )
        if marketplace_theme_id is not None:
            await self._session.execute(
                update(MarketplaceThemeInstallationModel)
                .where(
                    MarketplaceThemeInstallationModel.store_id == store_id,
                    MarketplaceThemeInstallationModel.marketplace_theme_id
                    == marketplace_theme_id,
                )
                .values(is_active=True)
            )
        await self._session.flush()

    async def mark_uninstalled(
        self, store_id: UUID, marketplace_theme_id: UUID
    ) -> bool:
        result = await self._session.execute(
            update(MarketplaceThemeInstallationModel)
            .where(
                MarketplaceThemeInstallationModel.store_id == store_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
            )
            .values(uninstalled_at=datetime.now(UTC), is_active=False)
        )
        await self._session.flush()
        return (result.rowcount or 0) > 0

    # ── Purchases ─────────────────────────────────────────────────────────────

    def _purchase_to_entity(
        self, m: MarketplaceThemePurchaseModel
    ) -> MarketplaceThemePurchase:
        return MarketplaceThemePurchase(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            user_id=m.user_id,
            marketplace_theme_id=m.marketplace_theme_id,
            amount_cents=m.amount_cents,
            currency=m.currency,
            stripe_payment_intent_id=m.stripe_payment_intent_id,
            stripe_charge_id=m.stripe_charge_id,
            status=MarketplacePurchaseStatus(m.status),
            refunded_amount_cents=m.refunded_amount_cents,
            refund_reason=m.refund_reason,
            purchase_metadata=copy.deepcopy(m.purchase_metadata or {}),
        )

    async def create_purchase(
        self,
        *,
        user_id: UUID,
        marketplace_theme_id: UUID,
        amount_cents: int,
        currency: str,
        stripe_payment_intent_id: str | None,
        purchase_metadata: dict[str, Any] | None = None,
    ) -> MarketplaceThemePurchase:
        m = MarketplaceThemePurchaseModel(
            user_id=user_id,
            marketplace_theme_id=marketplace_theme_id,
            amount_cents=amount_cents,
            currency=currency,
            stripe_payment_intent_id=stripe_payment_intent_id,
            purchase_metadata=purchase_metadata or {},
        )
        self._session.add(m)
        await self._session.flush()
        return self._purchase_to_entity(m)

    async def get_purchase_by_intent(
        self, payment_intent_id: str
    ) -> MarketplaceThemePurchase | None:
        result = await self._session.execute(
            select(MarketplaceThemePurchaseModel).where(
                MarketplaceThemePurchaseModel.stripe_payment_intent_id
                == payment_intent_id
            )
        )
        m = result.scalar_one_or_none()
        return self._purchase_to_entity(m) if m else None

    async def get_purchase_by_id(
        self, purchase_id: UUID
    ) -> MarketplaceThemePurchase | None:
        result = await self._session.execute(
            select(MarketplaceThemePurchaseModel).where(
                MarketplaceThemePurchaseModel.id == purchase_id
            )
        )
        m = result.scalar_one_or_none()
        return self._purchase_to_entity(m) if m else None

    async def update_purchase(
        self,
        purchase_id: UUID,
        *,
        status: MarketplacePurchaseStatus | None = None,
        stripe_charge_id: str | None = None,
        refunded_amount_cents: int | None = None,
        refund_reason: str | None = None,
    ) -> None:
        # Build a values dict that only sets fields actually passed —
        # `None` here means "leave alone", not "clear to NULL".
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status.value
        if stripe_charge_id is not None:
            values["stripe_charge_id"] = stripe_charge_id
        if refunded_amount_cents is not None:
            values["refunded_amount_cents"] = refunded_amount_cents
        if refund_reason is not None:
            values["refund_reason"] = refund_reason
        if not values:
            return
        await self._session.execute(
            update(MarketplaceThemePurchaseModel)
            .where(MarketplaceThemePurchaseModel.id == purchase_id)
            .values(**values)
        )
        await self._session.flush()

    async def has_active_purchase(
        self, user_id: UUID, marketplace_theme_id: UUID
    ) -> bool:
        """Return True if the user has a succeeded, non-refunded purchase
        for this theme. Used by install_theme to gate paid-theme installs.

        We treat ``partially_refunded`` as "no longer active" — the
        refund process revokes future-install rights regardless of the
        refunded amount, since paid themes don't tier features by
        amount-paid.
        """
        result = await self._session.execute(
            select(MarketplaceThemePurchaseModel.id)
            .where(
                MarketplaceThemePurchaseModel.user_id == user_id,
                MarketplaceThemePurchaseModel.marketplace_theme_id
                == marketplace_theme_id,
                MarketplaceThemePurchaseModel.status
                == MarketplacePurchaseStatus.SUCCEEDED.value,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_purchases_by_user(
        self, user_id: UUID
    ) -> list[MarketplaceThemePurchase]:
        result = await self._session.execute(
            select(MarketplaceThemePurchaseModel)
            .where(MarketplaceThemePurchaseModel.user_id == user_id)
            .order_by(desc(MarketplaceThemePurchaseModel.created_at))
        )
        return [self._purchase_to_entity(m) for m in result.scalars().all()]

    async def has_active_install(
        self, user_id: UUID, marketplace_theme_id: UUID
    ) -> bool:
        """True if `user_id` has any active install of `theme_id` across
        any of their stores. Used by the review service to mark a free
        theme's review as `is_verified_purchase=True`. (For paid themes
        we use `has_active_purchase` instead — install can be uninstalled
        but the purchase persists, which is the right "verified" signal.)
        """
        # Walk via the user's stores. We don't store user_id directly
        # on installs — they're store-scoped — so the check joins
        # `stores` through the user's owner relationship.
        from src.infrastructure.database.models.tenant.store import StoreModel

        result = await self._session.execute(
            select(MarketplaceThemeInstallationModel.id)
            .join(
                StoreModel,
                StoreModel.id == MarketplaceThemeInstallationModel.store_id,
            )
            .where(
                StoreModel.owner_id == user_id,
                MarketplaceThemeInstallationModel.marketplace_theme_id
                == marketplace_theme_id,
                MarketplaceThemeInstallationModel.uninstalled_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ── Reviews ───────────────────────────────────────────────────────────────

    def _review_to_entity(
        self, m: MarketplaceThemeReviewModel
    ) -> MarketplaceThemeReview:
        return MarketplaceThemeReview(
            id=m.id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            marketplace_theme_id=m.marketplace_theme_id,
            user_id=m.user_id,
            rating=m.rating,
            title=m.title,
            body=m.body,
            is_verified_purchase=m.is_verified_purchase,
            developer_response=m.developer_response,
            developer_response_at=m.developer_response_at,
            helpful_count=m.helpful_count,
        )

    async def get_review_by_id(self, review_id: UUID) -> MarketplaceThemeReview | None:
        result = await self._session.execute(
            select(MarketplaceThemeReviewModel).where(
                MarketplaceThemeReviewModel.id == review_id
            )
        )
        m = result.scalar_one_or_none()
        return self._review_to_entity(m) if m else None

    async def get_review_for_user(
        self, marketplace_theme_id: UUID, user_id: UUID
    ) -> MarketplaceThemeReview | None:
        """Returns the user's existing review for this theme if any —
        used by the create endpoint to detect duplicates and surface
        "you already reviewed this; edit instead" cleanly."""
        result = await self._session.execute(
            select(MarketplaceThemeReviewModel).where(
                MarketplaceThemeReviewModel.marketplace_theme_id
                == marketplace_theme_id,
                MarketplaceThemeReviewModel.user_id == user_id,
            )
        )
        m = result.scalar_one_or_none()
        return self._review_to_entity(m) if m else None

    async def create_review(
        self,
        *,
        marketplace_theme_id: UUID,
        user_id: UUID,
        rating: int,
        title: str | None,
        body: str | None,
        is_verified_purchase: bool,
    ) -> MarketplaceThemeReview:
        m = MarketplaceThemeReviewModel(
            marketplace_theme_id=marketplace_theme_id,
            user_id=user_id,
            rating=rating,
            title=title,
            body=body,
            is_verified_purchase=is_verified_purchase,
        )
        self._session.add(m)
        await self._session.flush()
        return self._review_to_entity(m)

    async def update_review(
        self,
        review_id: UUID,
        *,
        rating: int | None = None,
        title: str | None = None,
        body: str | None = None,
        developer_response: str | None = None,
    ) -> None:
        # Title/body are nullable — we accept the sentinel "" and "None"
        # both mean "leave alone"; clearing requires a separate endpoint
        # (kept simple here, matches Shopify's "edit your review" UX).
        values: dict[str, Any] = {}
        if rating is not None:
            values["rating"] = rating
        if title is not None:
            values["title"] = title
        if body is not None:
            values["body"] = body
        if developer_response is not None:
            values["developer_response"] = developer_response
            values["developer_response_at"] = datetime.now(UTC)
        if not values:
            return
        await self._session.execute(
            update(MarketplaceThemeReviewModel)
            .where(MarketplaceThemeReviewModel.id == review_id)
            .values(**values)
        )
        await self._session.flush()

    async def delete_review(self, review_id: UUID) -> bool:
        from sqlalchemy import delete

        result = await self._session.execute(
            delete(MarketplaceThemeReviewModel).where(
                MarketplaceThemeReviewModel.id == review_id
            )
        )
        await self._session.flush()
        return (result.rowcount or 0) > 0

    async def list_reviews_for_theme(
        self,
        marketplace_theme_id: UUID,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[MarketplaceThemeReview], int]:
        base = select(MarketplaceThemeReviewModel).where(
            MarketplaceThemeReviewModel.marketplace_theme_id == marketplace_theme_id
        )
        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar() or 0

        q = (
            base
            .order_by(desc(MarketplaceThemeReviewModel.created_at))
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self._session.execute(q)
        return [self._review_to_entity(m) for m in result.scalars().all()], total

    async def recompute_theme_rating_aggregates(
        self, marketplace_theme_id: UUID
    ) -> tuple[float, int]:
        """Recompute (average_rating, review_count) from the review rows
        and persist them on `marketplace_themes`. Called after every
        review create/update/delete in the same transaction.

        We compute aggregates in SQL rather than incrementally adjusting
        because the "user updated their existing review" path needs both
        before- and after-rating, and SQL keeps the math correct without
        a select-then-write race.
        """
        agg = await self._session.execute(
            select(
                func.coalesce(func.avg(MarketplaceThemeReviewModel.rating), 0.0),
                func.count(MarketplaceThemeReviewModel.id),
            ).where(
                MarketplaceThemeReviewModel.marketplace_theme_id == marketplace_theme_id
            )
        )
        avg_rating, review_count = agg.one()
        avg_rating = float(avg_rating or 0.0)
        review_count = int(review_count or 0)
        await self._session.execute(
            update(MarketplaceThemeModel)
            .where(MarketplaceThemeModel.id == marketplace_theme_id)
            .values(
                average_rating=avg_rating,
                review_count=review_count,
            )
        )
        await self._session.flush()
        return avg_rating, review_count
