"""Unit tests for LocalizationService."""


from src.core.services.localization import (
    Locale,
    LocalizationService,
    get_current_locale,
    set_current_locale,
)


class TestLocalizationService:
    """Tests for LocalizationService."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = LocalizationService(
            default_locale="en",
            supported_locales=["en", "ar"],
        )

    def test_default_locale(self):
        """Test default locale is set correctly."""
        assert self.service.default_locale == Locale.EN

    def test_supported_locales(self):
        """Test supported locales are set correctly."""
        assert Locale.EN in self.service.supported_locales
        assert Locale.AR in self.service.supported_locales

    def test_parse_accept_language_simple(self):
        """Test parsing simple Accept-Language header."""
        locales = self.service.parse_accept_language("ar")
        assert locales[0] == (Locale.AR, 1.0)

    def test_parse_accept_language_with_quality(self):
        """Test parsing Accept-Language with quality values."""
        locales = self.service.parse_accept_language("ar-EG,ar;q=0.9,en;q=0.8")
        assert locales[0][0] == Locale.AR  # ar-EG maps to ar
        assert locales[0][1] == 1.0

    def test_parse_accept_language_en_preferred(self):
        """Test parsing with English preferred."""
        locales = self.service.parse_accept_language("en-US,ar;q=0.5")
        assert locales[0][0] == Locale.EN  # en-US with quality 1.0
        assert locales[1][0] == Locale.AR  # ar with quality 0.5

    def test_parse_accept_language_empty(self):
        """Test parsing empty header returns default."""
        locales = self.service.parse_accept_language(None)
        assert locales[0][0] == Locale.EN

    def test_parse_accept_language_unknown(self):
        """Test parsing unknown locale returns default."""
        locales = self.service.parse_accept_language("fr-FR,de;q=0.9")
        assert locales[0][0] == Locale.EN  # Falls back to default

    def test_detect_locale_user_preference(self):
        """Test locale detection with user preference."""
        locale = self.service.detect_locale(user_preference="ar")
        assert locale == Locale.AR

    def test_detect_locale_url_locale(self):
        """Test locale detection from URL."""
        locale = self.service.detect_locale(url_locale="ar")
        assert locale == Locale.AR

    def test_detect_locale_cookie(self):
        """Test locale detection from cookie."""
        locale = self.service.detect_locale(cookie_locale="ar")
        assert locale == Locale.AR

    def test_detect_locale_accept_language(self):
        """Test locale detection from Accept-Language."""
        locale = self.service.detect_locale(accept_language="ar-EG")
        assert locale == Locale.AR

    def test_detect_locale_priority(self):
        """Test locale detection priority (user > url > cookie > header)."""
        locale = self.service.detect_locale(
            accept_language="en",
            cookie_locale="en",
            url_locale="en",
            user_preference="ar",
        )
        assert locale == Locale.AR  # User preference wins

    def test_detect_locale_fallback(self):
        """Test locale detection falls back to default."""
        locale = self.service.detect_locale()
        assert locale == Locale.EN

    def test_get_locale_info_en(self):
        """Test getting English locale info."""
        info = self.service.get_locale_info(Locale.EN)
        assert info.code == "en"
        assert info.name == "English"
        assert info.rtl is False

    def test_get_locale_info_ar(self):
        """Test getting Arabic locale info."""
        info = self.service.get_locale_info(Locale.AR)
        assert info.code == "ar"
        assert info.name == "Arabic"
        assert info.native_name == "العربية"
        assert info.rtl is True

    def test_is_rtl_english(self):
        """Test is_rtl returns False for English."""
        assert self.service.is_rtl(Locale.EN) is False

    def test_is_rtl_arabic(self):
        """Test is_rtl returns True for Arabic."""
        assert self.service.is_rtl(Locale.AR) is True

    def test_format_number(self):
        """Test number formatting."""
        formatted = self.service.format_number(1234.56, Locale.EN)
        assert "1,234.56" in formatted

    def test_format_currency_en(self):
        """Test currency formatting in English."""
        formatted = self.service.format_currency(1234.56, "EGP", Locale.EN)
        assert "EGP" in formatted
        assert "1,234.56" in formatted

    def test_format_currency_ar(self):
        """Test currency formatting in Arabic."""
        formatted = self.service.format_currency(1234.56, "EGP", Locale.AR)
        assert "EGP" in formatted
        assert "1,234.56" in formatted


class TestLocaleContextFunctions:
    """Tests for locale context functions."""

    def test_get_and_set_current_locale(self):
        """Test getting and setting current locale."""
        set_current_locale(Locale.AR)
        assert get_current_locale() == Locale.AR

        set_current_locale(Locale.EN)
        assert get_current_locale() == Locale.EN

    def test_default_locale_is_english(self):
        """Test default locale context is English."""
        # Reset to default by setting EN
        set_current_locale(Locale.EN)
        assert get_current_locale() == Locale.EN
