"""Sort parameter validation.

Provides a FastAPI dependency-compatible validator that rejects ``sort_by``
values not present in a per-endpoint whitelist.  This prevents SQL injection
via sort column names and avoids leaking internal column names to clients.

Usage in a route:

    from src.api.utils.sort_validation import SortValidator

    PRODUCT_SORT_FIELDS = {"name", "price", "created_at", "updated_at", "quantity"}

    @router.get("/")
    async def list_items(
        sort_by: str | None = Query(None),
        sort_order: str = Query("asc"),
        _sort: None = Depends(SortValidator(PRODUCT_SORT_FIELDS)),
    ):
        ...
"""

from __future__ import annotations

from fastapi import HTTPException, Query, status


class SortValidator:
    """Callable dependency that validates ``sort_by`` against a whitelist.

    Also normalises ``sort_order`` to ``asc`` or ``desc``.
    """

    __slots__ = ("_allowed",)

    def __init__(self, allowed_fields: set[str]) -> None:
        self._allowed = frozenset(allowed_fields)

    def __call__(
        self,
        sort_by: str | None = Query(None),
        sort_order: str = Query("asc"),
    ) -> None:
        if sort_by is not None and sort_by not in self._allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid sort field '{sort_by}'. "
                    f"Allowed: {', '.join(sorted(self._allowed))}"
                ),
            )

        if sort_order not in {"asc", "desc"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sort_order must be 'asc' or 'desc'.",
            )
