"""Customer API schemas for requests and responses."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr

# ============== Request Schemas ==============


class CustomerRegisterRequest(BaseModel):
    """Customer registration request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "ahmed@example.com",
                "password": "SecureP@ss1",
                "first_name": "Ahmed",
                "last_name": "Hassan",
                "phone": "+201234567890",
                "accepts_marketing": True,
            }
        }
    )

    email: EmailStr = Field(description="Customer email address")
    password: str = Field(
        ..., min_length=8, max_length=100, description="Password (min 8 characters)"
    )
    first_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="First name"
    )
    last_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Last name"
    )
    phone: str | None = Field(
        None, max_length=20, description="Phone number"
    )
    accepts_marketing: bool = Field(
        False, description="Whether the customer opts in to marketing"
    )


class CustomerLoginRequest(BaseModel):
    """Customer login request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "ahmed@example.com",
                "password": "SecureP@ss1",
            }
        }
    )

    email: EmailStr = Field(description="Customer email")
    password: str = Field(description="Customer password")


class CustomerUpdateProfileRequest(BaseModel):
    """Customer profile update request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Ahmed",
                "phone": "+201234567890",
            }
        }
    )

    first_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="First name"
    )
    last_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="Last name"
    )
    phone: str | None = Field(None, max_length=20, description="Phone number")
    accepts_marketing: bool | None = Field(
        None, description="Marketing opt-in preference"
    )


class CustomerChangePasswordRequest(BaseModel):
    """Customer change password request schema."""

    current_password: str = Field(description="Current password")
    new_password: str = Field(
        ..., min_length=8, max_length=100, description="New password (min 8 characters)"
    )


class CustomerPasswordResetRequest(BaseModel):
    """Customer password reset request schema."""

    email: EmailStr = Field(description="Email address to send reset link")


class CustomerPasswordResetConfirmRequest(BaseModel):
    """Customer password reset confirm request schema."""

    token: str = Field(description="Password reset token from email")
    new_password: str = Field(
        ..., min_length=8, max_length=100, description="New password"
    )


class CreateAddressRequest(BaseModel):
    """Create address request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "first_name": "Ahmed",
                "last_name": "Hassan",
                "address_line1": "45 Nile Corniche",
                "city": "Cairo",
                "country": "Egypt",
                "phone": "+201234567890",
                "is_default": True,
                "label": "home",
            }
        }
    )

    first_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Recipient first name"
    )
    last_name: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Recipient last name"
    )
    address_line1: SanitizedStr = Field(
        ..., min_length=1, max_length=255, description="Street address line 1"
    )
    address_line2: SanitizedStr | None = Field(
        None, max_length=255, description="Apartment, suite, floor"
    )
    city: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="City name"
    )
    state: SanitizedStr | None = Field(
        None, max_length=100, description="State or governorate"
    )
    postal_code: str | None = Field(
        None, max_length=20, description="Postal / ZIP code"
    )
    country: SanitizedStr = Field(
        ..., min_length=1, max_length=100, description="Country"
    )
    phone: str | None = Field(
        None, max_length=20, description="Phone number"
    )
    is_default: bool = Field(
        False, description="Set as the default address"
    )
    label: str = Field(
        "home", pattern="^(home|work|other)$", description="Address label: home, work, or other"
    )


class UpdateAddressRequest(BaseModel):
    """Update address request schema."""

    first_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="First name"
    )
    last_name: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="Last name"
    )
    address_line1: SanitizedStr | None = Field(
        None, min_length=1, max_length=255, description="Address line 1"
    )
    address_line2: SanitizedStr | None = Field(
        None, max_length=255, description="Address line 2"
    )
    city: SanitizedStr | None = Field(
        None, min_length=1, max_length=100, description="City"
    )
    state: SanitizedStr | None = Field(
        None, max_length=100, description="State or governorate"
    )
    postal_code: str | None = Field(None, max_length=20, description="Postal code")
    country: str | None = Field(
        None, min_length=1, max_length=100, description="Country"
    )
    phone: str | None = Field(None, max_length=20, description="Phone number")
    label: str | None = Field(
        None, pattern="^(home|work|other)$", description="Address label"
    )


# ============== Response Schemas ==============


class CustomerResponse(BaseModel):
    """Customer response schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "990e8400-e29b-41d4-a716-446655440000",
                "store_id": "660e8400-e29b-41d4-a716-446655440000",
                "email": "ahmed@example.com",
                "first_name": "Ahmed",
                "last_name": "Hassan",
                "full_name": "Ahmed Hassan",
                "phone": "+201234567890",
                "accepts_marketing": True,
                "is_verified": True,
                "total_orders": 5,
                "total_spent": 125000,
                "created_at": "2025-01-05T09:00:00Z",
                "updated_at": "2025-02-10T14:00:00Z",
            }
        }
    )

    id: str = Field(description="Customer UUID")
    store_id: str = Field(description="Store UUID this customer belongs to")
    email: str = Field(description="Customer email address")
    first_name: str = Field(description="First name")
    last_name: str = Field(description="Last name")
    full_name: str = Field(description="Concatenated full name")
    phone: str | None = Field(None, description="Phone number")
    accepts_marketing: bool = Field(False, description="Marketing opt-in")
    is_verified: bool = Field(False, description="Whether email is verified")
    total_orders: int = Field(0, description="Lifetime order count")
    total_spent: int = Field(0, description="Lifetime spend in cents")
    default_address_id: str | None = Field(None, description="Default address UUID")
    created_at: str | None = Field(None, description="ISO 8601 creation timestamp")
    updated_at: str | None = Field(None, description="ISO 8601 last-update timestamp")


