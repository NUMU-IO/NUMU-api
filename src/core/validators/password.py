"""Password length rule.

Composition rules (upper/lower/digit) were dropped per NIST SP 800-63B;
the breached-password check lives in
``src.application.services.password_policy``, which every password-setting
use case goes through.
"""

from src.core.exceptions import ValidationError

_MIN_LENGTH = 8


def validate_password(password: str) -> None:
    """Raise ValidationError if the password is shorter than 8 characters."""
    if len(password) < _MIN_LENGTH:
        raise ValidationError(
            f"Password must be at least {_MIN_LENGTH} characters.",
            field="password",
        )
