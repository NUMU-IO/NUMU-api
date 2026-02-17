"""Common Pydantic schemas."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = Field(1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(20, ge=1, le=100, description="Items per page (max 100)")


class PaginatedListResponse(BaseModel, Generic[T]):
    """Paginated list response schema."""

    items: list[T] = Field(description="Page of results")
    total: int = Field(description="Total number of results")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    total_pages: int = Field(description="Total number of pages")


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
