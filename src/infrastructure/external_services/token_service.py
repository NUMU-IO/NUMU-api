"""JWT token service implementation."""

from datetime import datetime, timedelta
from uuid import UUID

from jose import JWTError, jwt

from src.config import settings
from src.core.entities.user import User
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.core.interfaces.services.token_service import ITokenService, TokenPayload


class TokenService(ITokenService):
    """JWT token service implementation."""

    def __init__(
        self,
        secret_key: str | None = None,
        algorithm: str | None = None,
        access_token_expire_minutes: int | None = None,
        refresh_token_expire_days: int | None = None,
    ) -> None:
        self.secret_key = secret_key or settings.jwt_secret_key
        self.algorithm = algorithm or settings.jwt_algorithm
        self.access_token_expire_minutes = (
            access_token_expire_minutes or settings.access_token_expire_minutes
        )
        self.refresh_token_expire_days = (
            refresh_token_expire_days or settings.refresh_token_expire_days
        )

    def _create_token(
        self,
        user: User,
        token_type: str,
        expires_delta: timedelta,
    ) -> str:
        """Create a JWT token."""
        expire = datetime.utcnow() + expires_delta
        payload = {
            "sub": str(user.id),
            "email": str(user.email),
            "role": user.role.value,
            "token_type": token_type,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

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
                token, self.secret_key, algorithms=[self.algorithm]
            )
            return TokenPayload(
                user_id=UUID(payload["sub"]),
                email=payload["email"],
                role=payload["role"],
                exp=payload["exp"],
                token_type=payload.get("token_type", "access"),
            )
        except JWTError as e:
            if "expired" in str(e).lower():
                raise TokenExpiredError()
            raise InvalidTokenError()

    def decode_token(self, token: str) -> TokenPayload | None:
        """Decode a token without raising exceptions."""
        try:
            return self.verify_token(token)
        except Exception:
            return None


# Singleton instance
token_service = TokenService()
