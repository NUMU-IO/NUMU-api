"""Upload product image use case."""

from dataclasses import dataclass
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


class UploadProductImageUseCase:
    """Use case for uploading product images to Cloudflare R2.

    Validates:
    - File type (JPEG, PNG, WebP, GIF only)
    - File size (max 5 MB)
    - Product exists and belongs to user's store
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
        dto: UploadProductImageDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> UploadedImageDTO:
        """Upload a product image.

        Args:
            dto: Upload data with file content and metadata.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            UploadedImageDTO with the image URL and metadata.

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

        
        ext = ALLOWED_IMAGE_TYPES.get(content_type, ".jpg")
        safe_filename = f"{dto.product_id}_{dto.filename}"
        if not safe_filename.lower().endswith(ext):
            safe_filename = f"{safe_filename}{ext}"

        
        uploaded_file = await self.storage_service.upload_file(
            file_content=dto.file_content,
            filename=safe_filename,
            content_type=content_type,
            bucket=StorageBucket.PRODUCTS,
        )

        
        if uploaded_file.url not in product.images:
            product.images = [*product.images, uploaded_file.url]
            await self.product_repository.update(product)

        return UploadedImageDTO(
            url=uploaded_file.url,
            key=uploaded_file.key,
            size=uploaded_file.size,
            content_type=uploaded_file.content_type,
            product_id=dto.product_id,
        )


class DeleteProductImageUseCase:
    """Use case for deleting product images from Cloudflare R2."""

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
        """Delete a product image.

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
            raise ValidationError(
                f"Image not found on product. URL: {image_url}"
            )

        # Extract key from URL using proper URL parsing
        # URL format: https://pub-xxx.r2.dev/products/abc123.jpg
        # Key format: products/abc123.jpg
        key = None
        try:
            parsed = urlparse(image_url)
            path = parsed.path.lstrip("/")
            if path:
                key = path
        except Exception:
            # Fallback to simple string parsing if URL is malformed
            if "/products/" in image_url:
                key = "products/" + image_url.split("/products/")[-1]

        if key:
            await self.storage_service.delete_file(key)

        
        product.images = [img for img in product.images if img != image_url]
        await self.product_repository.update(product)

        return True
