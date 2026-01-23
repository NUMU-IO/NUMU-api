"""Address use cases module."""

from src.application.use_cases.customers.addresses.create_address import CreateAddressUseCase
from src.application.use_cases.customers.addresses.delete_address import DeleteAddressUseCase
from src.application.use_cases.customers.addresses.list_addresses import ListAddressesUseCase
from src.application.use_cases.customers.addresses.set_default import SetDefaultAddressUseCase
from src.application.use_cases.customers.addresses.update_address import UpdateAddressUseCase

__all__ = [
    "CreateAddressUseCase",
    "ListAddressesUseCase",
    "UpdateAddressUseCase",
    "DeleteAddressUseCase",
    "SetDefaultAddressUseCase",
]
