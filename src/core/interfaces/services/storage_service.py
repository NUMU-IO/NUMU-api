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


def sanitize_object_key(key: str) -> str:
    """Normalise a caller-supplied object key.

    Strips leading/trailing slashes, backslashes, and any ``.``/``..``
    segments so a caller-provided key can never traverse outside its
    intended prefix (or, for local storage, outside the uploads dir).
    """
    parts = [
        segment
        for segment in key.replace("\\", "/").split("/")
        if segment not in ("", ".", "..")
    ]
    return "/".join(parts)


class IStorageService(ABC):
    """File storage service interface."""

    @abstractmethod
    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        bucket: StorageBucket = StorageBucket.PRODUCTS,
        key: str | None = None,
    ) -> UploadedFile:
        """Upload a file to storage.

        When ``key`` is given it is used as the object key verbatim (after
        ``sanitize_object_key``); otherwise a unique key is generated under
        ``bucket``. Callers that need the object to land under a specific
        prefix (e.g. ``customization/{store_id}/``) MUST pass ``key`` — the
        ``bucket`` default would otherwise discard that prefix.
        """
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
