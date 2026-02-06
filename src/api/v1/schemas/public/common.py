"""Common Pydantic schemas."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = 1
    page_size: int = 20


class PaginatedListResponse(BaseModel, Generic[T]):
    """Paginated list response schema (offset-based)."""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class CursorPaginatedListResponse(BaseModel, Generic[T]):
    """Cursor-paginated list response schema.

    Optimized for mobile/3G networks:
    - No total count (expensive query)
    - Opaque cursor tokens for O(1) pagination
    - has_more flag for infinite scroll
    """

    items: list[T]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_more: bool = False


class MessageResponse(BaseModel):
    """Simple message response schema."""

    message: str


class DeleteResponse(BaseModel):
    """Delete response schema."""

    success: bool
    message: str = "Resource deleted successfully"
