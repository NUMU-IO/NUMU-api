"""Common Pydantic schemas."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = 1
    page_size: int = 20


class PaginatedListResponse(BaseModel, Generic[T]):
    """Paginated list response schema."""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class MessageResponse(BaseModel):
    """Simple message response schema."""

    message: str


class DeleteResponse(BaseModel):
    """Delete response schema."""

    success: bool
    message: str = "Resource deleted successfully"
