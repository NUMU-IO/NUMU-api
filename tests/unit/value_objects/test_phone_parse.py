"""Unit tests for the strict ``PhoneNumber.parse`` classmethod."""

import pytest

from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber


class TestPhoneNumberParse:
    """Strict E.164 parsing — bad input must raise."""

    def test_parse_egyptian_local(self):
        phone = PhoneNumber.parse("01001234567", default_region="EG")
        assert phone.value == "+201001234567"
        assert phone.e164 == "+201001234567"
        assert phone.country_code == "EG"

    def test_parse_egyptian_e164(self):
        phone = PhoneNumber.parse("+201001234567")
        assert phone.value == "+201001234567"
        assert phone.country_code == "EG"

    def test_parse_saudi_local(self):
        phone = PhoneNumber.parse("0501234567", default_region="SA")
        assert phone.value.startswith("+966")
        assert phone.country_code == "SA"

    def test_parse_uae_local(self):
        phone = PhoneNumber.parse("0501234567", default_region="AE")
        assert phone.value.startswith("+971")
        assert phone.country_code == "AE"

    def test_parse_us_e164(self):
        phone = PhoneNumber.parse("+12025551234")
        assert phone.value == "+12025551234"
        assert phone.country_code == "US"

    def test_parse_strips_whitespace(self):
        phone = PhoneNumber.parse("  +20 100 123 4567  ")
        assert phone.value == "+201001234567"

    def test_parse_rejects_empty(self):
        with pytest.raises(InvalidPhoneError):
            PhoneNumber.parse("")

    def test_parse_rejects_whitespace_only(self):
        with pytest.raises(InvalidPhoneError):
            PhoneNumber.parse("   ")

    def test_parse_rejects_garbage(self):
        with pytest.raises(InvalidPhoneError):
            PhoneNumber.parse("not-a-phone-number")

    def test_parse_rejects_too_short(self):
        with pytest.raises(InvalidPhoneError):
            PhoneNumber.parse("123", default_region="EG")

    def test_parse_rejects_wrong_region_format(self):
        # A US number passed without country code defaulting to EG won't
        # parse as a valid Egyptian number.
        with pytest.raises(InvalidPhoneError):
            PhoneNumber.parse("2025551234", default_region="EG")

    def test_formatted_international(self):
        phone = PhoneNumber.parse("+201001234567")
        # The exact spacing comes from libphonenumber's grouping rules;
        # we only assert it contains the dialing-code prefix and isn't the
        # raw E.164.
        assert phone.formatted_international.startswith("+20")
        assert " " in phone.formatted_international

    def test_str_returns_e164(self):
        phone = PhoneNumber.parse("01001234567", default_region="EG")
        assert str(phone) == "+201001234567"


class TestPhoneField:
    """The ``PhoneField`` Pydantic type used by request schemas."""

    def test_string_e164(self):
        from pydantic import BaseModel

        from src.application.dto.phone_field import PhoneField

        class M(BaseModel):
            phone: PhoneField = None  # type: ignore[assignment]

        m = M(phone="+201001234567")
        assert m.phone == "+201001234567"

    def test_string_local_with_default_region(self):
        from pydantic import BaseModel

        from src.application.dto.phone_field import PhoneField

        class M(BaseModel):
            phone: PhoneField = None  # type: ignore[assignment]

        # Default region inside the field is EG, so an EG-shaped local
        # number normalises to +20…
        m = M(phone="01001234567")
        assert m.phone == "+201001234567"

    def test_dict_country_code_local(self):
        from pydantic import BaseModel

        from src.application.dto.phone_field import PhoneField

        class M(BaseModel):
            phone: PhoneField = None  # type: ignore[assignment]

        m = M(phone={"country_code": "SA", "local": "0501234567"})
        assert m.phone.startswith("+966")

    def test_none_passthrough(self):
        from pydantic import BaseModel

        from src.application.dto.phone_field import PhoneField

        class M(BaseModel):
            phone: PhoneField = None  # type: ignore[assignment]

        assert M(phone=None).phone is None
        assert M(phone="").phone is None

    def test_invalid_raises_validation_error(self):
        from pydantic import BaseModel, ValidationError

        from src.application.dto.phone_field import PhoneField

        class M(BaseModel):
            phone: PhoneField = None  # type: ignore[assignment]

        with pytest.raises(ValidationError):
            M(phone="not-a-number")
