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
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.exp = exp
        self.token_type = token_type


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
    def create_access_token(self, user: User) -> str:
        """Create an access token for a user."""
        ...

    @abstractmethod
    def create_refresh_token(self, user: User) -> str:
        """Create a refresh token for a user."""
        ...

    @abstractmethod
    def verify_token(self, token: str) -> TokenPayload:
        """Verify and decode a token. Raises exception if invalid."""
        ...

    @abstractmethod
    def decode_token(self, token: str) -> TokenPayload | None:
        """Decode a token without raising exceptions."""
        ...

    @abstractmethod
    def create_customer_access_token(self, customer) -> str:
        """Create an access token for a customer."""
        ...

    @abstractmethod
    def create_customer_refresh_token(self, customer) -> str:
        """Create a refresh token for a customer."""
        ...

    @abstractmethod
    def verify_customer_token(self, token: str) -> CustomerTokenPayload:
        """Verify and decode a customer token. Raises exception if invalid."""
        ...

    @abstractmethod
    def decode_customer_token(self, token: str) -> CustomerTokenPayload | None:
        """Decode a customer token without raising exceptions."""
        ...

