"""Customer API schemas for requests and responses."""

from pydantic import BaseModel, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr

# ============== Request Schemas ==============


class CustomerRegisterRequest(BaseModel):
    """Customer registration request schema."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    first_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    last_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    accepts_marketing: bool = False


class CustomerLoginRequest(BaseModel):
    """Customer login request schema."""

    email: EmailStr
    password: str


class CustomerUpdateProfileRequest(BaseModel):
    """Customer profile update request schema."""

    first_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    last_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    accepts_marketing: bool | None = None


class CustomerChangePasswordRequest(BaseModel):
    """Customer change password request schema."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)


class CustomerPasswordResetRequest(BaseModel):
    """Customer password reset request schema."""

    email: EmailStr


class CustomerPasswordResetConfirmRequest(BaseModel):
    """Customer password reset confirm request schema."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=100)


class CreateAddressRequest(BaseModel):
    """Create address request schema."""

    first_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    last_name: SanitizedStr = Field(..., min_length=1, max_length=100)
    address_line1: SanitizedStr = Field(..., min_length=1, max_length=255)
    address_line2: SanitizedStr | None = Field(None, max_length=255)
    city: SanitizedStr = Field(..., min_length=1, max_length=100)
    state: SanitizedStr | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: SanitizedStr = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    is_default: bool = False
    label: str = Field("home", pattern="^(home|work|other)$")


class UpdateAddressRequest(BaseModel):
    """Update address request schema."""

    first_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    last_name: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    address_line1: SanitizedStr | None = Field(None, min_length=1, max_length=255)
    address_line2: SanitizedStr | None = Field(None, max_length=255)
    city: SanitizedStr | None = Field(None, min_length=1, max_length=100)
    state: SanitizedStr | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    label: str | None = Field(None, pattern="^(home|work|other)$")


# ============== Response Schemas ==============


class CustomerResponse(BaseModel):
    """Customer response schema."""

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
    created_at: str | None = None
    updated_at: str | None = None


class CustomerTokenResponse(BaseModel):
    """Customer token response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None


class CustomerAuthResponse(BaseModel):
    """Customer auth response schema."""

    customer: CustomerResponse
    tokens: CustomerTokenResponse


class CustomerAddressResponse(BaseModel):
    """Customer address response schema."""

    id: str
    customer_id: str
    first_name: str
    last_name: str
    full_name: str
    address_line1: str
    address_line2: str | None = None
    city: str
    state: str | None = None
    postal_code: str | None = None
    country: str
    phone: str | None = None
    is_default: bool = False
    label: str = "home"
    formatted_address: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class CustomerAddressListResponse(BaseModel):
    """Customer address list response schema."""

    addresses: list[CustomerAddressResponse]
    total: int


class CustomerOrderListResponse(BaseModel):
    """Customer order list response schema."""

    orders: list[dict]  # Using dict for flexibility, can be replaced with OrderResponse
    total: int
    skip: int
    limit: int
