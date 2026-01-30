"""Checkout DTOs."""

from dataclasses import dataclass

from src.application.dto.base import BaseDTO


@dataclass
class CheckoutAddressDTO(BaseDTO):
    """Checkout address data transfer object."""

    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None


@dataclass
class CheckoutDTO(BaseDTO):
    """Checkout data transfer object."""

    shipping_address: CheckoutAddressDTO
    billing_address: CheckoutAddressDTO | None = None
    shipping_cost: int = 0  # In cents
    tax_amount: int = 0  # In cents
    discount_amount: int = 0  # In cents
    currency: str = "EGP"
    payment_method: str | None = None
    shipping_method: str | None = None
    customer_notes: str | None = None