class CustomerTokenResponse(BaseModel):
    """Customer token response schema."""

    access_token: str = Field(description="JWT access token")
    refresh_token: str = Field(description="JWT refresh token")
    token_type: str = Field("bearer", description="Token type (always 'bearer')")
    expires_in: int | None = Field(None, description="Access token lifetime in seconds")


class CustomerAuthResponse(BaseModel):
    """Customer auth response schema."""

    customer: CustomerResponse = Field(description="Customer profile")
    tokens: CustomerTokenResponse = Field(description="Auth tokens")


class CustomerAddressResponse(BaseModel):
    """Customer address response schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "aa0e8400-e29b-41d4-a716-446655440000",
                "customer_id": "990e8400-e29b-41d4-a716-446655440000",
                "first_name": "Ahmed",
                "last_name": "Hassan",
                "full_name": "Ahmed Hassan",
                "address_line1": "45 Nile Corniche",
                "city": "Cairo",
                "country": "Egypt",
                "is_default": True,
                "label": "home",
            }
        }
    )

    id: str = Field(description="Address UUID")
    customer_id: str = Field(description="Customer UUID")
    first_name: str = Field(description="First name")
    last_name: str = Field(description="Last name")
    full_name: str = Field(description="Concatenated full name")
    address_line1: str = Field(description="Address line 1")
    address_line2: str | None = Field(None, description="Address line 2")
    city: str = Field(description="City")
    state: str | None = Field(None, description="State or governorate")
    postal_code: str | None = Field(None, description="Postal code")
    country: str = Field(description="Country")
    phone: str | None = Field(None, description="Phone number")
    is_default: bool = Field(False, description="Whether this is the default address")
    label: str = Field("home", description="Address label: home, work, other")
    formatted_address: str = Field("", description="Human-readable formatted address")
    created_at: str | None = Field(None, description="ISO 8601 creation timestamp")
    updated_at: str | None = Field(None, description="ISO 8601 last-update timestamp")


class CustomerAddressListResponse(BaseModel):
    """Customer address list response schema."""

    addresses: list[CustomerAddressResponse] = Field(description="List of addresses")
    total: int = Field(description="Total number of addresses")


class CustomerOrderListResponse(BaseModel):
    """Customer order list response schema."""

    orders: list[dict] = Field(description="List of order summaries")
    total: int = Field(description="Total number of orders")
    skip: int = Field(description="Number of records skipped")
    limit: int = Field(description="Page size")
