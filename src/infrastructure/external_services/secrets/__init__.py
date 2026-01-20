"""Secrets management service for secure credential storage.

This module provides encryption and decryption services for sensitive credentials
using industry-standard AES-256 encryption.

SECURITY ARCHITECTURE:
- Master encryption key stored in environment variable (CREDENTIAL_ENCRYPTION_KEY)
- Individual credentials encrypted with derived keys
- Key rotation support via key_id versioning
- In production, integrate with AWS Secrets Manager, HashiCorp Vault, etc.
"""

from .secrets_manager import SecretsManager, SecretsManagerError

__all__ = ["SecretsManager", "SecretsManagerError"]
