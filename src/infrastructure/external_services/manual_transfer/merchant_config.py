"""Read/write the merchant's manual-rail config in ``store.settings``.

One implementation for both rails. The route layer owns HTTP concerns
(auth, response models, status codes); everything about *what* a valid
config block looks like — which field is the secret, what carries
forward on a partial update, which knobs are merchant-tunable — lives
here so InstaPay and Vodafone Cash can never drift apart.

Shape of ``store.settings["payment"][<method>]``::

    {
      "enabled": bool,               # surfaced at checkout
      "is_configured": bool,         # a destination is stored
      "last_configured": iso8601,
      "encrypted_credentials": b64,  # {destination, fallback_phone}
      "encryption_key_id": str,
      "display_name": str | None,    # "Vionne Store" next to the number
      "ipa_display_name": str | None,# InstaPay-era alias of the above
      "auto_approve_*": int,         # thresholds
      "qr_image_url" / "qr_link_url",# InstaPay only
      "ocr_provider": str | None,    # admin-managed, never merchant-set
      "require_*": bool,             # opt-in auto-approval cross-checks
      "recipient_name_token": str | None,
    }

The destination is encrypted at rest even though every checkout
customer sees it. That is deliberate: a swapped IPA or wallet number is
how you steal a merchant's takings, so it gets the same protection as a
gateway key rather than sitting in plaintext JSONB.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.core.entities.instapay import ManualPaymentMethod
from src.infrastructure.external_services.manual_transfer.destinations import (
    InvalidDestinationError,
    mask_destination,
    normalize_destination,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
    default_amount_tolerance_bps,
    default_auto_approve_enabled,
    human_name,
)


class ManualConfigError(Exception):
    """A config write the merchant must fix. Carries the HTTP status.

    Kept as its own type (rather than raising ``HTTPException`` here)
    so this module stays importable from Celery tasks and tests without
    dragging FastAPI into them.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ManualConfigInput:
    """Merchant-supplied config for one rail.

    ``destination`` and ``fallback_phone`` are optional so the merchant
    can edit a display name or a threshold without re-typing a value
    the UI only ever shows masked. On a first-time save the handler
    rejects a missing destination.
    """

    destination: str | None = None
    fallback_phone: str | None = None
    display_name: str | None = None
    # ``None`` means "leave at the rail's default" — which is how an
    # existing block with no stored value is read, so turning this on
    # for Vodafone Cash is always a deliberate merchant action.
    auto_approve_enabled: bool | None = None
    auto_approve_threshold_cents: int = DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS
    auto_approve_daily_cap_cents: int = DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS
    auto_approve_daily_count: int = DEFAULT_AUTO_APPROVE_DAILY_COUNT
    # ``None`` leaves unchanged, "" clears, any value overwrites.
    # InstaPay only — ignored on rails without a QR.
    qr_link_url: str | None = None
    require_ocr_amount_match: bool = False
    # Historical name; means "the OCR'd recipient identifier must match
    # the merchant's destination" — IPA on InstaPay, wallet number on
    # Vodafone Cash. See manual_transfer/auto_approval.py.
    require_ocr_ipa_match: bool = False
    ocr_amount_tolerance_bps: int | None = None
    require_note_contains_reference: bool = False
    require_transaction_ref_match: bool = False
    require_recipient_name_match: bool = False
    recipient_name_token: str | None = None


def supports_qr(method: ManualPaymentMethod) -> bool:
    """Whether this rail has a scannable code to configure."""
    return method is ManualPaymentMethod.INSTAPAY


