"""Password hashing service implementation."""

from passlib.context import CryptContext

from src.core.interfaces.services.password_service import IPasswordService


class PasswordService(IPasswordService):
    """Password hashing service using bcrypt."""

    def __init__(self) -> None:
        self._context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt."""
        return self._context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return self._context.verify(plain_password, hashed_password)


# Singleton instance
password_service = PasswordService()
