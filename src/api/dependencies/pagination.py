"""Cursor-based pagination for 3G-optimized API responses.

Cursor pagination provides O(1) performance regardless of page depth,
making it ideal for mobile/3G networks where users scroll through large lists.

Key advantages over offset pagination:
- Constant performance at any depth (no OFFSET N query penalty)
- Stable results even when data changes
- Natural fit for infinite scroll UX patterns

Usage:
    @router.get("/products")
    async def list_products(
        cursor_params: CursorParams = Depends(get_cursor_params),
    ):
        products, pagination = await paginate_with_cursor(
            db, query, cursor_params, ["created_at", "id"]
        )
        return CursorPaginatedResponse(
            items=products,
            **pagination
        )

3G Optimization:
- Default page size: 15 items (optimal for 3G)
- Supports variable page sizes based on network
- Opaque cursor tokens hide implementation details
"""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from fastapi import HTTPException, Query
from pydantic import BaseModel

T = TypeVar("T")


@dataclass
class CursorParams:
    """Parameters for cursor-based pagination.

    Attributes:
        cursor: Opaque cursor token from previous response
        limit: Number of items to return (default: 15 for 3G)
        direction: 'next' or 'prev' for bi-directional pagination
    """

    cursor: str | None
    limit: int
    direction: str = "next"


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Response model for cursor-paginated results.

    Optimized for mobile clients - excludes total_count
    (requires expensive COUNT query).
    """

    items: list[T]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_more: bool = False


class CursorEncoder:
    """Encodes and decodes cursor tokens.

    Cursor format:
    {
        "v": 1,  # version for future compatibility
        "ts": "2024-01-15T10:30:00",  # timestamp field value
        "id": "uuid-string",  # primary key value
        "d": "next"  # direction
    }
    """

    VERSION = 1

    @classmethod
    def encode(
        cls,
        timestamp: datetime | str | None,
        record_id: UUID | str,
        direction: str = "next",
    ) -> str:
        """Encode cursor values into opaque token.

        Args:
            timestamp: The timestamp field value (for ordering)
            record_id: The primary key value (for uniqueness)
            direction: Pagination direction

        Returns:
            Base64-encoded cursor token
        """
        # Convert datetime to ISO string
        if isinstance(timestamp, datetime):
            ts_str = timestamp.isoformat()
        else:
            ts_str = str(timestamp) if timestamp else None

        # Convert UUID to string
        id_str = str(record_id)

        data = {
            "v": cls.VERSION,
            "ts": ts_str,
            "id": id_str,
            "d": direction,
        }

        json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(json_bytes).decode("ascii")

    @classmethod
    def decode(cls, token: str) -> dict[str, Any]:
        """Decode cursor token to extract values.

        Args:
            token: Base64-encoded cursor token

        Returns:
            Dictionary with cursor values

        Raises:
            HTTPException: If token is invalid or malformed
        """
        try:
            json_bytes = base64.urlsafe_b64decode(token.encode("ascii"))
            data = json.loads(json_bytes.decode("utf-8"))

            # Validate version
            if data.get("v") != cls.VERSION:
                raise ValueError("Invalid cursor version")

            return {
                "timestamp": data.get("ts"),
                "id": data.get("id"),
                "direction": data.get("d", "next"),
            }
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid cursor token: {e}"
            ) from e


def get_cursor_params(
    cursor: str | None = Query(
        None,
        description="Cursor token from previous response for pagination",
        example="eyJ2IjoxLCJ0cyI6IjIwMjQtMDEtMTVUMTA6MzA6MDAiLCJpZCI6IjEyMzQ1In0=",
    ),
    limit: int = Query(
        15,
        ge=1,
        le=100,
        description="Number of items per page (default: 15, max: 100). "
        "Recommended: 10-15 for 3G, 20-30 for 4G/WiFi.",
    ),
) -> CursorParams:
    """Dependency to get cursor pagination parameters.

    Returns:
        CursorParams with cursor, limit, and direction
    """
    direction = "next"

    if cursor:
        decoded = CursorEncoder.decode(cursor)
        direction = decoded.get("direction", "next")

    return CursorParams(cursor=cursor, limit=limit, direction=direction)


def build_cursor_response(
    items: list[Any],
    limit: int,
    id_field: str = "id",
    timestamp_field: str = "created_at",
) -> dict[str, Any]:
    """Build cursor pagination response metadata.

    Args:
        items: List of items (should have limit+1 items if has_more)
        limit: Requested page size
        id_field: Field name for unique ID
        timestamp_field: Field name for timestamp ordering

    Returns:
        Dictionary with next_cursor, prev_cursor, has_more
    """
    has_more = len(items) > limit

    # Trim to requested limit
    if has_more:
        items = items[:limit]

    result = {
        "has_more": has_more,
        "next_cursor": None,
        "prev_cursor": None,
    }

    if items:
        # Get last item for next cursor
        last_item = items[-1]
        last_ts = getattr(last_item, timestamp_field, None)
        last_id = getattr(last_item, id_field, None)

        if last_ts is not None and last_id is not None:
            result["next_cursor"] = CursorEncoder.encode(last_ts, last_id, "next")

        # Get first item for prev cursor (for bi-directional)
        first_item = items[0]
        first_ts = getattr(first_item, timestamp_field, None)
        first_id = getattr(first_item, id_field, None)

        if first_ts is not None and first_id is not None:
            result["prev_cursor"] = CursorEncoder.encode(first_ts, first_id, "prev")

    return result


def get_cursor_values(cursor: str | None) -> tuple[str | None, str | None] | None:
    """Extract timestamp and ID from cursor token.

    Args:
        cursor: Cursor token string

    Returns:
        Tuple of (timestamp, id) or None if no cursor
    """
    if not cursor:
        return None

    decoded = CursorEncoder.decode(cursor)
    return (decoded.get("timestamp"), decoded.get("id"))


# =============================================================================
# Page Size Recommendations
# =============================================================================

# Network-optimized page sizes
PAGE_SIZE_3G = 15  # Slow networks
PAGE_SIZE_4G = 25  # Fast mobile networks
PAGE_SIZE_WIFI = 50  # WiFi/broadband

# Default for mobile-first design
DEFAULT_PAGE_SIZE = PAGE_SIZE_3G
