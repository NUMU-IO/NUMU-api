"""Use case: Import social posts as draft products."""

import logging
import re
from uuid import UUID

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.social_connection_repository import (
    ISocialConnectionRepository,
)
from src.core.interfaces.repositories.social_post_repository import (
    ISocialPostRepository,
)
from src.core.value_objects.money import Currency, Money

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Create a URL-friendly slug from text."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:200]


def _extract_name_from_caption(caption: str) -> str:
    """Extract a product name from a social post caption."""
    # Take first line, strip emojis and common filler
    first_line = caption.split("\n")[0].strip()
    # Remove emojis (rough pattern)
    cleaned = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF\U00002702-\U000027B0]+",
        "",
        first_line,
    )
    cleaned = cleaned.strip(" -—|•")
    # Truncate to reasonable length
    if len(cleaned) > 100:
        cleaned = cleaned[:97] + "..."
    return cleaned or "Imported Product"


async def _download_and_upload_image(image_url: str, store_id: UUID) -> str | None:
    """Download an image from a URL and upload it to R2 storage.

    Returns the public R2 URL, or None if the download/upload fails.
    Uses Googlebot UA for Instagram CDN URLs (hash is tied to the scraping UA).
    """
    try:
        import httpx

        from src.api.dependencies.services import get_storage_service

        storage = get_storage_service()

        # Instagram CDN URLs have a hash tied to the UA used during scraping
        headers = {}
        if "cdninstagram" in image_url or "fbcdn.net" in image_url:
            headers["User-Agent"] = (
                "Mozilla/5.0 (compatible; Googlebot/2.1; "
                "+http://www.google.com/bot.html)"
            )

        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "image/jpeg")
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        from uuid import uuid4

        filename = f"stores/{store_id}/products/social-import-{uuid4().hex[:8]}.{ext}"

        uploaded = await storage.upload_file(
            file_content=resp.content,
            filename=filename,
            content_type=content_type,
        )
        return uploaded.url
    except Exception as e:
        logger.warning("Failed to download/upload image %s: %s", image_url, e)
        return None


class ImportSocialPostsUseCase:
    """Import one or more social posts as draft NUMU products."""

    def __init__(
        self,
        connection_repo: ISocialConnectionRepository,
        post_repo: ISocialPostRepository,
        product_repo: IProductRepository,
    ) -> None:
        self.connection_repo = connection_repo
        self.post_repo = post_repo
        self.product_repo = product_repo

    async def execute(
        self,
        connection_id: UUID,
        platform_post_ids: list[str],
    ) -> tuple[list[UUID], list[str]]:
        """Import posts as products.

        Returns (product_ids, errors).
        """
        connection = await self.connection_repo.get_by_id(connection_id)
        if not connection:
            raise EntityNotFoundError("SocialConnection", str(connection_id))

        product_ids: list[UUID] = []
        errors: list[str] = []

        for post_id in platform_post_ids:
            try:
                result = await self._import_single(
                    connection_id, connection.store_id, connection.tenant_id, post_id
                )
                product_ids.append(result)
            except Exception as e:
                logger.warning("Failed to import post %s: %s", post_id, e)
                errors.append(f"Post {post_id}: {e}")

        return product_ids, errors

    async def _import_single(
        self,
        connection_id: UUID,
        store_id: UUID,
        tenant_id: UUID | None,
        platform_post_id: str,
    ) -> UUID:
        """Import a single social post as a draft product."""
        post = await self.post_repo.get_by_platform_post_id(
            connection_id, platform_post_id
        )
        if not post:
            raise ValueError(f"Post {platform_post_id} not found")

        if post.is_imported:
            raise ValueError(f"Post {platform_post_id} has already been imported")

        # Use suggested_name if available, otherwise extract from caption
        name = post.suggested_name or _extract_name_from_caption(
            post.caption or "Imported Product"
        )
        slug = _slugify(name)

        # Check slug uniqueness and append UUID fragment if needed
        existing = await self.product_repo.get_by_slug(store_id, slug)
        if existing:
            slug = f"{slug}-{post.id.hex[:6]}"

        # Use suggested_price (in EGP) or default to 0
        from decimal import Decimal

        price_amount = (
            Decimal(post.suggested_price) if post.suggested_price else Decimal(0)
        )

        # Download image to R2 (falls back to original URL if upload fails)
        images: list[str] = []
        if post.image_url:
            r2_url = await _download_and_upload_image(post.image_url, store_id)
            images = [r2_url or post.image_url]

        # Build product from post data
        product = Product(
            store_id=store_id,
            tenant_id=tenant_id,
            name=name,
            slug=slug,
            price=Money(amount=price_amount, currency=Currency.EGP),
            status=ProductStatus.DRAFT,
            product_type=ProductType.PHYSICAL,
            images=images,
            description=post.caption,
        )

        created = await self.product_repo.create(product)

        # Mark the social post as imported
        await self.post_repo.mark_imported(post.id, created.id)

        return created.id
