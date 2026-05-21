"""Generate RSA key pair for JWT RS256 signing.

Usage:
    python scripts/generate_jwt_keys.py

Outputs PEM-encoded private and public keys that should be set as
JWT_PRIVATE_KEY and JWT_PUBLIC_KEY environment variables (or in .env).

Multi-line PEM values must use literal \\n in .env files:
    JWT_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\\nMIIE...\\n-----END RSA PRIVATE KEY-----"
"""

import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_rsa_key_pair(key_size: int = 2048) -> tuple[str, str]:
    """Generate an RSA private/public key pair.

    Args:
        key_size: RSA key size in bits (default 2048).

    Returns:
        Tuple of (private_key_pem, public_key_pem) as strings.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_pem, public_pem


def to_env_value(pem: str) -> str:
    """Convert a multi-line PEM string to a single-line .env-safe value."""
    return pem.replace("\n", "\\n").strip()


def main() -> None:
    key_size = 2048
    if len(sys.argv) > 1:
        try:
            key_size = int(sys.argv[1])
        except ValueError:
            print(f"Invalid key size: {sys.argv[1]}", file=sys.stderr)
            sys.exit(1)

    private_pem, public_pem = generate_rsa_key_pair(key_size)

    print("=" * 60)
    print("RSA Key Pair Generated Successfully")
    print("=" * 60)
    print()
    print("Add the following to your .env file:")
    print()
    print(f'JWT_PRIVATE_KEY="{to_env_value(private_pem)}"')
    print()
    print(f'JWT_PUBLIC_KEY="{to_env_value(public_pem)}"')
    print()
    print("=" * 60)
    print()
    print("--- Private Key (PEM) ---")
    print(private_pem)
    print("--- Public Key (PEM) ---")
    print(public_pem)


if __name__ == "__main__":
    main()