async def build_config_block(
    *,
    method: ManualPaymentMethod,
    existing: dict[str, Any] | None,
    data: ManualConfigInput,
) -> tuple[dict[str, Any], str]:
    """Return ``(new settings block, normalized destination)``.

    Raises :class:`ManualConfigError` for anything the merchant can fix:
    a missing first-time destination (400), a malformed one (400), or a
    previously-stored blob that no longer decrypts (409 — we make them
    re-enter rather than silently corrupt the record).
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    existing = existing or {}
    label = human_name(method)

    destination = data.destination
    phone = data.fallback_phone

    # Partial update: pull whichever field the merchant omitted out of
    # the previously-encrypted blob.
    #
    # Only when there IS a previous blob. The InstaPay handler this
    # generalizes treated "any field omitted" as "must be an update",
    # so a first-time save that left out the optional fallback_phone
    # was rejected with a misleading "IPA is required" 400. Missing
    # optional fields on a first save are simply null.
    if (destination is None or phone is None) and existing.get("encrypted_credentials"):
        try:
            prev = await secrets.decrypt(
                base64.b64decode(existing["encrypted_credentials"]),
                existing["encryption_key_id"],
            )
        except Exception as exc:
            raise ManualConfigError(
                409,
                f"Could not read your existing {label} settings. "
                f"Please re-enter your {_destination_noun(method)} to save.",
            ) from exc
        if destination is None:
            destination = (
                prev.get("destination") or prev.get("ipa") or prev.get("wallet_number")
            )
        if phone is None:
            phone = prev.get("fallback_phone")

    if not destination:
        raise ManualConfigError(400, _first_save_required_message(method))

    try:
        destination = normalize_destination(method, destination)
    except InvalidDestinationError as exc:
        raise ManualConfigError(400, str(exc)) from exc

    key_id = await secrets.get_current_key_id()
    encrypted = await secrets.encrypt(
        {
            # ``destination`` is the canonical key. The rail-specific
            # aliases are written too so a rollback to code that only
            # knows ``ipa`` still reads a working value.
            "destination": destination,
            "ipa": destination if method is ManualPaymentMethod.INSTAPAY else None,
            "wallet_number": (
                destination if method is ManualPaymentMethod.VODAFONE_CASH else None
            ),
            "fallback_phone": phone,
        },
        key_id,
    )

    tolerance = data.ocr_amount_tolerance_bps
    if tolerance is None:
        tolerance = default_amount_tolerance_bps(method)

    block: dict[str, Any] = {
        # Preserve the current enabled state on credential updates — a
        # merchant editing thresholds shouldn't accidentally unmute a
        # rail they deliberately switched off at checkout. A first save
        # enables it: you don't type in your wallet number to keep it
        # hidden.
        #
        # "First save" is keyed on there being no stored credentials,
        # NOT on `existing` being empty. Every store carries a default
        # `{"enabled": False, "is_configured": False}` block from
        # `_get_default_payment_settings()`, so the older
        # `if existing else True` form left a freshly-configured rail
        # disabled and made the merchant hunt for a second toggle —
        # the opposite of what its comment claimed.
        "enabled": (
            bool(existing.get("enabled", True))
            if existing.get("encrypted_credentials")
            else True
        ),
        "is_configured": True,
        "last_configured": datetime.now(UTC).isoformat(),
        "encrypted_credentials": base64.b64encode(encrypted).decode("ascii"),
        "encryption_key_id": key_id,
        "display_name": data.display_name,
        # InstaPay-era alias, kept populated so older readers of the
        # settings blob (and the InstaPay response model) still work.
        "ipa_display_name": data.display_name,
        "auto_approve_enabled": (
            data.auto_approve_enabled
            if data.auto_approve_enabled is not None
            else bool(
                existing.get(
                    "auto_approve_enabled", default_auto_approve_enabled(method)
                )
            )
        ),
        "auto_approve_threshold_cents": data.auto_approve_threshold_cents,
        "auto_approve_daily_cap_cents": data.auto_approve_daily_cap_cents,
        "auto_approve_daily_count": data.auto_approve_daily_count,
        # The OCR provider is admin-managed and intentionally NOT read
        # from the merchant request, so a merchant can't self-promote
        # onto a paid tier.
        "ocr_provider": existing.get("ocr_provider"),
        "require_ocr_amount_match": data.require_ocr_amount_match,
        "require_ocr_ipa_match": data.require_ocr_ipa_match,
        "ocr_amount_tolerance_bps": tolerance,
        "require_note_contains_reference": data.require_note_contains_reference,
        "require_transaction_ref_match": data.require_transaction_ref_match,
        "require_recipient_name_match": data.require_recipient_name_match,
        "recipient_name_token": (
            data.recipient_name_token.strip() if data.recipient_name_token else None
        ),
        # Per-store pHash dedup radius — set elsewhere, never clobbered.
        **(
            {"perceptual_dedup_max_distance": existing["perceptual_dedup_max_distance"]}
            if "perceptual_dedup_max_distance" in existing
            else {}
        ),
    }

    if supports_qr(method):
        # The QR image is uploaded via a dedicated endpoint, so a
        # credentials write must not erase a previously-uploaded URL.
        block["qr_image_url"] = existing.get("qr_image_url")
        if data.qr_link_url is None:
            block["qr_link_url"] = existing.get("qr_link_url")
        else:
            block["qr_link_url"] = data.qr_link_url.strip() or None

    return block, destination


def cleared_config_block() -> dict[str, Any]:
    """The block written when a merchant removes their config.

    Existing intents are deliberately left alone — they belong to orders
    already placed, and the merchant still needs to review their proofs.
    New orders simply can no longer choose the rail.
    """
    return {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }


async def read_config_view(
    *,
    method: ManualPaymentMethod,
    block: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the masked, merchant-facing view of a stored config block.

    Never raises on a bad blob: an undecryptable record still reports
    ``is_configured`` so the dashboard can tell the merchant to re-save
    instead of rendering an empty form that looks like data loss.
    """
    block = block or {}
    if not block.get("encrypted_credentials"):
        return {"is_configured": False, "enabled": False}

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    destination = ""
    try:
        creds = await secrets.decrypt(
            base64.b64decode(block["encrypted_credentials"]),
            block["encryption_key_id"],
        )
        destination = (
            creds.get("destination")
            or creds.get("ipa")
            or creds.get("wallet_number")
            or ""
        )
        fallback_phone = creds.get("fallback_phone")
    except Exception:
        return {
            "is_configured": True,
            "enabled": bool(block.get("enabled")),
            "last_configured": block.get("last_configured"),
            "unreadable": True,
        }

    view: dict[str, Any] = {
        "is_configured": True,
        "enabled": bool(block.get("enabled")),
        "destination_masked": mask_destination(method, destination),
        "display_name": block.get("display_name") or block.get("ipa_display_name"),
        "fallback_phone": fallback_phone,
        "auto_approve_enabled": bool(
            block.get("auto_approve_enabled", default_auto_approve_enabled(method))
        ),
        "auto_approve_threshold_cents": block.get("auto_approve_threshold_cents"),
        "auto_approve_daily_cap_cents": block.get("auto_approve_daily_cap_cents"),
        "auto_approve_daily_count": block.get("auto_approve_daily_count"),
        "last_configured": block.get("last_configured"),
        "ocr_provider": block.get("ocr_provider"),
        "require_ocr_amount_match": bool(block.get("require_ocr_amount_match", False)),
        "require_ocr_ipa_match": bool(block.get("require_ocr_ipa_match", False)),
        "ocr_amount_tolerance_bps": int(
            block.get("ocr_amount_tolerance_bps")
            or default_amount_tolerance_bps(method)
        ),
        "require_note_contains_reference": bool(
            block.get("require_note_contains_reference", False)
        ),
        "require_transaction_ref_match": bool(
            block.get("require_transaction_ref_match", False)
        ),
        "require_recipient_name_match": bool(
            block.get("require_recipient_name_match", False)
        ),
        "recipient_name_token": block.get("recipient_name_token"),
    }
    if supports_qr(method):
        view["qr_image_url"] = block.get("qr_image_url")
        view["qr_link_url"] = block.get("qr_link_url")
    return view


def _destination_noun(method: ManualPaymentMethod) -> str:
    if method is ManualPaymentMethod.VODAFONE_CASH:
        return "wallet number"
    return "InstaPay address (IPA)"


def _first_save_required_message(method: ManualPaymentMethod) -> str:
    if method is ManualPaymentMethod.VODAFONE_CASH:
        return (
            "A Vodafone Cash wallet number is required for the first save "
            "(e.g. 01012345678)."
        )
    return "InstaPay address (IPA) is required for the first save."
