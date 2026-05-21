"""EmailTemplate repository implementation.

Hot-path note
-------------
``get_for_send`` is called on every transactional email dispatch. Because
template rows change rarely but are read on the critical path, we keep a
small in-process TTL cache (``cachetools.TTLCache``) keyed by
``(store_id, event_type, language)``.

The cache is intentionally **process-local** — no Redis fan-out — for
two reasons:

* Worker fleets are small and short-lived; staleness windows are bounded
  by the 60-second TTL.
* On write (create / update / delete) the affected key is invalidated
  *in this process*. Other workers will see the updated row when their
  TTL expires (worst case ~60s after the merchant edits a template).
  This is an acceptable trade-off for a non-realtime configuration knob.

If multi-process consistency ever becomes a hard requirement we can swap
the cache for a Redis-backed equivalent without changing the public API.
"""

from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.email_template import EmailTemplate
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.email_template import (
    EmailTemplateModel,
)

# Process-local cache. Keys are stringified to avoid leaking UUID-instance
# identity across requests. Values include negative lookups (None) so a
# missing template doesn't trigger a DB hit on every send.
_CacheKey = tuple[str, str, str]
_cache: TTLCache[_CacheKey, EmailTemplate | None] = TTLCache(maxsize=1024, ttl=60)


def _cache_key(store_id: UUID, event_type: str, language: str) -> _CacheKey:
    return (str(store_id), event_type, language)


class EmailTemplateRepository(IEmailTemplateRepository):
    """SQLAlchemy implementation of :class:`IEmailTemplateRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(EmailTemplateModel.tenant_id == tid)
        return query

    def _to_entity(self, model: EmailTemplateModel) -> EmailTemplate:
        return EmailTemplate(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            event_type=model.event_type,
            language=model.language,
            name=model.name,
            subject=model.subject,
            html_body=model.html_body,
            is_enabled=model.is_enabled,
            from_name=model.from_name,
            reply_to=model.reply_to,
            extra_data=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: EmailTemplate) -> EmailTemplateModel:
        return EmailTemplateModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            event_type=entity.event_type,
            language=entity.language,
            name=entity.name,
            subject=entity.subject,
            html_body=entity.html_body,
            is_enabled=entity.is_enabled,
            from_name=entity.from_name,
            reply_to=entity.reply_to,
            extra_data=entity.extra_data or None,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    def _invalidate(self, store_id: UUID, event_type: str, language: str) -> None:
        """Drop the cache entry for a given triple, if present."""
        _cache.pop(_cache_key(store_id, event_type, language), None)

    # ------------------------------------------------------------------
    # BaseRepository methods
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: UUID) -> EmailTemplate | None:
        query = select(EmailTemplateModel).where(EmailTemplateModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[EmailTemplate]:
        query = (
            select(EmailTemplateModel)
            .order_by(EmailTemplateModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: EmailTemplate) -> EmailTemplate:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        self._invalidate(entity.store_id, entity.event_type, entity.language)
        return self._to_entity(model)

    async def update(self, entity: EmailTemplate) -> EmailTemplate:
        query = select(EmailTemplateModel).where(EmailTemplateModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"EmailTemplate with id {entity.id} not found")

        # Capture old triple BEFORE mutation so we can invalidate it as
        # well — merchants can rename / re-key a template.
        old_triple = (model.store_id, model.event_type, model.language)

        model.event_type = entity.event_type
        model.language = entity.language
        model.name = entity.name
        model.subject = entity.subject
        model.html_body = entity.html_body
        model.is_enabled = entity.is_enabled
        model.from_name = entity.from_name
        model.reply_to = entity.reply_to
        model.extra_data = entity.extra_data or None

        await self.session.flush()
        await self.session.refresh(model)

        self._invalidate(*old_triple)
        self._invalidate(entity.store_id, entity.event_type, entity.language)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(EmailTemplateModel).where(EmailTemplateModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model is None:
            return False
        triple = (model.store_id, model.event_type, model.language)
        await self.session.delete(model)
        await self.session.flush()
        self._invalidate(*triple)
        return True

    async def count(self) -> int:
        query = select(func.count(EmailTemplateModel.id))
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Custom methods
    # ------------------------------------------------------------------

    async def get_by_store_event_language(
        self,
        store_id: UUID,
        event_type: str,
        language: str,
    ) -> EmailTemplate | None:
        query = select(EmailTemplateModel).where(
            EmailTemplateModel.store_id == store_id,
            EmailTemplateModel.event_type == event_type,
            EmailTemplateModel.language == language,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        language: str | None = None,
        is_enabled: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[EmailTemplate]:
        query = select(EmailTemplateModel).where(
            EmailTemplateModel.store_id == store_id
        )
        if event_type is not None:
            query = query.where(EmailTemplateModel.event_type == event_type)
        if language is not None:
            query = query.where(EmailTemplateModel.language == language)
        if is_enabled is not None:
            query = query.where(EmailTemplateModel.is_enabled.is_(is_enabled))
        query = (
            query.order_by(EmailTemplateModel.event_type, EmailTemplateModel.language)
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_for_send(
        self,
        store_id: UUID,
        event_type: str,
        language: str,
    ) -> EmailTemplate | None:
        """Hot-path lookup. Cached for 60s, including negative results.

        Returns ``None`` when no row exists OR when the matching row is
        disabled — callers fall back to default templates in either case.
        """
        key = _cache_key(store_id, event_type, language)
        if key in _cache:
            return _cache[key]

        query = select(EmailTemplateModel).where(
            EmailTemplateModel.store_id == store_id,
            EmailTemplateModel.event_type == event_type,
            EmailTemplateModel.language == language,
            EmailTemplateModel.is_enabled.is_(True),
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        entity = self._to_entity(model) if model else None
        _cache[key] = entity
        return entity

    async def count_by_store(
        self,
        store_id: UUID,
        event_type: str | None = None,
        language: str | None = None,
        is_enabled: bool | None = None,
    ) -> int:
        query = select(func.count(EmailTemplateModel.id)).where(
            EmailTemplateModel.store_id == store_id
        )
        if event_type is not None:
            query = query.where(EmailTemplateModel.event_type == event_type)
        if language is not None:
            query = query.where(EmailTemplateModel.language == language)
        if is_enabled is not None:
            query = query.where(EmailTemplateModel.is_enabled.is_(is_enabled))
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar() or 0


# Public alias matching the naming convention used elsewhere in the
# repositories package.
EmailTemplateRepositoryImpl = EmailTemplateRepository
