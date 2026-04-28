"""InstaPay payment service — manual IPA + proof verification flow.

InstaPay does not expose a merchant-facing API (see `InstaPay` strategy
doc). Funds move directly from the customer's bank to the merchant's
bank; NUMU acts as the notary (unique per-order reference code, proof
upload, auto-rules + manual review).

This service implements :class:`IPaymentService` so the rest of the
codebase stays agnostic: ``create_payment_intent`` persists an
``InstapayIntent`` and returns a ``PaymentIntent`` whose ``id`` is the
reference code and whose ``client_secret`` is the scannable QR payload.
All methods that don't apply to a push payment (``confirm``, ``capture``,
``cancel`` at the gateway level, ``verify_webhook_signature``) return
neutral stubs rather than raising, so they compose safely with shared
payment plumbing.
"""

from __future__ import annotations

import base64
import logging
import secrets as _secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)
from src.infrastructure.external_services.instapay.qr_generator import (
    build_qr_payload,
    render_qr_data_url,  # kept for email / server-rendered pages
)

logger = logging.getLogger(__name__)


DEFAULT_EXPIRY_MINUTES = 30
DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS = 50_000  # 500 EGP
DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS = 500_000  # 5,000 EGP/day
DEFAULT_AUTO_APPROVE_DAILY_COUNT = 10

_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Crockford-ish


def generate_reference_code(prefix: str = "NU") -> str:
    """Return a short, human-typable, collision-resistant reference code.

    Format ``<prefix>-XXXXXX`` — 6 chars from a 32-char alphabet gives
    ~10⁹ codes, which is plenty for the *active* (un-expired) window we
    care about; DB-side uniqueness is still checked by the caller.
    """
    suffix = "".join(_secrets.choice(_REFERENCE_ALPHABET) for _ in range(6))
    return f"{prefix}-{suffix}"


