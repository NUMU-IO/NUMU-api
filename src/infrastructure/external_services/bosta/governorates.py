"""Back-compat shim: re-exports from `src.core.value_objects.geography`.

Canonical governorate data now lives in the domain layer. Existing code
that imports from this module continues to work unchanged — the symbols
below are the same objects re-exported.

Deprecated. New code should import from:

    from src.core.value_objects.geography import (
        LogisticsZone, Governorate, EGYPTIAN_GOVERNORATES, ...
    )
"""

from src.core.value_objects.geography import (
    EGYPTIAN_GOVERNORATES,
    Governorate,
    LogisticsZone,
    ShippingZone,  # alias = LogisticsZone, preserved for existing imports
    get_all_governorates_dict,
    get_governorate_by_code,
    get_governorate_by_name,
    get_governorates_by_zone,
)

__all__ = [
    "EGYPTIAN_GOVERNORATES",
    "Governorate",
    "LogisticsZone",
    "ShippingZone",
    "get_all_governorates_dict",
    "get_governorate_by_code",
    "get_governorate_by_name",
    "get_governorates_by_zone",
]
