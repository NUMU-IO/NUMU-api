"""Public projection of an app install — what a shopper's browser may see.

`/api/v1/storefront/**` sits in `PUBLIC_PATH_PREFIXES`, so tenancy middleware is
skipped and these routes carry no dependencies: `store_id` comes straight off the
URL and is never checked against the requesting host. Host-to-store binding
exists only in the Next.js proxy, which is not on the path when `api.numueg.app`
is called directly.

So everything these routes return is world-readable by anyone who can guess a
store UUID. `app_installations.settings` is the field the entity docstring
describes as holding "API tokens", and the raw `manifest` is returned verbatim.

That is latent rather than live only because no app row exists yet — and seeding
the first row is step one of shipping anything here. Hence this module: the
projection is **default-deny**. An app publishes what is safe by naming it in
`manifest.public_settings`; everything else stays server-side. A manifest that
declares nothing exposes nothing, which is the correct behaviour for a
storefront payload and costs the swatch app nothing, since every one of its
settings is presentation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.numu_apps import NUMU_APPS
from src.application.services.partner_program import partner_apps_enabled
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)

# Manifest keys a shopper's browser may see. Everything else — credentials,
# webhook URLs, scopes, internal routing — stays server-side. An allowlist
# rather than a denylist: a new manifest key is invisible until someone decides
# it is safe, instead of leaking until someone notices.
_PUBLIC_MANIFEST_KEYS = frozenset({
    "blocks",
    "slots",
    "settings_schema",
    "public_settings",
    "version",
    "locales",
})


def public_settings(manifest: dict | None, settings: dict | None) -> dict[str, Any]:
    """The subset of an install's settings the app declared publishable.

    `manifest.public_settings` is a list of setting ids. Missing, empty or
    malformed means **nothing** is public — never "everything".
    """
    allowed = (manifest or {}).get("public_settings")
    if not isinstance(allowed, list):
        return {}
    keys = {k for k in allowed if isinstance(k, str)}
    if not keys:
        return {}
    return {k: v for k, v in (settings or {}).items() if k in keys}


def public_manifest(manifest: dict | None) -> dict[str, Any]:
    """The manifest with only presentation keys retained."""
    if not isinstance(manifest, dict):
        return {}
    return {k: v for k, v in manifest.items() if k in _PUBLIC_MANIFEST_KEYS}


async def visible_installs(session: AsyncSession, store_id: UUID) -> Select:
    """The (app, install) rows a shopper may see for a store.

    The single definition shared by the store payload's `installed_apps` and the
    `/storefront/.../apps` routes, so the three can never disagree again: an
    install must be enabled and finished connecting (a Partner App mid-consent
    is `pending_auth`), the app not suspended, Partner Apps hidden while the
    kill switch is off, and hub-only NUMU Apps (WhatsApp, Inbox) never listed.
    """
    stmt = (
        select(AppModel, AppInstallationModel)
        .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
        .where(
            AppInstallationModel.store_id == store_id,
            AppInstallationModel.is_enabled.is_(True),
            AppInstallationModel.status == "active",
            AppModel.status != AppStatus.SUSPENDED,
            AppModel.slug.notin_(NUMU_APPS),
        )
    )
    if not await partner_apps_enabled(session):
        stmt = stmt.where(AppModel.developer_id.is_(None))
    return stmt
