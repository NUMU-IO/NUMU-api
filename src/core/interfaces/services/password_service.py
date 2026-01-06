"""Password hashing service interface."""

from abc import ABC, abstractmethod


class IPasswordService(ABC):
    """Password hashing service interface."""

    @abstractmethod
    def hash_password(self, password: str) -> str:
        """Hash a password."""
        ...

    @abstractmethod
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        ...
