"""JWT token service implementation using RS256 asymmetric signing."""

from datetime import datetime, timedelta
from uuid import UUID

import jwt
from jwt.exceptions import ExpiredSignatureError, PyJWTError

from src.config import settings
from src.core.entities.user import User
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.core.interfaces.services.token_service import (
    CustomerTokenPayload,
    ITokenService,
    TokenPayload,
)


class TokenService(ITokenService):
    """JWT token service using RS256 asymmetric key pair.

    Tokens are signed with the private key and verified with the public key.
    This allows external services to verify tokens using only the public key,
    without needing access to the signing secret.
    """

    def __init__(
        self,
        private_key: str | None = None,
        public_key: str | None = None,
        algorithm: str | None = None,
        access_token_expire_minutes: int | None = None,
        refresh_token_expire_days: int | None = None,
    ) -> None:
        self.algorithm = algorithm or settings.jwt_algorithm
        self.private_key = private_key or settings.jwt_private_key
        self.public_key = public_key or settings.jwt_public_key
        self.access_token_expire_minutes = (
            access_token_expire_minutes or settings.access_token_expire_minutes
        )
        self.refresh_token_expire_days = (
            refresh_token_expire_days or settings.refresh_token_expire_days
        )

    def _get_signing_key(self) -> str:
        """Get the key used for signing tokens."""
        return self.private_key

    def _get_verification_key(self) -> str:
        """Get the key used for verifying tokens."""
        return self.public_key

    def _create_token(
        self,
        user: User,
        token_type: str,
        expires_delta: timedelta,
    ) -> str:
        """Create a JWT token."""
        expire = datetime.utcnow() + expires_delta
        # Handle role as enum or string (for compatibility with DB enum names)
        role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
        payload = {
            "sub": str(user.id),
            "email": str(user.email),
            "role": role_value,
            "token_type": token_type,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, self._get_signing_key(), algorithm=self.algorithm)

    def create_access_token(self, user: User) -> str:
        """Create an access token for a user."""
        expires_delta = timedelta(minutes=self.access_token_expire_minutes)
        return self._create_token(user, "access", expires_delta)

    def create_refresh_token(self, user: User) -> str:
        """Create a refresh token for a user."""
        expires_delta = timedelta(days=self.refresh_token_expire_days)
        return self._create_token(user, "refresh", expires_delta)

    def verify_token(self, token: str) -> TokenPayload:
        """Verify and decode a token. Raises exception if invalid."""
        try:
            payload = jwt.decode(
                token, self._get_verification_key(), algorithms=[self.algorithm]
            )
            return TokenPayload(
                user_id=UUID(payload["sub"]),
                email=payload["email"],
                role=payload["role"],
                exp=payload["exp"],
                token_type=payload.get("token_type", "access"),
            )
        except ExpiredSignatureError:
            raise TokenExpiredError()
        except PyJWTError:
            raise InvalidTokenError()

    def decode_token(self, token: str) -> TokenPayload | None:
        """Decode a token without raising exceptions."""
        try:
            return self.verify_token(token)
        except Exception:
            return None

    # Customer token methods

    def _create_customer_token(
        self,
        customer,
        token_type: str,
        expires_delta: timedelta,
    ) -> str:
        """Create a JWT token for a customer."""
        expire = datetime.utcnow() + expires_delta
        payload = {
            "sub": str(customer.id),
            "store_id": str(customer.store_id),
            "email": str(customer.email),
            "token_type": token_type,
            "customer": True,  # Flag to identify customer tokens
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, self._get_signing_key(), algorithm=self.algorithm)

    def create_customer_access_token(self, customer) -> str:
        """Create an access token for a customer."""
        expires_delta = timedelta(minutes=self.access_token_expire_minutes)
        return self._create_customer_token(customer, "access", expires_delta)

    def create_customer_refresh_token(self, customer) -> str:
        """Create a refresh token for a customer."""
        expires_delta = timedelta(days=self.refresh_token_expire_days)
        return self._create_customer_token(customer, "refresh", expires_delta)

    def verify_customer_token(self, token: str) -> CustomerTokenPayload:
        """Verify and decode a customer token. Raises exception if invalid."""
        try:
            payload = jwt.decode(
                token, self._get_verification_key(), algorithms=[self.algorithm]
            )
            if not payload.get("customer"):
                raise InvalidTokenError()
            return CustomerTokenPayload(
                customer_id=UUID(payload["sub"]),
                store_id=UUID(payload["store_id"]),
                email=payload["email"],
                exp=payload["exp"],
                token_type=payload.get("token_type", "access"),
            )
        except ExpiredSignatureError:
            raise TokenExpiredError()
        except PyJWTError:
            raise InvalidTokenError()

    def decode_customer_token(self, token: str) -> CustomerTokenPayload | None:
        """Decode a customer token without raising exceptions."""
        try:
            return self.verify_customer_token(token)
        except Exception:
            return None


# Singleton instance
token_service = TokenService()
