"""Manual-transfer payment service — destination + proof verification.

Neither InstaPay nor Vodafone Cash exposes a merchant-facing API that
NUMU can use. (Vodafone does have a merchant API, but it requires a
commercial partnership and an aggregator — see :mod:`.destinations` for
why the old gateway-shaped validator was the wrong model.) On both
rails funds move directly from the customer to the merchant, and NUMU
acts as the notary: unique per-order reference code, proof upload,
auto-rules + manual review.

One service class covers both rails. The differences are small and all
live in data:

===============  ====================  =========================
   .             InstaPay              Vodafone Cash
===============  ====================  =========================
destination      IPA (merchant@cib)    wallet number (010...)
ref prefix       NU-XXXXXX             VF-XXXXXX
scannable QR     yes                   no — dial ``*9#`` / app
sender fee       none                  charged to the sender
===============  ====================  =========================

The QR row is the one worth stating out loud: a Vodafone Cash transfer
is started by dialling ``*9#`` or from inside the Ana Vodafone app.
There is nothing to scan, so this service never emits a QR payload for
that rail and the storefront must not render a dead QR box.

This implements :class:`IPaymentService` so the rest of the codebase
stays agnostic: ``create_payment_intent`` returns a ``PaymentIntent``
whose ``id`` is the reference code. Methods that don't apply to a push
payment (``confirm``, ``capture``, ``cancel``, webhook verification)
return neutral stubs rather than raising, so they compose safely with
shared payment plumbing.
"""

from __future__ import annotations

import base64
import logging
import secrets as _secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.entities.instapay import ManualPaymentMethod
from src.core.exceptions import PaymentError
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)
from src.infrastructure.external_services.manual_transfer.qr_generator import (
    build_qr_payload,
    render_qr_data_url,  # kept for email / server-rendered pages
)

logger = logging.getLogger(__name__)


DEFAULT_EXPIRY_MINUTES = 30
DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS = 50_000  # 500 EGP
DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS = 500_000  # 5,000 EGP/day
DEFAULT_AUTO_APPROVE_DAILY_COUNT = 10

# Vodafone charges the *sender* a transfer fee, so the amount that
# lands on the merchant's wallet is routinely a few pounds short of the
# order total. With InstaPay's 100 bps (1%) default a 250 EGP order
# tolerates only 2.50 EGP of shortfall — under Vodafone's fee on most
# tiers, which would push every single order into manual review. 300
# bps covers the standard fee band with room to spare while still
# catching a customer who "paid" half.
DEFAULT_VC_AMOUNT_TOLERANCE_BPS = 300

# Checkout dispatches on these, and the settings/proof layers use the
# set to decide whether an order is on a manual rail at all.
MANUAL_TRANSFER_METHODS: frozenset[str] = frozenset(
    m.value for m in ManualPaymentMethod
)

# Where each rail's config lives under ``store.settings["payment"]``.
# Same key as the checkout provider code, which is what makes the
# enable-toggle + market-registry plumbing work unchanged.
_SETTINGS_KEY = {
    ManualPaymentMethod.INSTAPAY: "instapay",
    ManualPaymentMethod.VODAFONE_CASH: "vodafone_cash",
}

_PROVIDER_ENUM = {
    ManualPaymentMethod.INSTAPAY: PaymentProvider.INSTAPAY,
    ManualPaymentMethod.VODAFONE_CASH: PaymentProvider.VODAFONE_CASH,
}

_HUMAN_NAME = {
    ManualPaymentMethod.INSTAPAY: "InstaPay",
    ManualPaymentMethod.VODAFONE_CASH: "Vodafone Cash",
}

# Storefront resume-page segments: ``<storefront>/<segment>/<order_id>``.
# Hyphenated, not the underscored provider code — these end up in emails
# and browser address bars. ``instapay`` is unchanged because links to it
# already exist in customers' inboxes.
_ROUTE_SEGMENT = {
    ManualPaymentMethod.INSTAPAY: "instapay",
    ManualPaymentMethod.VODAFONE_CASH: "vodafone-cash",
}

