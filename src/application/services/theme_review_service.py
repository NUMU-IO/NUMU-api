"""Service for marketplace theme reviews + ratings.

Owns:
  * Authorization rules (only buyers/installers may review; only the
    review's author may edit/delete; only the developer of the theme
    may post a developer_response).
  * Verified-purchase flag: at create time, look up whether the user
    has a succeeded purchase (paid theme) or active install (free
    theme). Edits don't refresh the flag — verification is a snapshot
    at first review.
  * Aggregate maintenance: every create/update/delete recomputes
    `marketplace_themes.average_rating` + `review_count` in the same
    transaction so the catalog never shows stale ratings.

Scope intentionally excluded:
  * Helpful-vote endpoint (column exists; no writes wired yet).
  * Moderation flow ("flag this review as inappropriate"). Future P2.
  * Threading on the developer response. Single-shot reply for now —
    matches Shopify's pattern.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.core.entities.marketplace_theme import (
    MarketplaceTheme,
    MarketplaceThemeReview,
)
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)


class ThemeReviewService:
    """Owns the create/edit/list/respond/delete flow for theme reviews."""

    def __init__(self, *, marketplace_repo: MarketplaceRepository) -> None:
        self._repo = marketplace_repo

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_review(
        self,
        *,
        marketplace_theme_id: UUID,
        user_id: UUID,
        rating: int,
        title: str | None,
        body: str | None,
    ) -> MarketplaceThemeReview:
        """Create a new review. Refuses if:
        * the theme doesn't exist or isn't published (a draft theme
          isn't visible to buyers, so reviews shouldn't accumulate),
        * the user already reviewed it (caller should PUT instead),
        * the user has neither a succeeded purchase nor an active
          install — random-stranger reviews are not allowed.
        """
        if rating < 1 or rating > 5:
            raise ValueError("rating must be between 1 and 5")

        theme = await self._repo.get_theme_by_id(marketplace_theme_id)
        if theme is None:
            raise ValueError("Theme not found")
        # We don't gate on PUBLISHED status alone — once published, the
        # theme can later be SUSPENDED, but existing reviews stay
        # visible. Block new reviews when the theme isn't currently
        # published.
        if theme.status.value not in ("published", "approved"):
            raise ValueError("Cannot review a theme that isn't published")

        existing = await self._repo.get_review_for_user(marketplace_theme_id, user_id)
        if existing is not None:
            raise ValueError(
                "You already reviewed this theme — edit your review instead."
            )

        is_verified = await self._is_verified_reviewer(
            user_id=user_id,
            marketplace_theme_id=marketplace_theme_id,
            theme=theme,
        )
        if not is_verified:
            # Strict gate: the only way to verify is to have purchased
            # OR installed the theme. Allowing un-verified reviews would
            # invite review-bombing and rating manipulation.
            raise ValueError(
                "Only buyers/users of a theme may review it. "
                "Install it first (or purchase, for paid themes)."
            )

        review = await self._repo.create_review(
            marketplace_theme_id=marketplace_theme_id,
            user_id=user_id,
            rating=rating,
            title=title,
            body=body,
            is_verified_purchase=is_verified,
        )
        await self._repo.recompute_theme_rating_aggregates(marketplace_theme_id)
        return review

    async def _is_verified_reviewer(
        self,
        *,
        user_id: UUID,
        marketplace_theme_id: UUID,
        theme: MarketplaceTheme,
    ) -> bool:
        if theme.price_cents > 0:
            return await self._repo.has_active_purchase(user_id, marketplace_theme_id)
        return await self._repo.has_active_install(user_id, marketplace_theme_id)

    # ── Update / delete ───────────────────────────────────────────────────────

    async def update_review(
        self,
        *,
        review_id: UUID,
        user_id: UUID,
        rating: int | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> MarketplaceThemeReview:
        review = await self._repo.get_review_by_id(review_id)
        if review is None or review.user_id != user_id:
            raise ValueError("Review not found")
        if rating is not None and (rating < 1 or rating > 5):
            raise ValueError("rating must be between 1 and 5")

        await self._repo.update_review(review_id, rating=rating, title=title, body=body)
        # Rating may have changed, refresh aggregates.
        if rating is not None:
            await self._repo.recompute_theme_rating_aggregates(
                review.marketplace_theme_id
            )

        updated = await self._repo.get_review_by_id(review_id)
        assert updated is not None
        return updated

    async def delete_review(self, *, review_id: UUID, user_id: UUID) -> bool:
        review = await self._repo.get_review_by_id(review_id)
        if review is None or review.user_id != user_id:
            return False
        ok = await self._repo.delete_review(review_id)
        if ok:
            await self._repo.recompute_theme_rating_aggregates(
                review.marketplace_theme_id
            )
        return ok

    # ── Developer response ────────────────────────────────────────────────────

    async def respond_to_review(
        self,
        *,
        review_id: UUID,
        developer_user_id: UUID,
        response_text: str,
    ) -> MarketplaceThemeReview:
        """Theme developer posts a single response to a review.

        Authorization: the responding user MUST be the listing's
        `developer_id`. We look the theme up via the review and reject
        otherwise. Subsequent calls overwrite the prior response —
        single-thread by design.
        """
        if not response_text or not response_text.strip():
            raise ValueError("Response text is required")

        review = await self._repo.get_review_by_id(review_id)
        if review is None:
            raise ValueError("Review not found")

        theme = await self._repo.get_theme_by_id(review.marketplace_theme_id)
        if theme is None or theme.developer_id != developer_user_id:
            raise ValueError("Only the theme's developer may respond to its reviews.")

        await self._repo.update_review(review_id, developer_response=response_text)
        updated = await self._repo.get_review_by_id(review_id)
        assert updated is not None
        return updated

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_for_theme(
        self,
        marketplace_theme_id: UUID,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        items, total = await self._repo.list_reviews_for_theme(
            marketplace_theme_id, page=page, per_page=per_page
        )
        return {
            "reviews": items,
            "total": total,
            "page": page,
            "per_page": per_page,
        }
