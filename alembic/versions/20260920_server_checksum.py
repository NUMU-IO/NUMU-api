"""Pin the SSR bundle's digest out of band (apps plan, Phase 8 item 8.1).

The storefront's SSR worker downloads `theme.server.js` and `import()`s it
inside a Node child process. Until now the only thing vouching for those
bytes was the `ssr.server_bundle_checksum` field in the bundle's own sibling
`manifest.json` — the same CDN prefix as the bytes it describes. Anything
able to rewrite the bundle could rewrite that field in the same breath, or
simply delete it, because the worker skipped the check when it was absent.

Storing the digest on the version row puts it in a different system from the
artifact, so a CDN write alone no longer changes what the worker will run.
Nullable: rows seeded before this column keep working off the manifest
digest, and the worker refuses only when neither source exists.

Revision ID: server_checksum_20260920
Revises: app_oauth_20260920
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "server_checksum_20260920"
down_revision: str | None = "app_oauth_20260920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("marketplace_theme_versions", "theme_versions"):
        op.add_column(
            table,
            sa.Column("server_checksum", sa.String(length=64), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    for table in ("marketplace_theme_versions", "theme_versions"):
        op.drop_column(table, "server_checksum", schema="public")
