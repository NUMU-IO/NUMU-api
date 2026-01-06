"""Base DTO class."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class BaseDTO:
    """Base DTO class."""

    pass


@dataclass
class PaginatedDTO(BaseDTO):
    """Paginated response DTO."""

    items: list
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list,
        total: int,
        page: int = 1,
        page_size: int = 20,
    ) -> "PaginatedDTO":
        """Create a paginated DTO."""
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
