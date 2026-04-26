"""File storage service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class StorageBucket(StrEnum):
    """Storage bucket types."""

    PRODUCTS = "products"
    STORES = "stores"
    AVATARS = "avatars"
    DOCUMENTS = "documents"
    CATEGORIES = "categories"
    THEMES = "themes"
    PAYMENT_PROOFS = "payment-proofs"


@dataclass
class UploadedFile:
    """Uploaded file data."""

    key: str
    url: str
    size: int
    content_type: str


class IStorageService(ABC):
    """File storage service interface."""

    @abstractmethod
    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        bucket: StorageBucket = StorageBucket.PRODUCTS,
    ) -> UploadedFile:
        """Upload a file to storage."""
        ...

    @abstractmethod
    async def delete_file(self, key: str) -> bool:
        """Delete a file from storage."""
        ...

    @abstractmethod
    async def get_signed_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> str:
        """Get a signed URL for a file."""
        ...

    @abstractmethod
    async def file_exists(self, key: str) -> bool:
        """Check if a file exists."""
        ...

    @abstractmethod
    def get_public_url(self, key: str) -> str:
        """Get the public URL for a file."""
        ...

    @abstractmethod
    async def get_object_bytes(self, key: str) -> tuple[bytes, str | None]:
        """Fetch the raw bytes of an object plus its content-type.

        Used to stream private objects (e.g. payment proofs) through the
        authenticated API without exposing signed URLs that depend on a
        publicly-reachable storage hostname. Returns
        ``(content, content_type)``. Raises ``ExternalServiceError`` on
        backend failures and ``FileNotFoundError`` semantics on missing
        keys (concrete subclass-specific).
        """
        ...

    @abstractmethod
    async def list_files(self, prefix: str) -> list[dict]:
        """List files under a given prefix. Returns list of {key, url, size, last_modified}."""
        ...
