"""Standardized API responses."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    """Success response wrapper."""

    success: bool = True
    data: T
    message: str | None = None


class ErrorResponse(BaseModel):
    """Error response wrapper."""

    success: bool = False
    error: str
    code: str
    details: dict[str, Any] | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    success: bool = True
    data: list[T]
    pagination: dict[str, int]

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedResponse[T]":
        """Create paginated response."""
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        return cls(
            data=items,
            pagination={
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
        )


def success_response(
    data: Any,
    message: str | None = None,
) -> dict[str, Any]:
    """Create success response."""
    response = {"success": True, "data": data}
    if message:
        response["message"] = message
    return response


def error_response(
    error: str,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create error response."""
    response = {
        "success": False,
        "error": error,
        "code": code,
    }
    if details:
        response["details"] = details
    return response
