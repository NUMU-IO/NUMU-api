"""Bosta shipping service for Egypt."""

from src.infrastructure.external_services.bosta.governorates import (
    EGYPTIAN_GOVERNORATES,
    Governorate,
    ShippingZone,
    get_governorate_by_code,
    get_governorate_by_name,
)
from src.infrastructure.external_services.bosta.shipping_service import BostaShippingService

__all__ = [
    "BostaShippingService",
    "EGYPTIAN_GOVERNORATES",
    "Governorate",
    "ShippingZone",
    "get_governorate_by_code",
    "get_governorate_by_name",
]
