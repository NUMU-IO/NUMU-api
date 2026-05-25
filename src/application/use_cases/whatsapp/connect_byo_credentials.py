"""Connect a merchant's own Meta WhatsApp Business Account (BYO; FR-020/021).

Runs the three Meta read calls (research.md R2) that together verify
the credentials carry both required OAuth scopes and that the supplied
``phone_number_id`` belongs to the supplied ``waba_id``:

1. ``GET /{phone_number_id}`` — phone metadata. Confirms access_token
   resolves the phone.
2. ``GET /{waba_id}`` — WABA info. Exercises
   ``whatsapp_business_management`` scope.
3. ``GET /{waba_id}/message_templates?limit=1`` — template list.
   Cross-ID consistency check + exercises the messaging-management
   surface.

All three must succeed before the credentials are persisted. On
success:

- Snapshot the store's current ``whatsapp_notifications`` settings to
  ``whatsapp_notifications_prev_platform_managed`` so a later
  disconnect can restore them (FR-019a).
- Reset ALL ``whatsapp_notifications`` toggles to False — BYO stores
  must explicitly enable each toggle after confirming their templates
  are approved under their own WABA (FR-019a).
- Encrypt and persist credentials via the existing
  ``ServiceCredential`` + ``get_secrets_manager()`` pattern (FR-022).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.services.meta_error_whitelist import sanitize_meta_error
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.external_services.meta.whatsapp_client import (
    WhatsAppClient,
)


class BYOValidationError(Exception):
    """Raised when the 3-step Meta validation fails. Maps to a 422 with
    a typed ``BYOValidationFailure`` body at the API boundary.
    """

    def __init__(
        self,
        *,
        failed_step: str,
        code: str,
        message: str,
        meta_error: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_step = failed_step
        self.code = code
        self.message = message
        # Already-sanitized (whitelisted) — never carries fbtrace_id etc.
        self.meta_error = meta_error

    def to_response_dict(self) -> dict[str, Any]:
        return {
            "failed_step": self.failed_step,
            "code": self.code,
            "message": self.message,
            "meta_error": self.meta_error,
        }


class ConnectBYOCredentialsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        *,
        store_id: UUID,
        access_token: str,
        phone_number_id: str,
        waba_id: str,
        app_secret: str,
    ) -> dict[str, Any]:
        """Validate against Meta + persist + reset toggles. Returns the
        connection-status dict that the route will serialize.
        """
        # 0. Load the store row (and its tenant_id) for the writes.
        store = (
            await self.session.execute(
                select(StoreModel).where(StoreModel.id == store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            raise BYOValidationError(
                failed_step="phone_metadata_read",
                code="unknown",
                message=f"Store {store_id} not found.",
            )

        client = WhatsAppClient(
            phone_number_id=phone_number_id,
            access_token=access_token,
            waba_id=waba_id,
        )
        phone_info: dict[str, Any] | None = None
        try:
            # Step 1: phone metadata
            try:
                phone_info = await client.get_phone_number_info()
            except httpx.HTTPStatusError as exc:
                raise BYOValidationError(
                    failed_step="phone_metadata_read",
                    code="phone_number_unreachable",
                    message=(
                        "Could not read phone number metadata. The access "
                        "token may not have access to this phone number, "
                        "or the phone_number_id is wrong."
                    ),
                    meta_error=_safe_json_error(exc),
                ) from exc

            # Step 2: WABA info (exercises whatsapp_business_management)
            try:
                waba_info = await client.get_waba_info()
            except httpx.HTTPStatusError as exc:
                # 403/401 here typically means missing scope. The token
                # might still resolve the phone (step 1) without having
                # the management scope.
                raise BYOValidationError(
                    failed_step="waba_info_read",
                    code="insufficient_scope",
                    message=(
                        "Access token does not have the "
                        "whatsapp_business_management scope, or the "
                        "supplied waba_id is incorrect."
                    ),
                    meta_error=_safe_json_error(exc),
                ) from exc
            returned_waba_id = waba_info.get("id")
            if returned_waba_id is not None and str(returned_waba_id) != waba_id:
                raise BYOValidationError(
                    failed_step="waba_info_read",
                    code="waba_mismatch",
                    message=("Token resolves a different WABA than the one supplied."),
                )

            # Step 3: template list (cross-ID consistency check)
            try:
                await client.list_templates(limit=1)
            except httpx.HTTPStatusError as exc:
                raise BYOValidationError(
                    failed_step="template_list_read",
                    code="waba_mismatch",
                    message=(
                        "Token cannot list templates for the supplied "
                        "waba_id. Confirm the phone_number_id belongs to "
                        "this WABA."
                    ),
                    meta_error=_safe_json_error(exc),
                ) from exc
        finally:
            await client.close()

        # All three reads succeeded — encrypt & persist.
        from src.infrastructure.external_services.secrets import (
            get_secrets_manager,
        )

        secrets = get_secrets_manager()
        key_id = await secrets.get_current_key_id()
        creds_data = {
            "access_token": access_token,
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "app_secret": app_secret,
            "display_name": (phone_info or {}).get("verified_name"),
            "phone_number": (phone_info or {}).get("display_phone_number"),
        }
        encrypted = await secrets.encrypt(creds_data, key_id)

        existing = (
            await self.session.execute(
                select(ServiceCredential).where(
                    ServiceCredential.tenant_id == store.tenant_id,
                    ServiceCredential.service_type == ServiceType.WHATSAPP,
                    ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing:
            existing.credentials_encrypted = encrypted
            existing.encryption_key_id = key_id
            existing.is_active = True
            existing.is_validated = True
            existing.last_validated_at = now
            existing.extra_metadata = {
                "phone_number": creds_data["phone_number"],
                "display_name": creds_data["display_name"],
                "waba_id": waba_id,
            }
        else:
            existing = ServiceCredential(
                tenant_id=store.tenant_id,
                service_type=ServiceType.WHATSAPP,
                service_name=ServiceName.WHATSAPP_BUSINESS,
                credentials_encrypted=encrypted,
                encryption_key_id=key_id,
                is_active=True,
                is_validated=True,
                last_validated_at=now,
                extra_metadata={
                    "phone_number": creds_data["phone_number"],
                    "display_name": creds_data["display_name"],
                    "waba_id": waba_id,
                },
            )
            self.session.add(existing)

        # Reset toggles to DISABLED per FR-019a. Snapshot the prior
        # platform-managed state to whatsapp_notifications_prev_platform_managed
        # so disconnect can restore it.
        store_settings = dict(store.settings or {})
        prev_notifs = store_settings.get("whatsapp_notifications") or {}
        if prev_notifs and not store_settings.get(
            "whatsapp_notifications_prev_platform_managed"
        ):
            store_settings["whatsapp_notifications_prev_platform_managed"] = dict(
                prev_notifs
            )

        store_settings["whatsapp_notifications"] = {
            "order_confirmation": False,
            "payment_received": False,
            "shipping_update": False,
            "delivery_confirmation": False,
            "abandoned_cart": False,
            "marketing": False,
        }

        # Connection metadata
        wa_settings = store_settings.get("whatsapp") or {}
        wa_settings["enabled"] = True
        wa_settings["is_configured"] = True
        wa_settings["connection_type"] = "own"
        wa_settings["last_configured"] = now.isoformat()
        # Clear any prior credential_error from a previous BYO that broke
        wa_settings.pop("credential_error", None)
        store_settings["whatsapp"] = wa_settings

        store.settings = store_settings
        await self.session.flush()

        return {
            "mode": "byo",
            "connected": True,
            "phone_display_name": creds_data["display_name"],
            "display_phone_number": creds_data["phone_number"],
            "waba_id": waba_id,
            "last_validated_at": now,
            "credential_error": None,
            "notifications": store_settings["whatsapp_notifications"],
        }


def _safe_json_error(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    """Pull whitelisted fields from Meta's error body, or None on parse fail."""
    try:
        body = exc.response.json()
    except Exception:
        return None
    return sanitize_meta_error(body)
