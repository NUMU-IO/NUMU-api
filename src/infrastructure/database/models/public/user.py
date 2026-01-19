"""User database model (public schema).

Users are stored in the public schema for global authentication/SSO.
This allows users to belong to multiple tenants.
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class UserModel(Base, UUIDMixin, TimestampMixin):
    """User database model (public schema).
    
    Users are stored globally in the public schema to enable:
    - Single Sign-On across tenants
    - Users owning multiple stores
    - Cross-tenant user management
    """

    __tablename__ = "users"
    __table_args__ = {"schema": "public"}

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole", schema="public"),
        default=UserRole.CUSTOMER,
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="userstatus", schema="public"),
        default=UserStatus.PENDING_VERIFICATION,
        nullable=False,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    owned_tenants = relationship("TenantModel", back_populates="owner", lazy="selectin")

    def __repr__(self) -> str:
        return f"<UserModel(id={self.id}, email={self.email})>"
