"""Storefront reverse-geocoding proxy.

URL: GET /storefront/store/{store_id}/geocode/reverse?lat=...&lng=...&lang=ar

The storefront checkout location picker calls this to translate the
map-pin coordinates into a structured Egyptian address (governorate,
area, street) that can autofill the delivery-details form.

We proxy through the backend instead of hitting the geocoder from the
browser so we can:
  1. Hide the upstream API key.
  2. Cache results in Redis (rounded coords → result, 30d TTL) —
     dense urban areas see ~80% cache hit, keeping cost down.
  3. Reject coords outside Egypt before burning any upstream quota.
  4. Normalize the provider response to a provider-agnostic shape,
     so swapping LocationIQ → self-hosted Nominatim later is a
     one-line settings change.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from src.api.responses import SuccessResponse
from src.config import settings
from src.infrastructure.cache.redis_cache import RedisCacheService

router = APIRouter()

# Egypt bounding box (generous — covers Sinai, Western Desert, Nubia).
EGYPT_BBOX_LAT = (22.0, 32.0)
EGYPT_BBOX_LNG = (24.5, 37.0)

# Round input coords to this many decimals for cache key → ~11m precision
# at the equator. Two clicks on the same building share a cache entry.
CACHE_COORD_PRECISION = 4
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days

# Map OSM governorate names (English + Arabic variants) onto the slugs
# used by the storefront checkout governorate dropdown.
_GOVERNORATE_SLUGS: dict[str, str] = {
    # English
    "cairo": "Cairo",
    "cairo governorate": "Cairo",
    "giza": "Giza",
    "giza governorate": "Giza",
    "alexandria": "Alexandria",
    "alexandria governorate": "Alexandria",
    "dakahlia": "Mansoura",  # capital of Dakahlia is Mansoura
    "dakahlia governorate": "Mansoura",
    "mansoura": "Mansoura",
    "gharbia": "Tanta",  # capital of Gharbia is Tanta
    "gharbia governorate": "Tanta",
    "tanta": "Tanta",
    "asyut": "Asyut",
    "asyut governorate": "Asyut",
    "assiut": "Asyut",
    "sohag": "Sohag",
    "sohag governorate": "Sohag",
    # Arabic
    "القاهرة": "Cairo",
    "محافظة القاهرة": "Cairo",
    "الجيزة": "Giza",
    "محافظة الجيزة": "Giza",
    "الإسكندرية": "Alexandria",
    "محافظة الإسكندرية": "Alexandria",
    "الدقهلية": "Mansoura",
    "المنصورة": "Mansoura",
    "الغربية": "Tanta",
    "طنطا": "Tanta",
    "أسيوط": "Asyut",
    "سوهاج": "Sohag",
}


def _resolve_governorate_slug(state: str | None) -> str:
    """Map an OSM state/governorate name onto the checkout dropdown slug."""
    if not state:
        return "Other"
    normalized = state.strip().lower()
    return _GOVERNORATE_SLUGS.get(normalized, "Other")


class GeocodeResult(BaseModel):
    """Normalized reverse-geocode response."""

    formatted_address: str | None = Field(
        None, description="Provider-normalized formatted address"
    )
    city: str | None = Field(
        None, description="Governorate name as returned by the provider"
    )
    city_code: str = Field(
        description=(
            "Governorate slug matching the checkout dropdown options "
            "('Cairo' | 'Giza' | 'Alexandria' | 'Mansoura' | 'Tanta' | "
            "'Asyut' | 'Sohag' | 'Other')."
        )
    )
    area: str | None = Field(None, description="Neighborhood / district / suburb")
    street: str | None = Field(
        None, description="Street name and house number if available"
    )
    country_code: str | None = Field(None, description="ISO 3166-1 alpha-2 code")
    latitude: float = Field(description="Echo of the input latitude")
    longitude: float = Field(description="Echo of the input longitude")
    provider: str = Field(
        description="Upstream provider used: 'nominatim' | 'locationiq'"
    )


_cache_service: RedisCacheService | None = None


def _get_cache() -> RedisCacheService | None:
    """Lazily instantiate the cache service (only if Redis is configured)."""
    global _cache_service
    if _cache_service is None and settings.redis_host:
        _cache_service = RedisCacheService()
    return _cache_service


def _in_egypt_bbox(lat: float, lng: float) -> bool:
    return (
        EGYPT_BBOX_LAT[0] <= lat <= EGYPT_BBOX_LAT[1]
        and EGYPT_BBOX_LNG[0] <= lng <= EGYPT_BBOX_LNG[1]
    )


def _cache_key(lat: float, lng: float, lang: str) -> str:
    lat_r = round(lat, CACHE_COORD_PRECISION)
    lng_r = round(lng, CACHE_COORD_PRECISION)
    return f"geo:rev:{lat_r}:{lng_r}:{lang}"


def _normalize_response(raw: dict[str, Any], lat: float, lng: float) -> GeocodeResult:
    """Normalize Nominatim/LocationIQ response to our envelope.

    Both providers share the same output schema since LocationIQ wraps
    Nominatim internally.
    """
    address = raw.get("address") or {}
    display_name = raw.get("display_name")

    # Area precedence: neighborhood > suburb > city_district > quarter > town
    area = (
        address.get("neighbourhood")
        or address.get("suburb")
        or address.get("city_district")
        or address.get("quarter")
        or address.get("town")
        or address.get("village")
    )
    # Street: build "house_number road" when both present, else just road.
    house_number = address.get("house_number")
    road = address.get("road")
    street: str | None
    if house_number and road:
        street = f"{road} {house_number}"
    else:
        street = road

    # Governorate lives in `state` for Egypt in Nominatim output.
    state = address.get("state")
    city_display = address.get("city") or state  # fallback when city absent
    city_code = _resolve_governorate_slug(state or city_display)

    return GeocodeResult(
        formatted_address=display_name,
        city=city_display,
        city_code=city_code,
        area=area,
        street=street,
        country_code=(address.get("country_code") or "").upper() or None,
        latitude=lat,
        longitude=lng,
        provider="locationiq" if settings.locationiq_key else "nominatim",
    )


async def _call_upstream(lat: float, lng: float, lang: str) -> dict[str, Any] | None:
    """Call the configured reverse-geocoder.

    Returns None if the upstream is unconfigured or fails; callers should
    treat None as "no geocode available" rather than an error, so the
    storefront can still proceed with manual entry.
    """
    base_url = settings.nominatim_url
    if not base_url:
        return None

    params: dict[str, str | float | int] = {
        "lat": lat,
        "lon": lng,
        "format": "json",
        "accept-language": lang,
        "zoom": 18,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "NUMU/1.0 (+https://numu.store)"}
    # Both self-hosted Nominatim and LocationIQ expose /reverse with the
    # same query shape; LocationIQ just additionally requires a `key`.
    if settings.locationiq_key:
        params["key"] = settings.locationiq_key
    url = f"{base_url.rstrip('/')}/reverse"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "error" not in data:
                return data
            return None
    except (httpx.HTTPError, ValueError):
        # Upstream is soft — a failure degrades to manual entry, not a 500.
        return None


@router.get(
    "/geocode/reverse",
    response_model=SuccessResponse[GeocodeResult],
    summary="Reverse-geocode a coordinate to an Egyptian address",
    operation_id="storefront_reverse_geocode",
)
async def reverse_geocode(
    store_id: Annotated[UUID, Path(description="Store ID")],  # noqa: ARG001
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude (WGS84)")],
    lng: Annotated[float, Query(ge=-180, le=180, description="Longitude (WGS84)")],
    lang: Annotated[str, Query(max_length=5, description="Preferred language")] = "ar",
) -> SuccessResponse[GeocodeResult]:
    # Reject clearly-out-of-country coords before hitting cache or upstream.
    if not _in_egypt_bbox(lat, lng):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Coordinates are outside the supported region.",
        )

    if not settings.nominatim_url:
        # Feature disabled server-side. 503 so the client can fall back
        # gracefully to the manual-pin flow without treating it as an error.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reverse geocoding is not configured.",
        )

    cache = _get_cache()
    key = _cache_key(lat, lng, lang)

    if cache is not None:
        cached = await cache.get(key)
        if cached is not None:
            return SuccessResponse(data=GeocodeResult(**cached), message="cache")

    raw = await _call_upstream(lat, lng, lang)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reverse geocoding provider did not return a result.",
        )

    result = _normalize_response(raw, lat, lng)

    if cache is not None:
        await cache.set(key, result.model_dump(), expire=CACHE_TTL_SECONDS)

    return SuccessResponse(data=result, message="ok")
