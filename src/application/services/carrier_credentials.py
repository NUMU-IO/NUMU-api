"""Per-store carrier credentials — one implementation, not one per carrier.

Each provider shipped its own copy of "read
``store.settings.shipping.<carrier>``, base64-decode, decrypt, construct".
Three copies had already drifted: Bosta's raised on a decrypt failure and
logged, Mylerz's and J&T's let the exception escape, and only Bosta
guarded a missing ``encryption_key_id``. A fourth carrier would have been
a fourth copy.

This module owns reading, validating and writing carrier credentials.
The credential *shape* comes from the registry's ``credential_fields``,
so a new carrier declares its fields once and gets storage, validation
and the hub's connect-form for free.

**Credentials never leave here in the clear.** ``describe_credentials``
reports only whether each field is set, so a settings response can show
"connected" without ever echoing a key back to the browser.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P1.4.
"""

import base64
from typing import Any

from src.application.services.carrier_registry import CarrierSpec
from src.application.services.carrier_resolver import (
    UnknownCarrierError,
    spec_for,
)
from src.core.logging import get_logger

logger = get_logger(__name__)

#: Where a store keeps carrier config inside ``store.settings``.
SHIPPING_KEY = "shipping"
ENCRYPTED_KEY = "encrypted_credentials"
KEY_ID_KEY = "encryption_key_id"


def carrier_settings(store_settings: dict | None, carrier: str) -> dict[str, Any]:
    """The stored settings block for one carrier. Never None."""
    block = (store_settings or {}).get(SHIPPING_KEY, {})
    if not isinstance(block, dict):
        return {}
    entry = block.get(carrier, {})
    return entry if isinstance(entry, dict) else {}


def has_credentials(store_settings: dict | None, carrier: str) -> bool:
    """Whether this store has encrypted credentials stored for a carrier.

    Both halves are required: ciphertext without its key id cannot be
    decrypted, and treating that as "configured" is what let a store show
    a green "connected" badge it could never actually use.
    """
    entry = carrier_settings(store_settings, carrier)
    return bool(entry.get(ENCRYPTED_KEY)) and bool(entry.get(KEY_ID_KEY))


async def load_credentials(
    store_settings: dict | None, carrier: str
) -> dict[str, Any] | None:
    """Decrypt a store's credentials for a carrier, or None.

    Returns None — never raises — when the store simply hasn't connected
    the carrier, so callers can fall back to platform-level env
    credentials. A decrypt *failure* is different: it means stored data
    we cannot read, so it is logged loudly and still returns None rather
    than crashing a merchant's shipment.
    """
    entry = carrier_settings(store_settings, carrier)
    encrypted = entry.get(ENCRYPTED_KEY)
    key_id = entry.get(KEY_ID_KEY)

    if not encrypted or not key_id:
        return None

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    try:
        manager = get_secrets_manager()
        return await manager.decrypt(base64.b64decode(encrypted), key_id)
    except Exception as e:
        # Deliberately not re-raised: an unreadable secret must not take
        # down shipment creation for the whole store. The caller falls
        # back to platform credentials and this line is the breadcrumb.
        logger.error(
            "carrier_credentials_decrypt_failed",
            carrier=carrier,
            key_id=key_id,
            error=str(e),
        )
        return None


def validate_credentials(
    carrier: str, values: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Check submitted credentials against the carrier's declared fields.

    Returns the cleaned values (unknown keys dropped) and a list of
    missing required field keys. Unknown keys are dropped rather than
    stored: a typo'd field name would otherwise sit encrypted forever,
    looking configured, and never be read by the provider.
    """
    spec = spec_for(carrier)
    declared = {f.key: f for f in spec.credential_fields}

    cleaned: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key in declared and value not in (None, ""):
            cleaned[key] = value.strip() if isinstance(value, str) else value

    missing = [
        f.key for f in spec.credential_fields if f.required and f.key not in cleaned
    ]
    return cleaned, missing


async def store_credentials(
    store_settings: dict | None, carrier: str, values: dict[str, Any]
) -> dict[str, Any]:
    """Encrypt and write credentials into a copy of ``store.settings``.

    Returns the updated settings dict for the caller to persist; does not
    touch the database itself.

    Raises:
        UnknownCarrierError: no registry entry for this slug.
        ValueError: a required credential field is missing.
    """
    spec = spec_for(carrier)
    cleaned, missing = validate_credentials(carrier, values)
    if missing:
        raise ValueError(
            f"Missing required credentials for {spec.slug}: {', '.join(missing)}"
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    manager = get_secrets_manager()
    key_id = await manager.get_current_key_id()
    ciphertext = await manager.encrypt(cleaned, key_id)

    settings = dict(store_settings or {})
    shipping = dict(settings.get(SHIPPING_KEY, {}))
    entry = dict(shipping.get(carrier, {}))
    entry.update({
        ENCRYPTED_KEY: base64.b64encode(ciphertext).decode(),
        KEY_ID_KEY: key_id,
        "is_configured": True,
    })
    shipping[carrier] = entry
    settings[SHIPPING_KEY] = shipping
    return settings


def clear_credentials(store_settings: dict | None, carrier: str) -> dict[str, Any]:
    """Remove a carrier's stored credentials, keeping the rest intact.

    Also clears ``is_configured`` and ``enabled`` — leaving a carrier
    enabled with no credentials is how a store ends up showing a live
    badge for something that cannot authenticate.
    """
    settings = dict(store_settings or {})
    shipping = dict(settings.get(SHIPPING_KEY, {}))
    entry = dict(shipping.get(carrier, {}))
    for key in (ENCRYPTED_KEY, KEY_ID_KEY):
        entry.pop(key, None)
    entry["is_configured"] = False
    entry["enabled"] = False
    shipping[carrier] = entry
    settings[SHIPPING_KEY] = shipping
    return settings


def describe_credentials(store_settings: dict | None, carrier: str) -> dict[str, Any]:
    """Connection status for the hub. **Never returns secret values.**

    Reports which declared fields are present so the UI can render
    "API key ✓ / Merchant ID missing" without the browser ever receiving
    a key.
    """
    try:
        spec: CarrierSpec = spec_for(carrier)
    except UnknownCarrierError:
        return {"carrier": carrier, "is_configured": False, "fields": {}}

    entry = carrier_settings(store_settings, carrier)
    return {
        "carrier": spec.slug,
        "is_configured": has_credentials(store_settings, carrier),
        "enabled": bool(entry.get("enabled", False)),
        "last_configured": entry.get("last_configured"),
        # Presence only — deliberately not the values.
        "fields": {
            f.key: {"required": f.required, "secret": f.secret}
            for f in spec.credential_fields
        },
    }


__all__ = [
    "carrier_settings",
    "clear_credentials",
    "describe_credentials",
    "has_credentials",
    "load_credentials",
    "store_credentials",
    "validate_credentials",
]
