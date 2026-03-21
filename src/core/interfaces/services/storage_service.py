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
