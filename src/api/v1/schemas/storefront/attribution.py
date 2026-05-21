"""Attribution request schema for the storefront wire.

Re-exports the domain value objects from ``src.core.entities.attribution``
so route handlers in ``api/v1/routes/storefront/`` can import the same
shape that gets persisted to the DB without reaching into ``core``.

No additional validators here — the per-field max_length and the
envelope size cap (4 KB) are enforced inside ``AttributionSnapshot`` /
``AttributionTouch`` themselves.
"""

from src.core.entities.attribution import (
    ATTRIBUTION_SCHEMA_VERSION,
    AttributionSnapshot,
    AttributionTouch,
)

__all__ = [
    "ATTRIBUTION_SCHEMA_VERSION",
    "AttributionSnapshot",
    "AttributionTouch",
]