async def get_merchant_instapay_credentials(store_settings: dict) -> dict:
    """Decrypt a merchant's InstaPay configuration from ``store.settings``.

    Returns a dict with at minimum ``ipa``; may also include
    ``fallback_phone``, ``ipa_display_name``, and the auto-approval
    thresholds the merchant chose. Missing auto-approval fields fall
    through to the module defaults in the caller.
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    instapay_settings = (store_settings or {}).get("payment", {}).get("instapay", {})

    if not instapay_settings.get("enabled"):
        raise PaymentError(
            "InstaPay is not enabled for this store. "
            "Please configure it in payment settings."
        )
    if not instapay_settings.get("encrypted_credentials"):
        raise PaymentError(
            "InstaPay credentials not configured for this store. "
            "Please configure payment gateway in store settings."
        )

    secrets_manager = get_secrets_manager()
    key_id = instapay_settings["encryption_key_id"]
    encrypted = base64.b64decode(instapay_settings["encrypted_credentials"])

    try:
        decrypted = await secrets_manager.decrypt(encrypted, key_id)
    except Exception as e:
        logger.error(f"Failed to decrypt InstaPay credentials: {e}")
        raise PaymentError(
            "Failed to read InstaPay credentials. Please re-save them."
        ) from e

    # Merge merchant-facing display/threshold settings (not encrypted)
    # so the caller sees one flat dict.
    return {
        **decrypted,
        "ipa_display_name": instapay_settings.get("ipa_display_name"),
        "qr_image_url": instapay_settings.get("qr_image_url"),
        "qr_link_url": instapay_settings.get("qr_link_url"),
        "auto_approve_threshold_cents": instapay_settings.get(
            "auto_approve_threshold_cents",
            DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
        ),
        "auto_approve_daily_cap_cents": instapay_settings.get(
            "auto_approve_daily_cap_cents",
            DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
        ),
        "auto_approve_daily_count": instapay_settings.get(
            "auto_approve_daily_count",
            DEFAULT_AUTO_APPROVE_DAILY_COUNT,
        ),
    }


class InstapayPaymentService(IPaymentService):
    """Manual InstaPay provider wrapping the proof-upload workflow.

    Instantiated per-request with the merchant's decrypted credentials.
    Stateless — the :class:`InstapayIntentRepository` is what actually
    writes to the DB; ``create_payment_intent`` here just computes the
    payload (ref code, QR, expiry) the route handler will persist.
    """

    def __init__(
        self,
        *,
        ipa: str,
        ipa_display_name: str | None = None,
        fallback_phone: str | None = None,
        qr_image_url: str | None = None,
        qr_link_url: str | None = None,
        expiry_minutes: int = DEFAULT_EXPIRY_MINUTES,
    ) -> None:
        if not ipa:
            raise PaymentError("InstaPay IPA is required")
        self.ipa = ipa
        self.ipa_display_name = ipa_display_name or ipa
        self.fallback_phone = fallback_phone
        self.qr_image_url = qr_image_url
        self.qr_link_url = qr_link_url
        self.expiry_minutes = expiry_minutes

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.INSTAPAY

    def build_intent_payload(
        self,
        *,
        amount_cents: int,
        reference_code: str,
        note: str | None = None,
    ) -> tuple[str, datetime]:
        """Return ``(qr_payload, expires_at)`` for persistence.

        Kept separate from ``create_payment_intent`` so the route handler
        can persist the InstapayIntent and the Order in the same
        transaction instead of being forced to take whatever
        ``PaymentIntent.client_secret`` exposes.
        """
        qr_payload = build_qr_payload(
            ipa=self.ipa,
            amount_cents=amount_cents,
            reference_code=reference_code,
            note=note,
        )
        expires_at = datetime.now(UTC) + timedelta(minutes=self.expiry_minutes)
        return qr_payload, expires_at

    async def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentIntent:
        """Return a PaymentIntent whose id is the reference code.

        The reference code itself must be generated and persisted by the
        caller (which knows the DB session and can enforce uniqueness);
        this method only fills it into the returned payload if passed
        via ``metadata["reference_code"]``. The ``client_secret`` is the
        scannable QR payload string.
        """
        metadata = metadata or {}
        reference_code = metadata.get("reference_code") or generate_reference_code()
        note = (
            metadata.get("note") or f"Order {metadata.get('order_number', '')}".strip()
        )
        qr_payload, _expires_at = self.build_intent_payload(
            amount_cents=amount,
            reference_code=reference_code,
            note=note,
        )
        return PaymentIntent(
            id=reference_code,
            client_secret=qr_payload,
            amount=amount,
            currency=currency.upper(),
            status="awaiting_payment",
            provider=PaymentProvider.INSTAPAY,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        # Confirmation happens out-of-band via proof upload + review.
        return PaymentResult(
            success=False,
            payment_id=payment_intent_id,
            error_message="InstaPay payments are confirmed via proof upload",
            error_code="manual_verification_required",
        )

    async def capture_payment(self, payment_intent_id: str) -> PaymentResult:
        return await self.confirm_payment(payment_intent_id)

    async def cancel_payment(self, payment_intent_id: str) -> PaymentResult:
        # The route handler handles intent cancellation directly via
        # the repository; this API-level cancel is a no-op.
        return PaymentResult(success=True, payment_id=payment_intent_id)

    async def refund_payment(
        self,
        payment_id: str,
        amount: int | None = None,
    ) -> RefundResult:
        # InstaPay refunds are manual bank pushes by the merchant; we
        # expose a structural "pending manual" result so callers can
        # surface the right UX without treating it as a failure.
        return RefundResult(
            success=False,
            error_message="InstaPay refunds are completed manually by the merchant",
        )

    async def get_payment_status(self, payment_id: str) -> str:
        # We don't query a gateway — the caller reads status from the
        # InstapayIntent + PaymentProof rows directly.
        return "awaiting_payment"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        # No webhooks in MVP. When Paymob's InstaPay SKU ships, the
        # Paymob webhook handler fields the callback and this method
        # can grow to delegate — for now, reject.
        return None

    def qr_data_url(self, qr_payload: str) -> str:
        """Render the scannable PNG as a data: URL.

        Kept for contexts where offline / inline rendering is needed
        (primarily email templates). The hot checkout path no longer
        calls this — the storefront renders the QR client-side from
        ``qr_payload`` so we avoid the ~20-80 ms PIL encode per order.
        """
        return render_qr_data_url(qr_payload)

    def to_checkout_payload(
        self,
        *,
        reference_code: str,
        qr_payload: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime,
        order_id: str,
        is_deposit: bool = False,
        order_total_cents: int | None = None,
    ) -> dict[str, Any]:
        """Assemble the ``payment_data`` the storefront renders after checkout.

        Matches the shape used for Fawry/Fawaterak — keeps the storefront
        free of provider branching beyond the initial ``provider`` switch.
        Returns ``qr_payload`` (a plain URI string); the storefront
        renders the actual QR image client-side with the ``qrcode`` npm
        package. Side benefits: no PIL on the API hot path, crisper QR
        at any zoom level (re-rendered, not a fixed-resolution PNG),
        smaller response body.
        """
        return {
            "provider": "instapay",
            "type": "manual_verification",
            "reference_code": reference_code,
            "ipa": self.ipa,
            "ipa_display_name": self.ipa_display_name,
            "fallback_phone": self.fallback_phone,
            "qr_payload": qr_payload,
            # Public URL of the merchant-uploaded QR image. The
            # client-side `qr_payload` URI is not InstaPay-app
            # readable; this image is what the customer actually
            # scans. May be null if the merchant hasn't uploaded yet.
            "qr_image_url": self.qr_image_url,
            # Merchant-pasted InstaPay "Share link" URL. The
            # storefront generates a QR code from this string; the
            # customer's phone camera follows the URL to the InstaPay
            # universal link. Takes priority over qr_image_url when
            # both are set.
            "qr_link_url": self.qr_link_url,
            "amount": f"{amount_cents / 100:.2f}",
            "amount_cents": amount_cents,
            "currency": currency.upper(),
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": max(
                0, int((expires_at - datetime.now(UTC)).total_seconds())
            ),
            "order_id": order_id,
            # Deposit context — when ``is_deposit`` is true the
            # storefront swaps to a "deposit of X, balance Y on
            # delivery" banner. ``order_total_cents`` is null on
            # full-InstaPay flows; ``balance_due_cents`` is computed
            # here so the storefront doesn't have to re-derive it.
            "is_deposit": is_deposit,
            "order_total_cents": order_total_cents if is_deposit else None,
            "balance_due_cents": (
                max(0, order_total_cents - amount_cents)
                if is_deposit and order_total_cents is not None
                else None
            ),
        }
