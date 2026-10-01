"""Strip the plaintext storefront password the old Preferences page stored.

It lived at stores.settings.storefront_password, readable by anyone with the
store's settings, and gated nothing (the real gate is the hashed
settings.password_protected). The API no longer accepts the key.

Revision ID: strip_plain_store_pw_20261001
Revises: users_language_20261001
"""

from alembic import op

revision = "strip_plain_store_pw_20261001"
down_revision = "users_language_20261001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE public.stores SET settings = settings - 'storefront_password' "
        "WHERE settings ? 'storefront_password'"
    )


def downgrade() -> None:
    # A plaintext password is not restored.
    pass
