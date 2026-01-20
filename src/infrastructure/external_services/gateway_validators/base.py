"""Base classes for gateway credential validation.

This module defines the abstract base class and common types for
all gateway validators.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ValidationStatus(str, Enum):
    """Status of credential validation."""
    VALID = "valid"
    INVALID = "invalid"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class ValidationResult:
    """Result of credential validation.
    
    Attributes:
        is_valid: Whether the credentials are valid.
        status: Detailed status of the validation.
        message: Human-readable message about the result.
        details: Additional details from the provider (e.g., account info).
        error_code: Provider-specific error code if validation failed.
    """
    is_valid: bool
    status: ValidationStatus
    message: str
    details: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    
    @classmethod
    def success(cls, message: str = "Credentials validated successfully", details: Optional[dict] = None) -> "ValidationResult":
        """Create a successful validation result."""
        return cls(
            is_valid=True,
            status=ValidationStatus.VALID,
            message=message,
            details=details,
        )
    
    @classmethod
    def failure(cls, message: str, error_code: Optional[str] = None, details: Optional[dict] = None) -> "ValidationResult":
        """Create a failed validation result."""
        return cls(
            is_valid=False,
            status=ValidationStatus.INVALID,
            message=message,
            error_code=error_code,
            details=details,
        )
    
    @classmethod
    def error(cls, message: str, error_code: Optional[str] = None) -> "ValidationResult":
        """Create an error validation result (validation could not be performed)."""
        return cls(
            is_valid=False,
            status=ValidationStatus.ERROR,
            message=message,
            error_code=error_code,
        )
    
    @classmethod
    def timeout(cls, message: str = "Validation request timed out") -> "ValidationResult":
        """Create a timeout validation result."""
        return cls(
            is_valid=False,
            status=ValidationStatus.TIMEOUT,
            message=message,
        )


class GatewayValidatorError(Exception):
    """Base exception for gateway validator errors."""
    pass


class GatewayValidator(ABC):
    """Abstract base class for gateway credential validators.
    
    Each external service (payment gateway, shipping carrier, etc.) should
    have its own validator implementation that inherits from this class.
    
    The validator is responsible for:
    1. Validating the structure of provided credentials
    2. Testing the credentials against the provider's API
    3. Returning detailed validation results
    
    Example:
        class FawryValidator(GatewayValidator):
            async def validate(self, credentials: dict) -> ValidationResult:
                # Validate with Fawry API
                ...
    """
    
    # Timeout for validation requests (in seconds)
    DEFAULT_TIMEOUT = 30
    
    @property
    @abstractmethod
    def service_name(self) -> str:
        """Return the name of the service this validator handles."""
        pass
    
    @property
    @abstractmethod
    def required_fields(self) -> list[str]:
        """Return list of required credential fields."""
        pass
    
    @property
    def optional_fields(self) -> list[str]:
        """Return list of optional credential fields."""
        return []
    
    def validate_structure(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate that all required fields are present.
        
        Args:
            credentials: Dictionary of credentials to validate.
        
        Returns:
            ValidationResult indicating if structure is valid.
        """
        missing_fields = []
        for field in self.required_fields:
            if field not in credentials or not credentials[field]:
                missing_fields.append(field)
        
        if missing_fields:
            return ValidationResult.failure(
                message=f"Missing required fields: {', '.join(missing_fields)}",
                error_code="MISSING_FIELDS",
                details={"missing_fields": missing_fields},
            )
        
        return ValidationResult.success(message="Credential structure is valid")
    
    @abstractmethod
    async def validate(self, credentials: dict[str, Any]) -> ValidationResult:
        """Validate credentials with the external service.
        
        This method should:
        1. First call validate_structure() to check required fields
        2. Make an API call to the provider to verify credentials
        3. Return a ValidationResult with the outcome
        
        Args:
            credentials: Dictionary containing the service credentials.
        
        Returns:
            ValidationResult with validation outcome.
        """
        pass
    
    async def test_connection(self, credentials: dict[str, Any]) -> ValidationResult:
        """Test the connection to the service with provided credentials.
        
        This is an alias for validate() but may be overridden to perform
        a lighter-weight connection test.
        
        Args:
            credentials: Dictionary containing the service credentials.
        
        Returns:
            ValidationResult with connection test outcome.
        """
        return await self.validate(credentials)
    
    def get_display_info(self, credentials: dict[str, Any]) -> dict[str, str]:
        """Get safe display information from credentials.
        
        Returns masked or partial credential information suitable for
        display in the UI.
        
        Args:
            credentials: Dictionary containing the service credentials.
        
        Returns:
            Dictionary with safe display values.
        """
        display_info = {}
        for field in self.required_fields + self.optional_fields:
            if field in credentials and credentials[field]:
                value = str(credentials[field])
                # Mask sensitive values
                if any(sensitive in field.lower() for sensitive in ["key", "secret", "password", "token"]):
                    display_info[field] = self._mask_value(value)
                else:
                    display_info[field] = value
        return display_info
    
    def _mask_value(self, value: str, visible_chars: int = 4) -> str:
        """Mask a sensitive value for display.
        
        Args:
            value: The value to mask.
            visible_chars: Number of characters to show at the end.
        
        Returns:
            Masked string.
        """
        if len(value) <= visible_chars:
            return "*" * len(value)
        return "*" * (len(value) - visible_chars) + value[-visible_chars:]
