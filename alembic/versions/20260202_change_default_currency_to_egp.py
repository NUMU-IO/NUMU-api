"""change product price_currency default from USD to EGP

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-02-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Change the server default for price_currency from USD to EGP
    op.alter_column(
        'products',
        'price_currency',
        server_default='EGP',
        schema='public',
    )

    # Update existing rows that still have USD as a leftover default
    op.execute(
        "UPDATE public.products SET price_currency = 'EGP' WHERE price_currency = 'USD'"
    )

    # Also update the store default_currency server default to EGP
    op.execute(
        "ALTER TABLE public.stores "
        "ALTER COLUMN default_currency SET DEFAULT 'EGP'"
    )

    # Update existing stores that have USD default
    op.execute(
        "UPDATE public.stores SET default_currency = 'EGP' WHERE default_currency = 'USD'"
    )


def downgrade() -> None:
    # Revert store default_currency back to USD
    op.execute(
        "ALTER TABLE public.stores "
        "ALTER COLUMN default_currency SET DEFAULT 'USD'"
    )

    # Revert product price_currency default back to USD
    op.alter_column(
        'products',
        'price_currency',
        server_default='USD',
        schema='public',
    )
