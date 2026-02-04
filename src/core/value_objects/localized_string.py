"""LocalizedString value object for multi-language text support."""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class LocalizedString(BaseModel):
    """Immutable value object for storing localized text.

    Supports multiple languages with automatic fallback to English
    when the requested locale is not available.

    Example:
        name = LocalizedString(en="Product Name", ar="اسم المنتج")
        print(name.get("ar"))  # اسم المنتج
        print(name.get("fr"))  # Product Name (falls back to en)
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    en: str | None = None
    ar: str | None = None

    @model_validator(mode="before")
    @classmethod
    def ensure_at_least_one_value(cls, data: Any) -> Any:
        """Ensure at least one locale has a value."""
        if isinstance(data, dict):
            # Check if any locale has a non-empty value
            has_value = any(
                v for k, v in data.items() if isinstance(v, str) and v.strip()
            )
            if not has_value:
                raise ValueError(
                    "LocalizedString must have at least one non-empty value"
                )
        return data

    def get(self, locale: str = "en", fallback_locale: str = "en") -> str | None:
        """Get text for the specified locale with fallback.

        Args:
            locale: Preferred locale code (e.g., "ar", "en")
            fallback_locale: Fallback locale if preferred not available

        Returns:
            Text in the preferred locale, or fallback, or None
        """
        # Try preferred locale
        value = getattr(self, locale, None)
        if value:
            return value

        # Try fallback locale
        if locale != fallback_locale:
            value = getattr(self, fallback_locale, None)
            if value:
                return value

        # Return first available value
        if self.en:
            return self.en
        if self.ar:
            return self.ar

        return None

    def get_required(self, locale: str = "en", fallback_locale: str = "en") -> str:
        """Get text for the specified locale, raising if not found.

        Args:
            locale: Preferred locale code
            fallback_locale: Fallback locale

        Returns:
            Text in the preferred or fallback locale

        Raises:
            ValueError: If no text is available
        """
        value = self.get(locale, fallback_locale)
        if value is None:
            raise ValueError(f"No text available for locale {locale}")
        return value

    def has_locale(self, locale: str) -> bool:
        """Check if a specific locale has a value.

        Args:
            locale: Locale code to check

        Returns:
            True if locale has a non-empty value
        """
        value = getattr(self, locale, None)
        return bool(value and value.strip())

    @property
    def available_locales(self) -> list[str]:
        """Get list of locales that have values.

        Returns:
            List of locale codes with non-empty values
        """
        locales = []
        if self.en:
            locales.append("en")
        if self.ar:
            locales.append("ar")
        return locales

    @property
    def is_rtl(self) -> bool:
        """Check if the primary text direction is RTL.

        Returns True if Arabic text is present and English is not,
        or if Arabic is the primary locale.
        """
        return bool(self.ar and not self.en)

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary with only non-empty values.

        Returns:
            Dictionary of locale -> text
        """
        result = {}
        if self.en:
            result["en"] = self.en
        if self.ar:
            result["ar"] = self.ar
        return result

    @classmethod
    def from_dict(cls, data: dict[str, str | None]) -> "LocalizedString":
        """Create from dictionary.

        Args:
            data: Dictionary of locale -> text

        Returns:
            LocalizedString instance
        """
        return cls(**{k: v for k, v in data.items() if v})

    @classmethod
    def from_single(cls, text: str, locale: str = "en") -> "LocalizedString":
        """Create from a single text value.

        Args:
            text: The text value
            locale: The locale for this text

        Returns:
            LocalizedString instance
        """
        return cls(**{locale: text})

    def with_translation(self, locale: str, text: str) -> "LocalizedString":
        """Create a new LocalizedString with an added/updated translation.

        Since LocalizedString is immutable, this returns a new instance.

        Args:
            locale: Locale code to add/update
            text: Text for the locale

        Returns:
            New LocalizedString with the translation
        """
        data = self.to_dict()
        data[locale] = text
        return LocalizedString(**data)

    def __str__(self) -> str:
        """String representation using English or first available."""
        return self.get("en") or ""

    def __repr__(self) -> str:
        """Detailed representation."""
        parts = []
        if self.en:
            parts.append(f"en={self.en!r}")
        if self.ar:
            parts.append(f"ar={self.ar!r}")
        return f"LocalizedString({', '.join(parts)})"

    def __bool__(self) -> bool:
        """Boolean evaluation - True if any locale has a value."""
        return bool(self.en or self.ar)

    def __hash__(self) -> int:
        """Hash for use in sets and dict keys."""
        return hash((self.en, self.ar))
