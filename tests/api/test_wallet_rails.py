"""WE Pay and Orange Cash ride the same manual rail as Vodafone Cash.

A mobile wallet is paid to a phone number and confirmed by a screenshot of an
SMS; InstaPay is paid to an address and confirmed by a receipt an OCR provider
can read. Everything that used to ask "is this Vodafone Cash?" meant "is this a
wallet?", so the two new rails are registry entries rather than new branches —
and these tests are what keeps that true when the next one is added.

Pure logic; no Postgres.
"""

import pytest

from src.api.v1.schemas.tenant.settings import DEPOSIT_GATEWAY_VALUES
from src.core.entities.instapay import ManualPaymentMethod
from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.manual_transfer.destinations import (
    InvalidDestinationError,
    mask_destination,
    normalize_destination,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    MANUAL_TRANSFER_METHODS,
    human_name,
    route_segment,
    settings_key,
)

WALLETS = (
    ManualPaymentMethod.VODAFONE_CASH,
    ManualPaymentMethod.WE_PAY,
    ManualPaymentMethod.ORANGE_CASH,
)


@pytest.mark.parametrize("method", WALLETS)
def test_every_wallet_is_marked_as_one(method: ManualPaymentMethod):
    assert method.is_wallet is True


def test_instapay_is_not_a_wallet():
    """It takes an address, not a phone number — the whole distinction."""
    assert ManualPaymentMethod.INSTAPAY.is_wallet is False


@pytest.mark.parametrize("method", list(ManualPaymentMethod))
def test_every_rail_is_fully_registered(method: ManualPaymentMethod):
    """A half-registered rail 500s at checkout instead of failing here."""
    assert settings_key(method)
    assert human_name(method)
    assert route_segment(method)
    assert method.reference_prefix
    assert PaymentProvider(method.value)


def test_reference_prefixes_are_unique():
    """A merchant reads the prefix to tell which rail a transfer belongs to."""
    prefixes = [m.reference_prefix for m in ManualPaymentMethod]
    assert len(prefixes) == len(set(prefixes))


def test_checkout_dispatches_every_rail():
    """The set checkout branches on is derived, not hand-maintained."""
    assert MANUAL_TRANSFER_METHODS == {m.value for m in ManualPaymentMethod}
    assert "we_pay" in MANUAL_TRANSFER_METHODS
    assert "orange_cash" in MANUAL_TRANSFER_METHODS


@pytest.mark.parametrize("method", WALLETS)
def test_wallet_destinations_mask_like_a_phone_number(method):
    masked = mask_destination(method, "01012345678")
    assert masked.startswith("010")
    assert masked.endswith("5678")
    assert "*" in masked


@pytest.mark.parametrize(
    ("method", "prefix"),
    [
        (ManualPaymentMethod.VODAFONE_CASH, "010"),
        (ManualPaymentMethod.WE_PAY, "015"),
        (ManualPaymentMethod.ORANGE_CASH, "012"),
    ],
)
def test_each_wallet_keeps_to_its_own_network(method, prefix):
    """A WE wallet is 015, an Orange one 012. Publishing the wrong one sends
    customers to a wallet that cannot receive their money."""
    good = f"{prefix}12345678"
    assert normalize_destination(method, good) == good

    wrong_network = "01112345678" if prefix != "011" else "01012345678"
    with pytest.raises(InvalidDestinationError):
        normalize_destination(method, wrong_network)


def test_wallet_numbers_survive_how_merchants_paste_them():
    """+20, 0020, spaces and Arabic-Indic digits all come off the clipboard."""
    for raw in ("+20 150 123 4567", "0020 150 123 4567", "٠١٥٠١٢٣٤٥٦٧"):
        assert normalize_destination(ManualPaymentMethod.WE_PAY, raw).startswith("015")


def test_wallets_can_carry_a_cod_deposit():
    """Manual, but it is how most Egyptian shoppers pay — a deposit nobody
    can pay protects nothing."""
    for rail in ("vodafone_cash", "we_pay", "orange_cash"):
        assert rail in DEPOSIT_GATEWAY_VALUES


def test_bank_transfer_still_cannot_carry_a_deposit():
    """No receipt to read, and settlement is days — it would strand orders."""
    assert "bank_transfer" not in DEPOSIT_GATEWAY_VALUES
