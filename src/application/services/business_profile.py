"""Read and write a tenant's commercial details.

Collect-and-surface only: nothing here blocks a store from selling. The
point is to know, per merchant, whether they could take non-COD money and
issue a compliant invoice if we asked them to — and to have somewhere to
put the answer when a gate eventually exists.

The payout account number is the only secret in the record. It is
encrypted with the same Fernet-backed SecretsManager used for channel
credentials, and everything the UI needs to render — bank, account name,
a masked tail — is stored in the clear beside it so displaying a profile
never decrypts anything. Decryption happens only when someone genuinely
needs the number, which today is nobody: there is no payout run yet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.public.merchant_business_profile import (
    MerchantBusinessProfileModel,
)
from src.infrastructure.external_services.secrets.secrets_manager import (
    SecretsManager,
)

logger = get_logger(__name__)


def _mask(account_number: str) -> str:
    """Render a display tail. Never returns enough to pay anyone."""
    tail = account_number.strip()[-4:]
    return f"•••• {tail}" if tail else ""


async def get_profile(
    db: AsyncSession, *, tenant_id: UUID
) -> MerchantBusinessProfileModel | None:
    return (
        (
            await db.execute(
                select(MerchantBusinessProfileModel).where(
                    MerchantBusinessProfileModel.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .first()
    )


async def upsert_profile(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    is_registered_business: bool | None = None,
    tax_id: str | None = None,
    payout_bank_name: str | None = None,
    payout_account_name: str | None = None,
    payout_account_number: str | None = None,
) -> MerchantBusinessProfileModel:
    """Create or update the profile, writing only what was supplied.

    Every field is optional and ``None`` means "not supplied", so a form
    that submits one section cannot blank another. Clearing a value is
    deliberately not expressible here — a merchant who wants to remove a
    tax id is a support conversation, not a PATCH with an empty string,
    because the same request shape would otherwise wipe a record on any
    front-end bug that sent "" for an untouched field.
    """
    profile = await get_profile(db, tenant_id=tenant_id)
    if profile is None:
        profile = MerchantBusinessProfileModel(tenant_id=tenant_id)
        db.add(profile)

    if is_registered_business is not None:
        profile.is_registered_business = is_registered_business
    if tax_id:
        profile.tax_id = tax_id.strip()[:50]
    if payout_bank_name:
        profile.payout_bank_name = payout_bank_name.strip()[:120]
    if payout_account_name:
        profile.payout_account_name = payout_account_name.strip()[:160]

    if payout_account_number:
        cleaned = payout_account_number.strip()
        secrets = SecretsManager()
        key_id = await secrets.get_current_key_id()
        profile.payout_encrypted = await secrets.encrypt(
            {"account_number": cleaned}, key_id
        )
        profile.payout_key_id = key_id
        profile.payout_masked = _mask(cleaned)

    # Stamped once, when the record first became complete. A merchant who
    # later edits a field has not "completed" it again.
    if profile.completed_at is None and profile.is_complete:
        profile.completed_at = datetime.now(UTC)

    await db.flush()
    return profile


async def read_payout_account(db: AsyncSession, *, tenant_id: UUID) -> str | None:
    """Decrypt the payout account number.

    Nothing calls this yet — there is no payout run. It exists so the
    encryption has a documented way out, rather than being discovered as
    a dead end by whoever builds payouts.
    """
    profile = await get_profile(db, tenant_id=tenant_id)
    if profile is None or not profile.payout_encrypted or not profile.payout_key_id:
        return None
    try:
        secrets = SecretsManager()
        data = await secrets.decrypt(profile.payout_encrypted, profile.payout_key_id)
        return data.get("account_number")
    except Exception:
        logger.exception("payout_account_decrypt_failed")
        return None
