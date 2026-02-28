"""Password policy validator.

Policy: minimum 12 characters, at least one uppercase letter,
one lowercase letter, and one digit.
"""

import re

from src.core.exceptions import ValidationError

_MIN_LENGTH = 12
_RE_UPPER = re.compile(r"[A-Z]")
_RE_LOWER = re.compile(r"[a-z]")
_RE_DIGIT = re.compile(r"\d")


def validate_password(password: str) -> None:
    """Raise ValidationError if password doesn't meet policy requirements.

    Rules:
    - At least 12 characters
    - At least one uppercase letter (A-Z)
    - At least one lowercase letter (a-z)
    - At least one digit (0-9)
    """
    errors: list[str] = []

    if len(password) < _MIN_LENGTH:
        errors.append(f"at least {_MIN_LENGTH} characters")
    if not _RE_UPPER.search(password):
        errors.append("at least one uppercase letter")
    if not _RE_LOWER.search(password):
        errors.append("at least one lowercase letter")
    if not _RE_DIGIT.search(password):
        errors.append("at least one digit")

    if errors:
        raise ValidationError(
            f"Password must contain {', '.join(errors)}.",
            field="password",
        )
