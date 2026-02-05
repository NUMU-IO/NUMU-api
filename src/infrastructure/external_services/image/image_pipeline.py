"""Image optimization pipeline.

Orchestrates the full image upload flow:
upload -> optimize original -> generate size variants -> upload all to R2 -> return URLs dict.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from src.core.interfaces.services.storage_service import IStorageService, StorageBucket
from src.infrastructure.external_services.image.image_processor import (
    ImageProcessor,
    ImageProcessingError,
    ImageVariantName,
    ProcessedImageResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ImagePipelineResult:
    """Result of the full image optimization pipeline."""

    # Primary URL (the optimized original)
    url: str
    # Storage key of the optimized original
    key: str
    # Total size of all variants combined
    total_size: int
    # URL for each variant: {"original": "...", "large": "...", "medium": "...", "thumbnail": "..."}
    variant_urls: dict[str, str] = field(default_factory=dict)
    # Storage keys for each variant (for cleanup/deletion)
    variant_keys: dict[str, str] = field(default_factory=dict)


class ImagePipeline:
    """Orchestrates image processing and upload to cloud storage.

    Flow:
    1. Receive raw image bytes
    2. Process through ImageProcessor (validate, strip EXIF, normalize, resize)
    3. Upload optimized original + all size variants to R2
    4. Return URLs dict for all variants

    Usage:
        pipeline = ImagePipeline(
            image_processor=ImageProcessor(),
            storage_service=r2_storage_service,
        )
        result = await pipeline.process_and_upload(
            file_data=raw_bytes,
            product_id=uuid,
            original_filename="photo.jpg",
        )
        # result.variant_urls == {
        #     "original": "https://cdn.example.com/products/abc123_original.webp",
        #     "large": "https://cdn.example.com/products/abc123_large.webp",
        #     "medium": "https://cdn.example.com/products/abc123_medium.webp",
        #     "thumbnail": "https://cdn.example.com/products/abc123_thumbnail.webp",
        # }
    """

    def __init__(
        self,
        image_processor: ImageProcessor,
        storage_service: IStorageService,
    ) -> None:
        self.image_processor = image_processor
        self.storage_service = storage_service

    async def process_and_upload(
        self,
        file_data: bytes,
        product_id: UUID,
        original_filename: str,
        bucket: StorageBucket = StorageBucket.PRODUCTS,
    ) -> ImagePipelineResult:
        """Process an image and upload all variants to cloud storage.

        Args:
            file_data: Raw image file bytes.
            product_id: Product UUID (used in storage key for organization).
            original_filename: Original filename from upload.
            bucket: Storage bucket to upload to.

        Returns:
            ImagePipelineResult with URLs for all variants.

        Raises:
            ImageProcessingError: If image processing fails.
            ExternalServiceError: If upload to storage fails.
        """
        # Step 1: Process image -> get optimized original + variants
        processed_variants = self.image_processor.process_image(file_data)

        # Step 2: Upload each variant to R2
        variant_urls: dict[str, str] = {}
        variant_keys: dict[str, str] = {}
        total_size = 0
        uploaded_keys: list[str] = []

        try:
            for variant in processed_variants:
                filename = self._build_variant_filename(
                    product_id, variant.variant_name
                )
                uploaded = await self.storage_service.upload_file(
                    file_content=variant.data,
                    filename=filename,
                    content_type=variant.content_type,
                    bucket=bucket,
                )
                variant_urls[variant.variant_name] = uploaded.url
                variant_keys[variant.variant_name] = uploaded.key
                total_size += variant.file_size
                uploaded_keys.append(uploaded.key)

                logger.info(
                    "Uploaded variant '%s': %dx%d, %d bytes -> %s",
                    variant.variant_name,
                    variant.width,
                    variant.height,
                    variant.file_size,
                    uploaded.url,
                )

        except Exception as exc:
            # Cleanup any already-uploaded variants on failure
            await self._cleanup_uploaded(uploaded_keys)
            raise ImageProcessingError(
                f"Failed to upload image variants: {exc}"
            )

        # Step 3: Build result
        original_url = variant_urls.get(ImageVariantName.ORIGINAL.value, "")
        original_key = variant_keys.get(ImageVariantName.ORIGINAL.value, "")

        return ImagePipelineResult(
            url=original_url,
            key=original_key,
            total_size=total_size,
            variant_urls=variant_urls,
            variant_keys=variant_keys,
        )

    async def delete_variants(
        self,
        variant_keys: dict[str, str],
    ) -> None:
        """Delete all variant files from storage.

        Used when deleting a product image to clean up all size variants.

        Args:
            variant_keys: Dict mapping variant name to storage key.
        """
        for variant_name, key in variant_keys.items():
            try:
                await self.storage_service.delete_file(key)
                logger.info("Deleted variant '%s': %s", variant_name, key)
            except Exception as exc:
                logger.warning(
                    "Failed to delete variant '%s' (key=%s): %s",
                    variant_name,
                    key,
                    exc,
                )

    async def _cleanup_uploaded(self, keys: list[str]) -> None:
        """Clean up already-uploaded files on pipeline failure."""
        for key in keys:
            try:
                await self.storage_service.delete_file(key)
            except Exception as exc:
                logger.warning("Failed to cleanup key '%s': %s", key, exc)

    @staticmethod
    def _build_variant_filename(product_id: UUID, variant_name: str) -> str:
        """Build a filename for a variant upload.

        Format: {product_id}_{variant_name}.webp

        Args:
            product_id: Product UUID.
            variant_name: Variant name (original, large, medium, thumbnail).

        Returns:
            Formatted filename string.
        """
        return f"{product_id}_{variant_name}.webp"
