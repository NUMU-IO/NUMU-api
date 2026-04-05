"""User session tracking service.

Records logins, lists active sessions, and revokes sessions.
Parses user-agent to extract device/browser/OS info.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.user_session import UserSessionModel


def parse_user_agent(ua: str | None) -> dict:
    """Parse user-agent string into device_name, device_type, browser, os."""
    if not ua:
        return {
            "device_name": "Unknown",
            "device_type": "desktop",
            "browser": None,
            "os": None,
        }

    ua_lower = ua.lower()

    # Detect OS
    os_name = "Unknown"
    if "windows" in ua_lower:
        os_name = "Windows"
    elif "mac os" in ua_lower or "macintosh" in ua_lower:
        os_name = "macOS"
    elif "iphone" in ua_lower:
        os_name = "iOS"
    elif "ipad" in ua_lower:
        os_name = "iPadOS"
    elif "android" in ua_lower:
        os_name = "Android"
    elif "linux" in ua_lower:
        os_name = "Linux"
    elif "cros" in ua_lower:
        os_name = "ChromeOS"

    # Detect browser
    browser = "Unknown"
    if "edg/" in ua_lower:
        browser = "Edge"
    elif "chrome" in ua_lower and "safari" in ua_lower:
        browser = "Chrome"
    elif "firefox" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower:
        browser = "Safari"
    elif "opera" in ua_lower or "opr/" in ua_lower:
        browser = "Opera"

    # Detect device type
    if any(k in ua_lower for k in ("iphone", "android", "mobile")):
        if "ipad" in ua_lower or "tablet" in ua_lower:
            device_type = "tablet"
        else:
            device_type = "mobile"
    elif "ipad" in ua_lower:
        device_type = "tablet"
    else:
        device_type = "desktop"

    device_name = f"{browser} — {os_name}"

    return {
        "device_name": device_name,
        "device_type": device_type,
        "browser": browser,
        "os": os_name,
    }


async def record_session(
    session: AsyncSession,
    user_id: UUID,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> UserSessionModel:
    """Record a new login session."""
    parsed = parse_user_agent(user_agent)

    model = UserSessionModel(
        id=uuid4(),
        user_id=user_id,
        device_name=parsed["device_name"],
        device_type=parsed["device_type"],
        browser=parsed["browser"],
        os=parsed["os"],
        ip_address=ip_address,
    )
    session.add(model)
    await session.flush()
    return model


async def list_active_sessions(
    session: AsyncSession,
    user_id: UUID,
) -> list[UserSessionModel]:
    """List all active sessions for a user, most recent first."""
    result = await session.execute(
        select(UserSessionModel)
        .where(UserSessionModel.user_id == user_id)
        .where(UserSessionModel.is_active == True)  # noqa: E712
        .order_by(UserSessionModel.last_active_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())


async def revoke_session(
    session: AsyncSession,
    session_id: UUID,
    user_id: UUID,
) -> bool:
    """Revoke a specific session."""
    result = await session.execute(
        update(UserSessionModel)
        .where(UserSessionModel.id == session_id)
        .where(UserSessionModel.user_id == user_id)
        .where(UserSessionModel.is_active == True)  # noqa: E712
        .values(is_active=False, revoked_at=datetime.now(UTC))
    )
    return result.rowcount > 0


async def revoke_all_other_sessions(
    session: AsyncSession,
    user_id: UUID,
    current_session_id: UUID | None = None,
) -> int:
    """Revoke all sessions except the current one."""
    stmt = (
        update(UserSessionModel)
        .where(UserSessionModel.user_id == user_id)
        .where(UserSessionModel.is_active == True)  # noqa: E712
        .values(is_active=False, revoked_at=datetime.now(UTC))
    )
    if current_session_id:
        stmt = stmt.where(UserSessionModel.id != current_session_id)
    result = await session.execute(stmt)
    return result.rowcount
