"""Localization service for multi-language support.

Provides locale detection from HTTP headers and locale context
for request processing. Supports Arabic (RTL) and English.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum

from src.config import settings


class Locale(str, Enum):
    """Supported locales."""

    EN = "en"
    AR = "ar"


# Context variable for current request locale
_current_locale: ContextVar[Locale] = ContextVar("current_locale", default=Locale.EN)


@dataclass(frozen=True)
class LocaleInfo:
    """Information about a locale."""

    code: str
    name: str
    native_name: str
    rtl: bool
    date_format: str
    number_format: str


# Locale metadata
LOCALE_INFO: dict[Locale, LocaleInfo] = {
    Locale.EN: LocaleInfo(
        code="en",
        name="English",
        native_name="English",
        rtl=False,
        date_format="%B %d, %Y",
        number_format="{:,.2f}",
    ),
    Locale.AR: LocaleInfo(
        code="ar",
        name="Arabic",
        native_name="العربية",
        rtl=True,
        date_format="%d %B %Y",  # Arabic date format
        number_format="{:,.2f}",
    ),
}


class LocalizationService:
    """Service for handling localization and locale detection."""

    def __init__(
        self,
        default_locale: str | None = None,
        supported_locales: list[str] | None = None,
    ):
        """Initialize the localization service.

        Args:
            default_locale: Default locale code (from settings if not provided)
            supported_locales: List of supported locale codes
        """
        self.default_locale = Locale(default_locale or settings.default_locale)
        self.supported_locales = [
            Locale(loc) for loc in (supported_locales or settings.supported_locales)
        ]

    def parse_accept_language(self, header: str | None) -> list[tuple[Locale, float]]:
        """Parse Accept-Language header into list of (locale, quality) tuples.

        Args:
            header: Accept-Language header value

        Returns:
            List of (Locale, quality) tuples sorted by quality descending
        """
        if not header:
            return [(self.default_locale, 1.0)]

        locales = []

        # Parse header like "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
        parts = header.replace(" ", "").split(",")

        for part in parts:
            if ";q=" in part:
                lang, q = part.split(";q=")
                try:
                    quality = float(q)
                except ValueError:
                    quality = 0.0
            else:
                lang = part
                quality = 1.0

            # Extract base language (ar-EG -> ar)
            base_lang = lang.split("-")[0].lower()

            # Map to supported locale
            try:
                locale = Locale(base_lang)
                if locale in self.supported_locales:
                    locales.append((locale, quality))
            except ValueError:
                # Unknown locale, skip
                continue

        # Sort by quality descending
        locales.sort(key=lambda x: x[1], reverse=True)

        # Add default as fallback
        if not locales:
            locales.append((self.default_locale, 1.0))

        return locales

    def detect_locale(
        self,
        accept_language: str | None = None,
        url_locale: str | None = None,
        cookie_locale: str | None = None,
        user_preference: str | None = None,
    ) -> Locale:
        """Detect the best locale for the request.

        Priority order:
        1. User preference (stored in profile)
        2. URL parameter/path segment
        3. Cookie value
        4. Accept-Language header
        5. Default locale

        Args:
            accept_language: Accept-Language header value
            url_locale: Locale from URL (e.g., /ar/products)
            cookie_locale: Locale from cookie
            user_preference: User's stored locale preference

        Returns:
            Best matching Locale
        """
        # 1. User preference
        if user_preference:
            try:
                locale = Locale(user_preference.lower())
                if locale in self.supported_locales:
                    return locale
            except ValueError:
                pass

        # 2. URL locale
        if url_locale:
            try:
                locale = Locale(url_locale.lower())
                if locale in self.supported_locales:
                    return locale
            except ValueError:
                pass

        # 3. Cookie locale
        if cookie_locale:
            try:
                locale = Locale(cookie_locale.lower())
                if locale in self.supported_locales:
                    return locale
            except ValueError:
                pass

        # 4. Accept-Language header
        if accept_language:
            locales = self.parse_accept_language(accept_language)
            if locales:
                return locales[0][0]

        # 5. Default
        return self.default_locale

    def get_locale_info(self, locale: Locale | None = None) -> LocaleInfo:
        """Get information about a locale.

        Args:
            locale: Locale to get info for (current if None)

        Returns:
            LocaleInfo for the locale
        """
        if locale is None:
            locale = get_current_locale()
        return LOCALE_INFO.get(locale, LOCALE_INFO[Locale.EN])

    def is_rtl(self, locale: Locale | None = None) -> bool:
        """Check if locale is right-to-left.

        Args:
            locale: Locale to check (current if None)

        Returns:
            True if RTL locale
        """
        return self.get_locale_info(locale).rtl

    def format_number(
        self,
        value: int | float,
        locale: Locale | None = None,
        decimal_places: int = 2,
    ) -> str:
        """Format a number for display in the locale.

        Args:
            value: Number to format
            locale: Locale for formatting
            decimal_places: Number of decimal places

        Returns:
            Formatted number string
        """
        self.get_locale_info(locale)

        # Arabic numerals conversion (optional, can be enabled)
        # For now, use Western Arabic numerals which are common in Egypt
        format_str = f"{{:,.{decimal_places}f}}"
        return format_str.format(value)

    def format_currency(
        self,
        amount: int | float,
        currency: str = "EGP",
        locale: Locale | None = None,
    ) -> str:
        """Format a currency amount for display.

        Args:
            amount: Amount to format
            currency: Currency code
            locale: Locale for formatting

        Returns:
            Formatted currency string
        """
        formatted = self.format_number(amount, locale)

        if locale == Locale.AR or (locale is None and get_current_locale() == Locale.AR):
            # Arabic: number followed by currency
            return f"{formatted} {currency}"
        else:
            # English: currency followed by number
            return f"{currency} {formatted}"


# Context management functions
def get_current_locale() -> Locale:
    """Get the current request locale from context.

    Returns:
        Current Locale from context variable
    """
    return _current_locale.get()


def set_current_locale(locale: Locale) -> None:
    """Set the current request locale in context.

    Args:
        locale: Locale to set
    """
    _current_locale.set(locale)


# FastAPI dependency
async def get_locale_from_request(
    accept_language: str | None = None,
    locale_cookie: str | None = None,
) -> Locale:
    """FastAPI dependency to get locale from request.

    This can be used as a dependency in route handlers:

    @router.get("/products")
    async def list_products(locale: Locale = Depends(get_locale_from_request)):
        ...

    Args:
        accept_language: Accept-Language header (injected by FastAPI)
        locale_cookie: Locale cookie value (injected by FastAPI)

    Returns:
        Detected Locale
    """
    service = LocalizationService()
    locale = service.detect_locale(
        accept_language=accept_language,
        cookie_locale=locale_cookie,
    )
    set_current_locale(locale)
    return locale


# Singleton service instance
_localization_service: LocalizationService | None = None


def get_localization_service() -> LocalizationService:
    """Get the localization service singleton.

    Returns:
        LocalizationService instance
    """
    global _localization_service
    if _localization_service is None:
        _localization_service = LocalizationService()
    return _localization_service
