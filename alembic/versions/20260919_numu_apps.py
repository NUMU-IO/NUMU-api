"""WhatsApp and the Inbox become NUMU Apps: catalog rows + auto-install.

Revision ID: numu_apps_20260919
Revises: proof_txn_ref_partial_20260920
Create Date: 2026-09-19

Phase 1 of docs/Plans/apps-developer-work. Two things, both inserts only:

1. The catalog rows for ``whatsapp`` and ``inbox`` (first party, published),
   with their Arabic and English listing. They are here rather than in
   ``scripts/seed_apps.py`` because that script has never run in production,
   and a store cannot be installed onto an app row that does not exist.
2. An install for every store that already uses the feature, so no merchant
   loses anything when their tenant gets ``ff_numu_apps``:
   - WhatsApp: every store with a ``whatsapp_access_requests`` row, in any
     status. Every send path already needs that row, so it is the complete set.
   - Inbox: every store with a ``channel_connections`` row, in any status.

3. ``app_uninstalls``: when a NUMU App is uninstalled, the date its data gets
   deleted (30 days later). Reinstalling removes the row; a daily task purges
   the rest (``numu_apps.purge_due``).

Nothing reads these rows until a tenant has ``ff_numu_apps``: the catalog and
install list hide both apps, and ``app_enabled`` answers True, while the flag
is off. Idempotent: ON CONFLICT DO NOTHING throughout, so a re-run, or a
merchant who installed first, changes nothing.

Downgrade drops ``app_uninstalls`` and deletes the two app rows; their
installs go with them (FK CASCADE).
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "numu_apps_20260919"
down_revision: str | Sequence[str] | None = "proof_txn_ref_partial_20260920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEVELOPER = {
    "name": "NUMU",
    "url": "https://numueg.app",
    "support_email": "support@numueg.app",
    "is_first_party": True,
}

APPS = [
    {
        "slug": "whatsapp",
        "name": "WhatsApp",
        "description": (
            "Order updates, COD confirmation, campaigns and a shared inbox on WhatsApp."
        ),
        "manifest": {
            "version": "1.0.0",
            "tagline": "Confirm orders and talk to customers on WhatsApp",
            "locales": {
                "ar": {"tagline": "أكّد الطلبات واتكلم مع عملائك على واتساب"},
                "en": {"tagline": "Confirm orders and talk to customers on WhatsApp"},
            },
            "developer": _DEVELOPER,
            "highlights": [
                {
                    "locales": {
                        "ar": {
                            "text": "رسايل تأكيد الطلب والدفع عند الاستلام أوتوماتيك"
                        },
                        "en": {"text": "Automatic order and COD confirmation messages"},
                    }
                },
                {
                    "locales": {
                        "ar": {"text": "كود تأكيد رقم الموبايل في الـ Checkout"},
                        "en": {"text": "Phone verification code at checkout"},
                    }
                },
                {
                    "locales": {
                        "ar": {
                            "text": "حملات وقوالب رسايل ومحادثات العملاء في مكان واحد"
                        },
                        "en": {
                            "text": "Campaigns, templates and customer chats in one place"
                        },
                    }
                },
            ],
            "pricing": {
                "plan": "paid",
                "locales": {
                    "ar": {"label": "مدفوع، والسعر على حسب عدد الرسايل"},
                    "en": {"label": "Paid, priced by message volume"},
                },
            },
            "languages": ["ar", "en"],
            "app_locales": {
                "ar": {
                    "name": "واتساب",
                    "description": (
                        "تحديثات الطلبات وتأكيد الدفع عند الاستلام والحملات "
                        "ومحادثات العملاء، كلها على واتساب."
                    ),
                },
                "en": {
                    "name": "WhatsApp",
                    "description": (
                        "Order updates, COD confirmation, campaigns and customer "
                        "chats, all on WhatsApp."
                    ),
                },
            },
        },
    },
    {
        "slug": "inbox",
        "name": "Inbox",
        "description": "Messenger and Instagram messages from your customers in one inbox.",
        "manifest": {
            "version": "1.0.0",
            "tagline": "Every customer message in one place",
            "locales": {
                "ar": {"tagline": "كل رسايل عملائك في مكان واحد"},
                "en": {"tagline": "Every customer message in one place"},
            },
            "developer": _DEVELOPER,
            "highlights": [
                {
                    "locales": {
                        "ar": {"text": "رسايل ماسنجر وإنستجرام في صندوق واحد"},
                        "en": {"text": "Messenger and Instagram in one inbox"},
                    }
                },
                {
                    "locales": {
                        "ar": {"text": "اربط المحادثة بالعميل وشوف طلباته"},
                        "en": {
                            "text": "Link a chat to the customer and see their orders"
                        },
                    }
                },
            ],
            "pricing": {
                "plan": "free",
                "locales": {"ar": {"label": "مجاني"}, "en": {"label": "Free"}},
            },
            "languages": ["ar", "en"],
            "app_locales": {
                "ar": {
                    "name": "صندوق الوارد",
                    "description": "رسايل عملائك من ماسنجر وإنستجرام في صندوق واحد.",
                },
                "en": {
                    "name": "Inbox",
                    "description": (
                        "Messenger and Instagram messages from your customers "
                        "in one inbox."
                    ),
                },
            },
        },
    },
]

# Which stores already use each app. Any status: a store mid-request or with a
# lapsed connection is still a store that uses the feature.
_USERS = {
    "whatsapp": "SELECT store_id FROM public.whatsapp_access_requests",
    "inbox": "SELECT store_id FROM public.channel_connections",
}


def upgrade() -> None:
    op.create_table(
        "app_uninstalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("store_id", "app_id", name="uq_app_uninstall_store_app"),
        schema="public",
    )
    op.create_index(
        "ix_app_uninstalls_purge_after",
        "app_uninstalls",
        ["purge_after"],
        schema="public",
    )

    bind = op.get_bind()
    for app in APPS:
        bind.execute(
            sa.text(
                """
                INSERT INTO public.apps (
                    id, slug, name, description, version, icon_url,
                    manifest, status, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :slug, :name, :description, '1.0.0', NULL,
                    CAST(:manifest AS jsonb), CAST('published' AS appstatus),
                    now(), now()
                )
                ON CONFLICT (slug) DO NOTHING
                """
            ),
            {
                "slug": app["slug"],
                "name": app["name"],
                "description": app["description"],
                "manifest": json.dumps(app["manifest"], ensure_ascii=False),
            },
        )
        bind.execute(
            sa.text(
                f"""
                INSERT INTO public.app_installations (
                    id, tenant_id, store_id, app_id, is_enabled, settings,
                    created_at, updated_at
                )
                SELECT gen_random_uuid(), s.tenant_id, s.id, a.id, true,
                       '{{}}'::jsonb, now(), now()
                FROM public.stores s
                JOIN public.apps a ON a.slug = :slug
                WHERE s.id IN ({_USERS[app["slug"]]})
                  AND s.tenant_id IS NOT NULL
                ON CONFLICT ON CONSTRAINT uq_app_installation_store_app DO NOTHING
                """
            ),
            {"slug": app["slug"]},
        )


def downgrade() -> None:
    op.drop_table("app_uninstalls", schema="public")
    op.execute("DELETE FROM public.apps WHERE slug IN ('whatsapp', 'inbox')")
