"""JWT token service interface."""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.user import User


class TokenPayload:
    """Token payload data."""

    def __init__(
        self,
        user_id: UUID,
        email: str,
        role: str,
        exp: int,
        token_type: str = "access",
        iat: int = 0,
        jti: str | None = None,
        tenant_id: UUID | None = None,
        membership_id: UUID | None = None,
        perm_version: int = 0,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.exp = exp
        self.token_type = token_type
        self.iat = iat
        self.jti = jti
        self.tenant_id = tenant_id
        self.membership_id = membership_id
        self.perm_version = perm_version


class CustomerTokenPayload:
    """Customer token payload data."""

    def __init__(
        self,
        customer_id: UUID,
        store_id: UUID,
        email: str,
        exp: int,
        token_type: str = "access",
    ) -> None:
        self.customer_id = customer_id
        self.store_id = store_id
        self.email = email
        self.exp = exp
        self.token_type = token_type


class ITokenService(ABC):
    """JWT token service interface."""

    @abstractmethod
    def create_access_token(self, user: User, tenant_id: UUID | None = None) -> str:
        """Create an access token for a user.

        ``tenant_id`` is optional. When supplied, it is embedded in the
        token payload so consumers can read the tenant scope without
        re-resolving via subdomain middleware (used by the demo flow).
        """
        pass

    @abstractmethod
    def create_refresh_token(self, user: User, tenant_id: UUID | None = None) -> str:
        """Create a refresh token for a user."""
        pass

    @abstractmethod
    def create_reset_token(self, user: User) -> str:
        """Create a password reset token for a user."""
        pass

    @abstractmethod
    def create_email_verification_token(self, user: User) -> str:
        """Create an email verification token for a user (24 h expiry)."""
        pass

    @abstractmethod
    def verify_token(self, token: str) -> TokenPayload:
        """Verify and decode a token. Raises exception if invalid."""
        pass

    @abstractmethod
    def decode_token(self, token: str) -> TokenPayload | None:
        """Decode a token without raising exceptions."""
        pass

    @abstractmethod
    def create_customer_access_token(self, customer) -> str:
        """Create an access token for a customer."""
        pass

    @abstractmethod
    def create_customer_refresh_token(self, customer) -> str:
        """Create a refresh token for a customer."""
        pass

    @abstractmethod
    def verify_customer_token(self, token: str) -> CustomerTokenPayload:
        """Verify and decode a customer token. Raises exception if invalid."""
        pass

    @abstractmethod
    def decode_customer_token(self, token: str) -> CustomerTokenPayload | None:
        """Decode a customer token without raising exceptions."""
        pass
