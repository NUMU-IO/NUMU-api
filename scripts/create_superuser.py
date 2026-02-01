#!/usr/bin/env python
"""Create or promote a superuser for admin access.

Usage:
    python -m scripts.create_superuser
    python -m scripts.create_superuser --email admin@example.com --password secret123
    python -m scripts.create_superuser --promote user@example.com
"""

import argparse
import asyncio
import re
import sys
from getpass import getpass
from uuid import uuid4

from sqlalchemy import select, text

from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.external_services.password_service import password_service


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_password(password: str) -> tuple[bool, str]:
    """Validate password strength."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    return True, ""


async def create_superuser(
    email: str,
    password: str,
    first_name: str = "Super",
    last_name: str = "Admin",
) -> bool:
    """Create a superuser in the public schema."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Check if user already exists
        result = await session.execute(
            select(UserModel).where(UserModel.email == email.lower())
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"\n❌ User with email '{email}' already exists!")
            print("   Use --promote flag to promote an existing user to superadmin.")
            return False

        # Hash password and create user
        hashed_password = password_service.hash_password(password)

        user = UserModel(
            id=uuid4(),
            email=email.lower(),
            hashed_password=hashed_password,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )

        session.add(user)
        await session.commit()

        print(f"\n✅ Superuser created successfully!")
        print(f"   Email: {email}")
        print(f"   Name: {first_name} {last_name}")
        print(f"   Role: SUPER_ADMIN")
        print(f"\n   You can now login at: /admin")
        return True


async def promote_to_superuser(email: str) -> bool:
    """Promote an existing user to superadmin."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        result = await session.execute(
            select(UserModel).where(UserModel.email == email.lower())
        )
        user = result.scalar_one_or_none()

        if not user:
            print(f"\n❌ User with email '{email}' not found!")
            return False

        if user.role == UserRole.SUPER_ADMIN:
            print(f"\n⚠️  User '{email}' is already a superadmin!")
            return True

        old_role = user.role
        user.role = UserRole.SUPER_ADMIN
        user.status = UserStatus.ACTIVE
        await session.commit()

        print(f"\n✅ User promoted to superadmin!")
        print(f"   Email: {email}")
        print(f"   Previous Role: {old_role}")
        print(f"   New Role: SUPER_ADMIN")
        return True


async def reset_password(email: str, new_password: str) -> bool:
    """Reset a user's password."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        result = await session.execute(
            select(UserModel).where(UserModel.email == email.lower())
        )
        user = result.scalar_one_or_none()

        if not user:
            print(f"\n❌ User with email '{email}' not found!")
            return False

        user.hashed_password = password_service.hash_password(new_password)
        await session.commit()

        print(f"\n✅ Password reset successfully for '{email}'!")
        return True


async def list_superusers() -> None:
    """List all superusers."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        result = await session.execute(
            select(UserModel).where(UserModel.role == UserRole.SUPER_ADMIN)
        )
        users = result.scalars().all()

        if not users:
            print("\n⚠️  No superusers found!")
            print("   Run this script without arguments to create one.")
            return

        print(f"\n📋 Superusers ({len(users)}):")
        print("-" * 60)
        for user in users:
            status_icon = "🟢" if user.status == UserStatus.ACTIVE else "🔴"
            print(f"   {status_icon} {user.email}")
            print(f"      Name: {user.first_name} {user.last_name}")
            print(f"      Status: {user.status}")
            print(f"      Created: {user.created_at}")
            print()


def interactive_create() -> None:
    """Interactive mode for creating a superuser."""
    print("\n" + "=" * 50)
    print("  NUMU Admin - Create Superuser")
    print("=" * 50 + "\n")

    # Get email
    while True:
        email = input("Email: ").strip()
        if not email:
            print("❌ Email is required!\n")
            continue
        if not validate_email(email):
            print("❌ Invalid email format!\n")
            continue
        break

    # Get password
    while True:
        password = getpass("Password: ")
        if not password:
            print("❌ Password is required!\n")
            continue

        valid, msg = validate_password(password)
        if not valid:
            print(f"❌ {msg}\n")
            continue

        password_confirm = getpass("Confirm Password: ")
        if password != password_confirm:
            print("❌ Passwords do not match!\n")
            continue
        break

    # Get name (optional)
    first_name = input("First Name (default: Super): ").strip() or "Super"
    last_name = input("Last Name (default: Admin): ").strip() or "Admin"

    # Create the user
    asyncio.run(create_superuser(email, password, first_name, last_name))


def main():
    parser = argparse.ArgumentParser(
        description="Create or manage superuser accounts for NUMU Admin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Interactive mode:
    python -m scripts.create_superuser

  Create with arguments:
    python -m scripts.create_superuser --email admin@numu.io --password MyP@ss123

  Promote existing user:
    python -m scripts.create_superuser --promote user@example.com

  Reset password:
    python -m scripts.create_superuser --reset-password admin@numu.io

  List all superusers:
    python -m scripts.create_superuser --list
        """,
    )

    parser.add_argument(
        "--email", "-e",
        help="Email for the new superuser"
    )
    parser.add_argument(
        "--password", "-p",
        help="Password for the new superuser"
    )
    parser.add_argument(
        "--first-name", "-f",
        default="Super",
        help="First name (default: Super)"
    )
    parser.add_argument(
        "--last-name", "-l",
        default="Admin",
        help="Last name (default: Admin)"
    )
    parser.add_argument(
        "--promote",
        metavar="EMAIL",
        help="Promote an existing user to superadmin"
    )
    parser.add_argument(
        "--reset-password",
        metavar="EMAIL",
        help="Reset password for a user"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all superusers"
    )

    args = parser.parse_args()

    # List superusers
    if args.list:
        asyncio.run(list_superusers())
        return

    # Promote existing user
    if args.promote:
        asyncio.run(promote_to_superuser(args.promote))
        return

    # Reset password
    if args.reset_password:
        new_password = getpass("New Password: ")
        valid, msg = validate_password(new_password)
        if not valid:
            print(f"❌ {msg}")
            sys.exit(1)
        confirm = getpass("Confirm Password: ")
        if new_password != confirm:
            print("❌ Passwords do not match!")
            sys.exit(1)
        asyncio.run(reset_password(args.reset_password, new_password))
        return

    # Create with arguments
    if args.email and args.password:
        if not validate_email(args.email):
            print("❌ Invalid email format!")
            sys.exit(1)
        valid, msg = validate_password(args.password)
        if not valid:
            print(f"❌ {msg}")
            sys.exit(1)
        asyncio.run(create_superuser(
            args.email,
            args.password,
            args.first_name,
            args.last_name,
        ))
        return

    # Interactive mode
    if args.email or args.password:
        print("❌ Both --email and --password are required when using arguments!")
        sys.exit(1)

    interactive_create()


if __name__ == "__main__":
    main()