_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Crockford-ish


def human_name(method: ManualPaymentMethod) -> str:
    """Merchant/customer-facing label for a rail (English)."""
    return _HUMAN_NAME[method]


def route_segment(method: ManualPaymentMethod) -> str:
    """Storefront path segment for this rail's resume page."""
    return _ROUTE_SEGMENT[method]


def resume_url(
    method: ManualPaymentMethod,
    *,
    base_url: str,
    order_id: object,
    reference_code: str | None = None,
) -> str:
    """Build the customer-facing "finish paying / upload proof" link.

    The reference code rides along as ``?ref=`` because it is what
    *authorizes* the page: the proof endpoints accept either the
    customer's session cookie or the intent's reference code, and a
    customer following a link out of their email has neither a session
    nor any way to type the code in. Without it the link 403s for
    exactly the people it exists for.

    That is the same bearer-credential model the endpoints already
    document — a ~10^9-wide namespace with a 30-minute TTL, exposing
    only data the customer was already shown at checkout.
    """
    url = f"{base_url.rstrip('/')}/{route_segment(method)}/{order_id}"
    if reference_code:
        url = f"{url}?ref={reference_code}"
    return url


def settings_key(method: ManualPaymentMethod) -> str:
    """The ``store.settings["payment"][...]`` key holding this rail's config."""
    return _SETTINGS_KEY[method]


def generate_reference_code(prefix: str = "NU") -> str:
    """Return a short, human-typable, collision-resistant reference code.

    Format ``<prefix>-XXXXXX`` — 6 chars from a 32-char alphabet gives
    ~10^9 codes, which is plenty for the *active* (un-expired) window we
    care about; DB-side uniqueness is still checked by the caller.
    """
    suffix = "".join(_secrets.choice(_REFERENCE_ALPHABET) for _ in range(6))
    return f"{prefix}-{suffix}"


def default_auto_approve_enabled(method: ManualPaymentMethod) -> bool:
    """Whether a rail auto-approves proofs before a merchant has opted in.

    InstaPay: yes — unchanged, merchants have relied on it for months and
    a bank receipt carries enough chrome to make a convincing fake real
    work.

    Vodafone Cash: no. A wallet confirmation is a plain SMS screenshot,
    and with no OCR provider assigned nothing inspects the image at all,
    so "auto-approve under 500 EGP" means "approve any photo under 500
    EGP". The merchant turns it on once they trust what they are seeing.
    """
    return method is ManualPaymentMethod.INSTAPAY


def default_amount_tolerance_bps(method: ManualPaymentMethod) -> int:
    """Per-rail default for the declared/OCR amount-match tolerance."""
    if method is ManualPaymentMethod.VODAFONE_CASH:
        return DEFAULT_VC_AMOUNT_TOLERANCE_BPS
    return 100


