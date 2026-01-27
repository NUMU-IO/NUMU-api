"""Core value objects."""

from src.core.value_objects.address import Address
from src.core.value_objects.email import Email
from src.core.value_objects.localized_string import LocalizedString
from src.core.value_objects.money import Currency, Money
from src.core.value_objects.phone import PhoneNumber

__all__ = [
    "Address",
    "Currency",
    "Email",
    "LocalizedString",
    "Money",
    "PhoneNumber",
]
