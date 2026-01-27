"""Unit tests for LocalizedString value object."""

import pytest

from src.core.value_objects.localized_string import LocalizedString


class TestLocalizedString:
    """Tests for LocalizedString value object."""

    def test_create_with_english(self):
        """Test creating LocalizedString with English text."""
        text = LocalizedString(en="Hello World")
        assert text.en == "Hello World"
        assert text.ar is None

    def test_create_with_arabic(self):
        """Test creating LocalizedString with Arabic text."""
        text = LocalizedString(ar="مرحبا بالعالم")
        assert text.ar == "مرحبا بالعالم"
        assert text.en is None

    def test_create_with_both_languages(self):
        """Test creating LocalizedString with both languages."""
        text = LocalizedString(en="Product Name", ar="اسم المنتج")
        assert text.en == "Product Name"
        assert text.ar == "اسم المنتج"

    def test_get_preferred_locale(self):
        """Test getting text for preferred locale."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        assert text.get("en") == "Hello"
        assert text.get("ar") == "مرحبا"

    def test_get_with_fallback(self):
        """Test getting text falls back to English."""
        text = LocalizedString(en="Hello")
        assert text.get("ar") == "Hello"  # Falls back to en
        assert text.get("fr") == "Hello"  # Unknown locale falls back

    def test_get_with_custom_fallback(self):
        """Test getting text with custom fallback locale."""
        text = LocalizedString(ar="مرحبا")
        assert text.get("en", fallback_locale="ar") == "مرحبا"

    def test_get_required(self):
        """Test get_required returns text."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        assert text.get_required("en") == "Hello"
        assert text.get_required("ar") == "مرحبا"

    def test_has_locale(self):
        """Test checking if locale has value."""
        text = LocalizedString(en="Hello")
        assert text.has_locale("en") is True
        assert text.has_locale("ar") is False

    def test_available_locales(self):
        """Test getting available locales."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        locales = text.available_locales
        assert "en" in locales
        assert "ar" in locales

    def test_available_locales_single(self):
        """Test available locales with single language."""
        text = LocalizedString(en="Hello")
        locales = text.available_locales
        assert locales == ["en"]

    def test_is_rtl_arabic_only(self):
        """Test is_rtl returns True for Arabic-only text."""
        text = LocalizedString(ar="مرحبا")
        assert text.is_rtl is True

    def test_is_rtl_with_english(self):
        """Test is_rtl returns False when English present."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        assert text.is_rtl is False

    def test_to_dict(self):
        """Test converting to dictionary."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        data = text.to_dict()
        assert data == {"en": "Hello", "ar": "مرحبا"}

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        text = LocalizedString(en="Hello")
        data = text.to_dict()
        assert data == {"en": "Hello"}
        assert "ar" not in data

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {"en": "Hello", "ar": "مرحبا"}
        text = LocalizedString.from_dict(data)
        assert text.en == "Hello"
        assert text.ar == "مرحبا"

    def test_from_single(self):
        """Test creating from single text value."""
        text = LocalizedString.from_single("Hello", "en")
        assert text.en == "Hello"
        assert text.ar is None

    def test_from_single_arabic(self):
        """Test creating from single Arabic text."""
        text = LocalizedString.from_single("مرحبا", "ar")
        assert text.ar == "مرحبا"
        assert text.en is None

    def test_with_translation(self):
        """Test adding translation creates new instance."""
        text = LocalizedString(en="Hello")
        new_text = text.with_translation("ar", "مرحبا")

        assert text.ar is None  # Original unchanged
        assert new_text.ar == "مرحبا"
        assert new_text.en == "Hello"

    def test_str_representation(self):
        """Test string representation uses English."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        assert str(text) == "Hello"

    def test_str_representation_arabic_only(self):
        """Test string representation with Arabic only falls back to Arabic."""
        text = LocalizedString(ar="مرحبا")
        # get("en") falls back to Arabic when English is not available
        assert str(text) == "مرحبا"

    def test_repr(self):
        """Test detailed representation."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        repr_str = repr(text)
        assert "LocalizedString" in repr_str
        assert "Hello" in repr_str
        assert "مرحبا" in repr_str

    def test_bool_true(self):
        """Test boolean evaluation with value."""
        text = LocalizedString(en="Hello")
        assert bool(text) is True

    def test_hash(self):
        """Test LocalizedString is hashable."""
        text = LocalizedString(en="Hello", ar="مرحبا")
        text_set = {text}
        assert text in text_set

    def test_equality(self):
        """Test LocalizedString equality."""
        text1 = LocalizedString(en="Hello", ar="مرحبا")
        text2 = LocalizedString(en="Hello", ar="مرحبا")
        assert text1 == text2

    def test_inequality(self):
        """Test LocalizedString inequality."""
        text1 = LocalizedString(en="Hello")
        text2 = LocalizedString(en="World")
        assert text1 != text2

    def test_immutability(self):
        """Test LocalizedString is immutable."""
        text = LocalizedString(en="Hello")
        with pytest.raises(Exception):
            text.en = "World"