async def get_merchant_manual_credentials(
    store_settings: dict,
    method: ManualPaymentMethod = ManualPaymentMethod.INSTAPAY,
) -> dict:
    """Decrypt a merchant's manual-rail configuration from ``store.settings``.

    Returns a flat dict: the decrypted secret fields (``destination``,
    plus the InstaPay-era ``ipa`` alias, and ``fallback_phone``) merged
    with the plaintext display/threshold settings the merchant tunes in
    the dashboard.

    Raises :class:`PaymentError` when the rail isn't enabled or has no
    stored credentials — checkout turns that into a 400 telling the
    customer to pick another method.
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    name = human_name(method)
    cfg = (store_settings or {}).get("payment", {}).get(settings_key(method), {})

    if not cfg.get("enabled"):
        raise PaymentError(
            f"{name} is not enabled for this store. "
            "Please configure it in payment settings."
        )
    if not cfg.get("encrypted_credentials"):
        raise PaymentError(
            f"{name} credentials not configured for this store. "
            "Please configure payment settings."
        )

    secrets_manager = get_secrets_manager()
    key_id = cfg["encryption_key_id"]
    encrypted = base64.b64decode(cfg["encrypted_credentials"])

    try:
        decrypted = await secrets_manager.decrypt(encrypted, key_id)
    except Exception as e:
        logger.error(f"Failed to decrypt {name} credentials: {e}")
        raise PaymentError(
            f"Failed to read {name} credentials. Please re-save them."
        ) from e

    # ``ipa`` is the historical key inside the encrypted blob for
    # InstaPay; Vodafone Cash writes ``wallet_number``. Normalize both
    # to ``destination`` so callers never branch, while keeping ``ipa``
    # populated for the InstaPay-era call sites.
    destination = (
        decrypted.get("destination")
        or decrypted.get("ipa")
        or decrypted.get("wallet_number")
    )

    return {
        **decrypted,
        "destination": destination,
        "ipa": destination if method is ManualPaymentMethod.INSTAPAY else None,
        "method": method,
        # Merchant-facing display/threshold settings (not encrypted) so
        # the caller sees one flat dict.
        "display_name": cfg.get("display_name") or cfg.get("ipa_display_name"),
        "ipa_display_name": cfg.get("ipa_display_name"),
        "qr_image_url": cfg.get("qr_image_url"),
        "qr_link_url": cfg.get("qr_link_url"),
        "auto_approve_threshold_cents": cfg.get(
            "auto_approve_threshold_cents",
            DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
        ),
        "auto_approve_daily_cap_cents": cfg.get(
            "auto_approve_daily_cap_cents",
            DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
        ),
        "auto_approve_daily_count": cfg.get(
            "auto_approve_daily_count",
            DEFAULT_AUTO_APPROVE_DAILY_COUNT,
        ),
    }


async def get_merchant_instapay_credentials(store_settings: dict) -> dict:
    """Back-compat shim — :func:`get_merchant_manual_credentials` for InstaPay."""
    return await get_merchant_manual_credentials(
        store_settings, ManualPaymentMethod.INSTAPAY
    )


class ManualTransferPaymentService(IPaymentService):
    """Manual push-payment provider wrapping the proof-upload workflow.

    Instantiated per-request with the merchant's decrypted credentials.
    Stateless — :class:`ManualPaymentIntentRepository` is what actually
    writes to the DB; ``build_intent_payload`` here just computes the
    payload (QR, expiry) the route handler will persist.
    """

    def __init__(
        self,
        *,
        destination: str,
        method: ManualPaymentMethod = ManualPaymentMethod.INSTAPAY,
        display_name: str | None = None,
        fallback_phone: str | None = None,
        qr_image_url: str | None = None,
        qr_link_url: str | None = None,
        expiry_minutes: int = DEFAULT_EXPIRY_MINUTES,
    ) -> None:
        if not destination:
            raise PaymentError(f"A {human_name(method)} destination is required")
        self.method = method
        self.destination = destination
        self.display_name = display_name or destination
        self.fallback_phone = fallback_phone
        # QR is an InstaPay-only affordance. Accepting the kwargs on
        # both rails and dropping them here means the caller doesn't
        # have to branch, and a stray merchant-uploaded QR can never
        # leak onto a Vodafone Cash checkout.
        self.qr_image_url = qr_image_url if self.supports_qr else None
        self.qr_link_url = qr_link_url if self.supports_qr else None
        self.expiry_minutes = expiry_minutes

    @property
    def supports_qr(self) -> bool:
        """Whether this rail has anything scannable.

        False for Vodafone Cash: transfers start at ``*9#`` or in the
        Ana Vodafone app, so a QR would be decoration the customer
        cannot act on.
        """
        return self.method is ManualPaymentMethod.INSTAPAY

    @property
    def provider(self) -> PaymentProvider:
        return _PROVIDER_ENUM[self.method]

    @property
    def ipa(self) -> str:
        """Back-compat alias for :attr:`destination` (InstaPay call sites)."""
        return self.destination

    def build_intent_payload(
        self,
        *,
        amount_cents: int,
        reference_code: str,
        note: str | None = None,
    ) -> tuple[str, datetime]:
        """Return ``(qr_payload, expires_at)`` for persistence.

        ``qr_payload`` is an empty string on rails without a QR. Kept
        separate from ``create_payment_intent`` so the route handler
        can persist the intent and the Order in the same transaction.
        """
        qr_payload = (
            build_qr_payload(
                ipa=self.destination,
                amount_cents=amount_cents,
                reference_code=reference_code,
                note=note,
            )
            if self.supports_qr
            else ""
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
        via ``metadata["reference_code"]``.
        """
        metadata = metadata or {}
        reference_code = metadata.get("reference_code") or generate_reference_code(
            self.method.reference_prefix
        )
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
            provider=self.provider,
        )

    async def confirm_payment(self, payment_intent_id: str) -> PaymentResult:
        # Confirmation happens out-of-band via proof upload + review.
        return PaymentResult(
            success=False,
            payment_id=payment_intent_id,
            error_message=(
                f"{human_name(self.method)} payments are confirmed via proof upload"
            ),
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
        # Manual-rail refunds are pushes the merchant makes by hand; we
        # expose a structural "pending manual" result so callers can
        # surface the right UX without treating it as a failure.
        return RefundResult(
            success=False,
            error_message=(
                f"{human_name(self.method)} refunds are completed manually "
                "by the merchant"
            ),
        )

    async def get_payment_status(self, payment_id: str) -> str:
        # We don't query a gateway — the caller reads status from the
        # intent + PaymentProof rows directly.
        return "awaiting_payment"

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        # No webhooks on either rail. When Paymob's InstaPay SKU ships,
        # the Paymob webhook handler fields the callback and this
        # method can grow to delegate — for now, reject.
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
        ``qr_payload`` / ``qr_image_url`` / ``qr_link_url`` are all null
        on Vodafone Cash so the instructions panel has an unambiguous
        signal to hide its QR block rather than render an empty one.
        """
        is_wallet = self.method is ManualPaymentMethod.VODAFONE_CASH
        return {
            "provider": self.method.value,
            "type": "manual_verification",
            "reference_code": reference_code,
            # Rail-neutral destination + the rail-specific aliases the
            # storefront labels differently ("IPA" vs "wallet number").
            "destination": self.destination,
            "destination_kind": "wallet_number" if is_wallet else "ipa",
            "ipa": None if is_wallet else self.destination,
            "wallet_number": self.destination if is_wallet else None,
            "display_name": self.display_name,
            "ipa_display_name": self.display_name,
            "fallback_phone": self.fallback_phone,
            "supports_qr": self.supports_qr,
            "qr_payload": qr_payload or None,
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
            # full-payment flows; ``balance_due_cents`` is computed
            # here so the storefront doesn't have to re-derive it.
            "is_deposit": is_deposit,
            "order_total_cents": order_total_cents if is_deposit else None,
            "balance_due_cents": (
                max(0, order_total_cents - amount_cents)
                if is_deposit and order_total_cents is not None
                else None
            ),
        }


class InstapayPaymentService(ManualTransferPaymentService):
    """Back-compat constructor taking ``ipa=`` instead of ``destination=``.

    Same behaviour — it exists purely so InstaPay-era call sites keep
    reading naturally. New code should use
    :class:`ManualTransferPaymentService` with an explicit ``method``.
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
        super().__init__(
            destination=ipa,
            method=ManualPaymentMethod.INSTAPAY,
            display_name=ipa_display_name,
            fallback_phone=fallback_phone,
            qr_image_url=qr_image_url,
            qr_link_url=qr_link_url,
            expiry_minutes=expiry_minutes,
        )
