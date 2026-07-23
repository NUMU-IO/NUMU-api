"""Legacy omnichannel CAPI route — RETIRED (410 Gone).

This predates the Settings → Tracking pipeline and was dangerous to keep
callable:

  * it read flat, UNENCRYPTED ``settings.meta_pixel_id`` /
    ``meta_capi_token`` — a config shape the modern settings PUT no longer
    writes (tokens now live encrypted in ``service_credentials``);
  * it hashed only 5 user_data fields and passed no fbp/fbc/ip/ua, so any
    event it sent had bottom-tier match quality;
  * its purchase path put raw CENTS into ``value`` with no /100, so a
    single call would report 100× revenue to Meta.

Nothing has called it (grep across api/hub/storefront/SDK: zero callers);
the modern path is the storefront ``/track`` relay + ``meta_capi`` Celery
fanout. The route is kept mounted as an explicit 410 so any stale
integration fails loudly instead of silently sending wrong-value events.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["Omnichannel"])


@router.post("/event", status_code=status.HTTP_410_GONE, include_in_schema=False)
async def send_capi_event() -> None:
    """Retired. Use the storefront tracking pipeline (`/storefront/store/{id}/track`)."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "This endpoint has been retired. Conversion events are sent via "
            "the storefront tracking pipeline (Settings → Tracking)."
        ),
    )


__all__ = ["router"]
