"""Sparse fieldsets dependency for API response optimization.

This module implements JSON:API-style sparse fieldsets, allowing clients
to request only specific fields via ?fields=id,name,price query parameter.

3G Optimization Benefits:
- Full product response: ~500 bytes
- Sparse response (?fields=id,name,price,images): ~150 bytes
- Up to 70% reduction in payload size

Usage:
    @router.get("/products")
    async def list_products(
        field_selector: FieldSelector = Depends(get_product_field_selector),
        fields: str | None = Query(None, description="Comma-separated fields")
    ):
        products = await fetch_products()
        return [field_selector.filter(p, fields) for p in products]

Security:
- Whitelist validation prevents access to undeclared fields
- Sensitive fields are explicitly blocked
- Input validation prevents injection attacks
"""

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Query

# Regex pattern for valid field names (alphanumeric and underscores)
FIELD_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass
class FieldsetConfig:
    """Configuration for sparse fieldsets.

    Attributes:
        allowed_fields: Set of fields that can be requested
        default_fields: Fields returned when no ?fields param is provided
        sensitive_fields: Fields that should never be exposed (even if in allowed)
    """

    allowed_fields: set[str]
    default_fields: set[str]
    sensitive_fields: set[str] = field(default_factory=set)

    def __post_init__(self):
        """Validate configuration."""
        # Ensure default fields are subset of allowed fields
        invalid_defaults = self.default_fields - self.allowed_fields
        if invalid_defaults:
            raise ValueError(f"Default fields {invalid_defaults} not in allowed fields")
        # Ensure sensitive fields are not in default fields
        exposed_sensitive = self.default_fields & self.sensitive_fields
        if exposed_sensitive:
            raise ValueError(
                f"Sensitive fields {exposed_sensitive} cannot be in default fields"
            )


class FieldSelector:
    """Handles sparse fieldset parsing, validation, and filtering.

    Thread-safe and reusable across requests.
    """

    def __init__(self, config: FieldsetConfig):
        """Initialize with fieldset configuration.

        Args:
            config: FieldsetConfig defining allowed, default, and sensitive fields
        """
        self.config = config

    def parse_fields(self, fields_param: str | None) -> set[str]:
        """Parse and validate the fields query parameter.

        Args:
            fields_param: Comma-separated list of field names

        Returns:
            Set of validated field names to include in response

        Raises:
            HTTPException: If fields are invalid or contain sensitive fields
        """
        if not fields_param:
            return self.config.default_fields

        # Parse comma-separated fields
        requested = {f.strip() for f in fields_param.split(",") if f.strip()}

        # Validate each field name format
        for field_name in requested:
            if not self._is_valid_field_name(field_name):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid field name format: {field_name}",
                )

        # Security: Block access to sensitive fields
        attempted_sensitive = requested & self.config.sensitive_fields
        if attempted_sensitive:
            # Don't reveal which fields are sensitive
            raise HTTPException(
                status_code=400,
                detail="One or more requested fields are not available",
            )

        # Validate against whitelist
        unknown_fields = requested - self.config.allowed_fields
        if unknown_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown fields: {sorted(unknown_fields)}. "
                f"Available fields: {sorted(self.config.allowed_fields)}",
            )

        return requested

    def filter_dict(self, data: dict[str, Any], fields: set[str]) -> dict[str, Any]:
        """Filter a dictionary to only include specified fields.

        Args:
            data: Dictionary to filter
            fields: Set of field names to include

        Returns:
            Filtered dictionary containing only specified fields
        """
        return {k: v for k, v in data.items() if k in fields}

    def filter_model(self, model: Any, fields: set[str]) -> dict[str, Any]:
        """Filter a Pydantic model to only include specified fields.

        Args:
            model: Pydantic model instance
            fields: Set of field names to include

        Returns:
            Dictionary containing only specified fields
        """
        if hasattr(model, "model_dump"):
            # Pydantic v2
            return model.model_dump(include=fields)
        elif hasattr(model, "dict"):
            # Pydantic v1 fallback
            data = model.dict()
            return {k: v for k, v in data.items() if k in fields}
        else:
            # Assume it's a dict-like object
            return self.filter_dict(dict(model), fields)

    def _is_valid_field_name(self, field_name: str) -> bool:
        """Check if field name matches allowed pattern.

        Args:
            field_name: Field name to validate

        Returns:
            True if field name is valid, False otherwise
        """
        return bool(FIELD_NAME_PATTERN.match(field_name)) and len(field_name) <= 50


# =============================================================================
# Product Field Configuration
# =============================================================================

PRODUCT_FIELDSET_CONFIG = FieldsetConfig(
    allowed_fields={
        # Basic info
        "id",
        "store_id",
        "name",
        "slug",
        "sku",
        "description",
        "short_description",
        "product_type",
        "status",
        # Pricing
        "price",
        "price_currency",
        "compare_at_price",
        "is_on_sale",
        # Stock
        "quantity",
        "is_in_stock",
        "is_low_stock",
        # Media & categorization
        "images",
        "category_id",
        "tags",
        "attributes",
        # Timestamps
        "created_at",
        "updated_at",
    },
    # Mobile-optimized defaults: minimal fields for list views
    default_fields={
        "id",
        "name",
        "slug",
        "price",
        "price_currency",
        "compare_at_price",
        "is_on_sale",
        "is_in_stock",
        "images",
    },
    # Fields that should never be exposed in storefront
    sensitive_fields={
        "cost_price",
        "low_stock_threshold",
        "supplier_id",
        "internal_notes",
    },
)

# Full fields for detail view (more fields than list view default)
PRODUCT_DETAIL_FIELDS = {
    "id",
    "store_id",
    "name",
    "slug",
    "sku",
    "description",
    "short_description",
    "product_type",
    "status",
    "price",
    "price_currency",
    "compare_at_price",
    "is_on_sale",
    "quantity",
    "is_in_stock",
    "is_low_stock",
    "images",
    "category_id",
    "tags",
    "attributes",
    "created_at",
    "updated_at",
}


# =============================================================================
# Dependency Functions
# =============================================================================


def get_product_field_selector() -> FieldSelector:
    """Get FieldSelector for product resources.

    Returns:
        FieldSelector configured for products
    """
    return FieldSelector(PRODUCT_FIELDSET_CONFIG)


def fields_query(
    description: str = "Comma-separated list of fields to include in response",
    example: str = "id,name,price,images",
) -> str | None:
    """Query parameter for sparse fieldsets.

    Usage:
        @router.get("/products")
        async def list_products(
            fields: str | None = Depends(fields_query())
        ):
            ...
    """
    return Query(
        None,
        description=description,
        example=example,
        max_length=500,
        pattern=r"^[a-zA-Z_,]+$",
    )
