"""Use case: Import products from social media post URLs (no OAuth required)."""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.value_objects.money import Currency, Money
from src.infrastructure.external_services.meta.url_scraper import (
    ScrapedPost,
    scrape_posts,
)

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:200]


@dataclass
class UrlImportResult:
    """Result of importing a single URL."""

    url: str
    product_id: str | None = None
    product_name: str | None = None
    images_count: int = 0
    error: str | None = None


class ImportFromUrlUseCase:
    """Import products from Instagram/Facebook post URLs.

    Scrapes OG meta tags from public post URLs — no OAuth or API keys needed.
    Creates draft products with the scraped image, name, and price.
    """

    def __init__(self, product_repo: IProductRepository) -> None:
        self.product_repo = product_repo

    async def execute(
        self,
        store_id: UUID,
        tenant_id: UUID | None,
        urls: list[str],
    ) -> list[UrlImportResult]:
        """Scrape URLs and create draft products.

        Returns a result per URL with either product_id or error.
        """
        # Scrape all URLs concurrently
        scraped = await scrape_posts(urls)

        results: list[UrlImportResult] = []
        for post in scraped:
            if post.error:
                results.append(UrlImportResult(url=post.url, error=post.error))
                continue

            try:
                product_id, img_count = await self._create_product(
                    store_id, tenant_id, post
                )
                results.append(
                    UrlImportResult(
                        url=post.url,
                        product_id=str(product_id),
                        product_name=post.suggested_name,
                        images_count=img_count,
                    )
                )
            except Exception as e:
                logger.warning("Failed to import %s: %s", post.url, e)
                results.append(UrlImportResult(url=post.url, error=str(e)))

        return results

    async def _create_product(
        self,
        store_id: UUID,
        tenant_id: UUID | None,
        post: ScrapedPost,
    ) -> tuple[UUID, int]:
        name = post.suggested_name or "Imported Product"
        slug = _slugify(name)

        # Ensure slug uniqueness
        existing = await self.product_repo.get_by_slug(store_id, slug)
        if existing:
            from uuid import uuid4

            slug = f"{slug}-{uuid4().hex[:6]}"

        price_amount = (
            Decimal(post.suggested_price) if post.suggested_price else Decimal(0)
        )

        # Download all images to R2 (carousel support)
        source_urls = post.image_urls or ([post.image_url] if post.image_url else [])
        images: list[str] = []
        if source_urls:
            from src.application.use_cases.social.import_posts import (
                _download_and_upload_image,
            )

            for img_url in source_urls:
                r2_url = await _download_and_upload_image(img_url, store_id)
                images.append(r2_url or img_url)

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
        return created.id, len(images)
