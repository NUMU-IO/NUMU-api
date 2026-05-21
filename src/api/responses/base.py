"""Standardized API responses."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationMeta(BaseModel):
    """Pagination metadata for list responses."""

    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number (1-indexed)")
    page_size: int = Field(description="Number of items per page")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")


class SuccessResponse(BaseModel, Generic[T]):
    """Success response wrapper for single items."""

    success: bool = True
    data: T
    message: str | None = None


class ErrorResponse(BaseModel):
    """Error response wrapper."""

    success: bool = False
    error: str = Field(description="Human-readable error message")
    code: str = Field(description="Machine-readable error code")
    details: dict[str, Any] | None = Field(
        default=None, description="Additional error details"
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper for list endpoints."""

    success: bool = True
    data: list[T]
    pagination: PaginationMeta
    message: str | None = None

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
        message: str | None = None,
    ) -> "PaginatedResponse[T]":
        """Create paginated response from items and pagination info.

        Args:
            items: List of items for current page
            total: Total number of items across all pages
            page: Current page number (1-indexed)
            page_size: Number of items per page
            message: Optional message

        Returns:
            PaginatedResponse with pagination metadata
        """
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        return cls(
            data=items,
            pagination=PaginationMeta(
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
            message=message,
        )


class ListResponse(BaseModel, Generic[T]):
    """Simple list response without pagination (for small lists)."""

    success: bool = True
    data: list[T]
    count: int = Field(description="Number of items in the list")
    message: str | None = None

    @classmethod
    def create(
        cls,
        items: list[T],
        message: str | None = None,
    ) -> "ListResponse[T]":
        """Create list response from items.

        Args:
            items: List of items
            message: Optional message

        Returns:
            ListResponse with item count
        """
        return cls(data=items, count=len(items), message=message)


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    success: bool = True
    message: str = "Resource deleted successfully"
    deleted_id: str | None = None


class BulkOperationResponse(BaseModel):
    """Response for bulk operations."""

    success: bool = True
    message: str
    processed: int = Field(description="Number of items processed")
    failed: int = Field(default=0, description="Number of items that failed")
    errors: list[dict[str, Any]] = Field(
        default_factory=list, description="Error details for failed items"
    )


def success_response(
    data: Any,
    message: str | None = None,
) -> dict[str, Any]:
    """Create success response dictionary.

    Args:
        data: Response data
        message: Optional success message

    Returns:
        Dictionary with success response structure
    """
    response: dict[str, Any] = {"success": True, "data": data}
    if message:
        response["message"] = message
    return response


def error_response(
    error: str,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create error response dictionary.

    Args:
        error: Human-readable error message
        code: Machine-readable error code
        details: Optional additional error details

    Returns:
        Dictionary with error response structure
    """
    response: dict[str, Any] = {
        "success": False,
        "error": error,
        "code": code,
    }
    if details:
        response["details"] = details
    return response


def paginated_response(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
    message: str | None = None,
) -> dict[str, Any]:
    """Create paginated response dictionary.

    Args:
        items: List of items for current page
        total: Total number of items
        page: Current page number
        page_size: Items per page
        message: Optional message

    Returns:
        Dictionary with paginated response structure
    """
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    response: dict[str, Any] = {
        "success": True,
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }
    if message:
        response["message"] = message
    return response
