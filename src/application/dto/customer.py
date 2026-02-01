"""Customer DTOs for application layer."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.address import AddressLabel


@dataclass
class CustomerDTO(BaseDTO):
    """Customer data transfer object."""

    id: str
    store_id: str
    email: str
    first_name: str
    last_name: str
    full_name: str
    phone: str | None = None
    accepts_marketing: bool = False
    is_verified: bool = False
    total_orders: int = 0
    total_spent: int = 0
    default_address_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity) -> "CustomerDTO":
        """Create DTO from entity."""
        return cls(
            id=str(entity.id),
            store_id=str(entity.store_id),
            email=str(entity.email),
            first_name=entity.first_name,
            last_name=entity.last_name,
            full_name=entity.full_name,
            phone=str(entity.phone) if entity.phone else None,
            accepts_marketing=entity.accepts_marketing,
            is_verified=entity.is_verified,
            total_orders=entity.total_orders,
            total_spent=entity.total_spent,
            default_address_id=str(entity.default_address_id) if entity.default_address_id else None,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CustomerAddressDTO(BaseDTO):
    """Customer address data transfer object."""

    id: str
    customer_id: str
    first_name: str
    last_name: str
    full_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    is_default: bool = False
    label: str = "home"
    formatted_address: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity) -> "CustomerAddressDTO":
        """Create DTO from entity."""
        return cls(
            id=str(entity.id),
            customer_id=str(entity.customer_id),
            first_name=entity.first_name,
            last_name=entity.last_name,
            full_name=entity.full_name,
            address_line1=entity.address_line1,
            address_line2=entity.address_line2,
            city=entity.city,
            state=entity.state,
            postal_code=entity.postal_code,
            country=entity.country,
            phone=entity.phone,
            is_default=entity.is_default,
            label=entity.label.value if entity.label else "home",
            formatted_address=entity.formatted_address,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CustomerRegisterDTO(BaseDTO):
    """Customer registration request data."""

    store_id: str
    email: str
    password: str
    first_name: str
    last_name: str
    phone: str | None = None
    accepts_marketing: bool = False


@dataclass
class CustomerLoginDTO(BaseDTO):
    """Customer login request data."""

    store_id: str
    email: str
    password: str


@dataclass
class CustomerTokenDTO(BaseDTO):
    """Customer token response data."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None


@dataclass
class CustomerAuthResponseDTO(BaseDTO):
    """Customer authentication response with customer and tokens."""

    customer: CustomerDTO
    tokens: CustomerTokenDTO


@dataclass
class CustomerUpdateProfileDTO(BaseDTO):
    """Customer profile update request data."""

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    accepts_marketing: bool | None = None


@dataclass
class CustomerChangePasswordDTO(BaseDTO):
    """Customer change password request data."""

    current_password: str
    new_password: str


@dataclass
class CustomerPasswordResetRequestDTO(BaseDTO):
    """Customer password reset request data."""

    store_id: str
    email: str


@dataclass
class CustomerPasswordResetDTO(BaseDTO):
    """Customer password reset with token data."""

    token: str
    new_password: str


@dataclass
class CreateAddressDTO(BaseDTO):
    """Create address request data."""

    customer_id: str
    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    is_default: bool = False
    label: str = "home"


@dataclass
class UpdateAddressDTO(BaseDTO):
    """Update address request data."""

    first_name: str | None = None
    last_name: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    label: str | None = None
