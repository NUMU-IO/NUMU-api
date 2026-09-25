"""The password policy: 8 characters minimum, mixed case and a digit."""

import pytest

from src.core.exceptions import ValidationError
from src.core.validators.password import validate_password


def test_eight_characters_with_mixed_case_and_a_digit_pass():
    validate_password("Partner1")


@pytest.mark.parametrize("password", ["Partne1", "partner12", "PARTNER12", "Partnerxx"])
def test_short_or_weak_passwords_fail(password):
    with pytest.raises(ValidationError):
        validate_password(password)
