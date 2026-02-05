"""Upload product image use case."""

from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
)
from src.infrastructure.external_services.image.image_pipeline import ImagePipeline
from src.infrastructure.external_services.image.image_processor import (
    ImageProcessingError,
)

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Maximum file size (5 MB)
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024


@dataclass
class UploadProductImageDTO(BaseDTO):
    """Upload product image request DTO."""

    product_id: UUID
    file_content: bytes
    filename: str
    content_type: str


@dataclass
class UploadedImageDTO(BaseDTO):
    """Uploaded image result DTO."""

    url: str
    key: str
    size: int
    content_type: str
    product_id: UUID
    variant_urls: dict[str, str] = field(default_factory=dict)


class UploadProductImageUseCase:
    """Use case for uploading product images with optimization pipeline.

    Validates:
    - File type (JPEG, PNG, WebP, GIF only)
    - File size (max 5 MB)
    - Product exists and belongs to user's store

    Processing pipeline:
    - Validates and opens image
    - Strips EXIF metadata (preserving orientation)
    - Converts to WebP format
    - Generates 3 size variants (thumbnail 150px, medium 600px, large 1200px)
    - Uploads all variants to Cloudflare R2
    - Stores variant URLs in product.media_urls metadata
    """

    def __init__(
        self,
        image_pipeline: ImagePipeline,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        """Initialize use case.

        Args:
            image_pipeline: Image processing and upload pipeline.
            product_repository: Product repository instance.
            store_repository: Store repository instance.
        """
        self.image_pipeline = image_pipeline
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        dto: UploadProductImageDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> UploadedImageDTO:
        """Upload a product image through the optimization pipeline.

        Args:
            dto: Upload data with file content and metadata.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            UploadedImageDTO with the image URL, variant URLs, and metadata.

        Raises:
            ValidationError: If file type or size is invalid.
            EntityNotFoundError: If product not found.
            AuthorizationError: If user doesn't own the store.
        """
        content_type = dto.content_type.lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            allowed_types = ", ".join(ALLOWED_IMAGE_TYPES.keys())
            raise ValidationError(
                f"Invalid image type: {dto.content_type}. "
                f"Allowed types: {allowed_types}"
            )

        file_size = len(dto.file_content)
        if file_size > MAX_FILE_SIZE_BYTES:
            max_size_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
            actual_size_mb = file_size / (1024 * 1024)
            raise ValidationError(
                f"File too large: {actual_size_mb:.2f} MB. "
                f"Maximum allowed: {max_size_mb:.0f} MB"
            )

        if file_size == 0:
            raise ValidationError("File is empty")

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to upload images to this store"
            )

        product = await self.product_repository.get_by_id(dto.product_id)
        if not product:
            raise EntityNotFoundError("Product", str(dto.product_id))

        if product.store_id != store_id:
            raise EntityNotFoundError("Product", str(dto.product_id))

        # Process through image pipeline: optimize + generate variants + upload
        try:
            pipeline_result = await self.image_pipeline.process_and_upload(
                file_data=dto.file_content,
                product_id=dto.product_id,
                original_filename=dto.filename,
                bucket=StorageBucket.PRODUCTS,
            )
        except ImageProcessingError as exc:
            raise ValidationError(f"Image processing failed: {exc}")

        # Add optimized original URL to product.images
        if pipeline_result.url not in product.images:
            product.images = [*product.images, pipeline_result.url]

        # Store variant URLs and keys in product.metadata for later retrieval
        media_urls = product.metadata.get("media_urls", {})
        media_urls[pipeline_result.url] = {
            "variants": pipeline_result.variant_urls,
            "variant_keys": pipeline_result.variant_keys,
        }
        product.metadata = {**product.metadata, "media_urls": media_urls}

        await self.product_repository.update(product)

        return UploadedImageDTO(
            url=pipeline_result.url,
            key=pipeline_result.key,
            size=pipeline_result.total_size,
            content_type="image/webp",
            product_id=dto.product_id,
            variant_urls=pipeline_result.variant_urls,
        )


class DeleteProductImageUseCase:
    """Use case for deleting product images from Cloudflare R2.

    Handles cleanup of all image variants (original + size variants)
    when an image pipeline was used for upload.
    """

    def __init__(
        self,
        storage_service: IStorageService,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        """Initialize use case.

        Args:
            storage_service: Storage service (Cloudflare R2) instance.
            product_repository: Product repository instance.
            store_repository: Store repository instance.
        """
        self.storage_service = storage_service
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        product_id: UUID,
        image_url: str,
        store_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Delete a product image and all its variants.

        Args:
            product_id: The product UUID.
            image_url: The URL of the image to delete.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            True if deleted successfully.

        Raises:
            EntityNotFoundError: If product not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If image not found on product.
        """

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to delete images from this store"
            )

        product = await self.product_repository.get_by_id(product_id)
        if not product:
            raise EntityNotFoundError("Product", str(product_id))

        if product.store_id != store_id:
            raise EntityNotFoundError("Product", str(product_id))

        if image_url not in product.images:
            raise ValidationError(f"Image not found on product. URL: {image_url}")

        # Check if this image has variant keys stored in metadata
        media_urls = product.metadata.get("media_urls", {})
        image_meta = media_urls.get(image_url, {})
        variant_keys = image_meta.get("variant_keys", {})

        if variant_keys:
            # Delete all variant files from storage
            for variant_name, key in variant_keys.items():
                try:
                    await self.storage_service.delete_file(key)
                except Exception:
                    pass  # Log but don't fail deletion
        else:
            # Fallback: delete single file by extracting key from URL
            key = None
            try:
                parsed = urlparse(image_url)
                path = parsed.path.lstrip("/")
                if path:
                    key = path
            except Exception:
                if "/products/" in image_url:
                    key = "products/" + image_url.split("/products/")[-1]

            if key:
                await self.storage_service.delete_file(key)

        # Remove URL from product images
        product.images = [img for img in product.images if img != image_url]

        # Clean up media_urls metadata
        if image_url in media_urls:
            del media_urls[image_url]
            product.metadata = {**product.metadata, "media_urls": media_urls}

        await self.product_repository.update(product)

        return True
