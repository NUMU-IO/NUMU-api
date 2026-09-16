"""New stores get the WhatsApp system template rows every send is guarded on.

Those rows were only ever created by one-shot backfills inside migrations, so
a store created after the last one had none, the send guard read no row, and
every automation — order confirmation, shipped, delivered, all of COD
Autopilot — skipped in silence. Measured on prod before the fix: a May store
had 32 rows with the Autopilot templates APPROVED, a September store had zero.

Uses a fake session, so no Postgres is required.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.whatsapp_template_seed import seed_system_templates
from src.core.whatsapp_rich_templates import RICH_TEMPLATES


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Answers the "what does this store already have?" query, records adds."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.added = []
        self.flushes = 0

    async def execute(self, _query):
        return _Result(self.existing)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_a_new_store_gets_every_system_template():
    session = _FakeSession()
    added = await seed_system_templates(session, store_id=uuid4(), tenant_id=uuid4())

    assert added == len(RICH_TEMPLATES)
    assert {(r.name, r.language) for r in session.added} == {
        (t["name"], t["language"]) for t in RICH_TEMPLATES
    }
    # The two COD Autopilot templates are the point of the exercise.
    names = {r.name for r in session.added}
    assert "cod_ship_digest_v1" in names
    assert "order_delivery_check_v1" in names


@pytest.mark.asyncio
async def test_rows_are_seeded_pending_and_marked_system():
    """The 15-minute poll flips them to APPROVED off the platform WABA."""
    session = _FakeSession()
    await seed_system_templates(session, store_id=uuid4(), tenant_id=uuid4())

    assert all(r.status == "PENDING" for r in session.added)
    assert all(r.is_system is True for r in session.added)
    assert all(r.body_text for r in session.added)


@pytest.mark.asyncio
async def test_seeding_twice_adds_nothing_the_second_time():
    """Idempotent: an existing row's status is owned by Meta, not by us."""
    existing = [(t["name"], t["language"]) for t in RICH_TEMPLATES]
    session = _FakeSession(existing=existing)

    added = await seed_system_templates(session, store_id=uuid4(), tenant_id=uuid4())

    assert added == 0
    assert session.added == []
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_a_partly_seeded_store_gets_only_the_missing_rows():
    first = RICH_TEMPLATES[0]
    session = _FakeSession(existing=[(first["name"], first["language"])])

    added = await seed_system_templates(session, store_id=uuid4(), tenant_id=uuid4())

    assert added == len(RICH_TEMPLATES) - 1
    assert (first["name"], first["language"]) not in {
        (r.name, r.language) for r in session.added
    }


def test_the_templates_autopilot_sends_are_in_the_seed():
    """The guard looks these two up by name; the seed must carry them."""
    from src.api.v1.routes.stores.settings import AUTOPILOT_TEMPLATES

    seeded = {t["name"] for t in RICH_TEMPLATES}
    for name in AUTOPILOT_TEMPLATES:
        assert name in seeded


def _store(settings):
    return SimpleNamespace(id=uuid4(), contact_phone="+201000000000", settings=settings)


def test_readiness_reports_cod_off():
    """Autopilot only touches COD orders — with COD off it has no work."""
    from src.api.v1.routes.stores.settings import _build_cod_autopilot_response

    resp = _build_cod_autopilot_response(
        _store({"payment": {"cod": {"enabled": False}}})
    )
    assert resp.cod_enabled is False
    assert resp.templates_ready is False

    resp = _build_cod_autopilot_response(
        _store({"payment": {"cod": {"enabled": True}}}),
        templates_ready=True,
        templates_pending=[],
    )
    assert resp.cod_enabled is True
    assert resp.templates_ready is True
