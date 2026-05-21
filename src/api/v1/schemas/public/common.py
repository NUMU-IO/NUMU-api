"""Common Pydantic schemas."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = Field(1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(20, ge=1, le=100, description="Items per page (max 100)")


class PaginatedListResponse(BaseModel, Generic[T]):
    """Paginated list response schema (offset-based)."""

    items: list[T] = Field(description="Page of results")
    total: int = Field(description="Total number of results")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    total_pages: int = Field(description="Total number of pages")


class CursorPaginatedListResponse(BaseModel, Generic[T]):
    """Cursor-paginated list response schema.

    Optimized for mobile/3G networks:
    - No total count (expensive query)
    - Opaque cursor tokens for O(1) pagination
    - has_more flag for infinite scroll
    """

    items: list[T] = Field(description="Page of results")
    next_cursor: str | None = Field(None, description="Opaque cursor for the next page")
    prev_cursor: str | None = Field(
        None, description="Opaque cursor for the previous page"
    )
    has_more: bool = Field(False, description="Whether more results exist")


class MessageResponse(BaseModel):
    """Simple message response schema."""

    message: str = Field(description="Human-readable message")


class DeleteResponse(BaseModel):
    """Delete response schema."""

    success: bool = Field(description="Whether the deletion succeeded")
    message: str = Field("Resource deleted successfully", description="Result message")


class ErrorResponse(BaseModel):
    """Standard error response schema."""

    detail: str = Field(description="Human-readable error message")
    error_code: str | None = Field(None, description="Machine-readable error code")
