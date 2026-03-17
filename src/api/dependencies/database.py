"""Database dependency."""

from src.infrastructure.database.connection import (
    get_admin_db_session as get_admin_db_session,
)
from src.infrastructure.database.connection import (
    get_db_session as get_db,
)

__all__ = ["get_admin_db_session", "get_db"]
