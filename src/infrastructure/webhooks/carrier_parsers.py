"""Per-carrier webhook payload parsers.

Everything *around* parsing a carrier webhook was identical in
``webhooks/{bosta,jt,mylerz}.py``: find the shipment by tracking number,
derive the tenant, decrypt that store's webhook secret, verify the
signature with a global fallback, apply the status, sync the order. Only
three things actually differ per carrier — the field names in the body,
the signature header, and the status vocabulary.

The status vocabulary already moved to the carrier registry. This module
holds the second piece: a small function per carrier that turns a raw
body into a :class:`WebhookEvent`. The generic route
``POST /webhooks/shipping/{carrier}`` supplies everything else.

Each parser is total — it never raises on a malformed body, and returns
None when it cannot find a tracking number, because a carrier retrying a
body we cannot read should get a clean answer rather than a 500.

These live here rather than on the providers because Bosta, Mylerz and
J&T still implement the old ``IShippingService``. When P4/P6 migrate them
onto ``ShippingProvider``, each parser becomes that class's
``parse_webhook`` and this module goes away.
"""

from typing import Any

from src.core.interfaces.services.shipping_provider import WebhookEvent


def _first(data: dict[str, Any], *keys: str) -> Any:
    """First present, non-empty value among ``keys``.

    Carriers are inconsistent about casing between their docs and what
    they actually send (Mylerz sends ``Barcode``, its docs say
    ``barcode``), so every field is looked up under each spelling the
    previous per-carrier handlers accepted.
    """
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_bosta(data: dict[str, Any]) -> WebhookEvent | None:
    """Bosta nests everything under ``delivery``."""
    if not isinstance(data, dict):
        return None
    delivery = data.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}

    tracking = _first(delivery, "trackingNumber", "tracking_number") or _first(
        data, "trackingNumber", "tracking_number"
    )
    if not tracking:
        return None

    state = delivery.get("state")
    raw_status = ""
    if isinstance(state, dict):
        raw_status = str(state.get("value") or "")
    elif state:
        raw_status = str(state)

    cod = delivery.get("cod")
    cod_amount = _to_float(cod.get("amount")) if isinstance(cod, dict) else None

    return WebhookEvent(
        tracking_number=str(tracking),
        status=None,  # mapped by the caller via the registry
        raw_status=raw_status,
        description=str(_first(delivery, "description", "reason") or ""),
        cod_collected=bool(cod_amount),
        cod_amount=cod_amount,
        occurred_at=_first(delivery, "updatedAt", "timestamp"),
        payload=data,
    )


def parse_mylerz(data: dict[str, Any]) -> WebhookEvent | None:
    """Mylerz sends a flat body with PascalCase keys."""
    if not isinstance(data, dict):
        return None

    tracking = _first(data, "Barcode", "barcode", "tracking_number")
    if not tracking:
        return None

    cod_amount = _to_float(_first(data, "CODAmount", "cod_amount"))
    return WebhookEvent(
        tracking_number=str(tracking),
        status=None,
        raw_status=str(_first(data, "Status", "status") or ""),
        description=str(_first(data, "Description", "description", "Notes") or ""),
        cod_collected=bool(cod_amount),
        cod_amount=cod_amount,
        occurred_at=_first(data, "UpdatedAt", "updated_at", "timestamp"),
        payload=data,
    )


def parse_jt(data: dict[str, Any]) -> WebhookEvent | None:
    """J&T identifies the parcel by ``billCode`` and the event by ``scanType``."""
    if not isinstance(data, dict):
        return None

    tracking = _first(data, "billCode", "billcode", "tracking_number")
    if not tracking:
        return None

    cod_amount = _to_float(_first(data, "codAmount", "cod_amount"))
    return WebhookEvent(
        tracking_number=str(tracking),
        status=None,
        raw_status=str(_first(data, "scanType", "status") or ""),
        description=str(_first(data, "desc", "description", "remark") or ""),
        cod_collected=bool(cod_amount),
        cod_amount=cod_amount,
        occurred_at=_first(data, "scanTime", "timestamp"),
        payload=data,
    )


#: Registry slug → parser. Referenced by the carrier registry.
PARSERS = {
    "bosta": parse_bosta,
    "mylerz": parse_mylerz,
    "jt": parse_jt,
}


__all__ = ["PARSERS", "parse_bosta", "parse_jt", "parse_mylerz"]
