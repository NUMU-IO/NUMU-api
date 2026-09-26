"""get_db_session's one-round-trip setup leaves the same session state as before."""

import uuid

import pytest
from sqlalchemy import text

from src.infrastructure.database import connection

pytestmark = pytest.mark.asyncio


async def _settings(session):
    row = (
        await session.execute(
            text(
                "SELECT current_setting('search_path'),"
                " current_setting('app.current_tenant', true),"
                " current_setting('app.current_user', true)"
            )
        )
    ).one()
    return tuple(row)


async def test_setup_sets_schema_tenant_and_user_in_one_statement():
    tenant, user = str(uuid.uuid4()), str(uuid.uuid4())
    connection._tenant_id.set(tenant)
    connection._user_id.set(user)
    try:
        gen = connection.get_db_session()
        session = await gen.__anext__()
        assert await _settings(session) == ("public, public", tenant, user)
        # search_path is session-level like SET; the RLS GUCs are transaction-local.
        await session.commit()
        path, t, u = await _settings(session)
        assert path == "public, public" and not t and not u
        await gen.aclose()

        # Invalid ids are dropped, never bound as-is.
        connection._tenant_id.set("not-a-uuid")
        connection._user_id.set("nope")
        gen = connection.get_db_session()
        session = await gen.__anext__()
        _, t, u = await _settings(session)
        assert t == "" and u == ""
        await gen.aclose()
    finally:
        connection.reset_tenant_context()
        connection._user_id.set(None)
