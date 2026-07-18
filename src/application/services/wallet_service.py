"""Merchant wallet service — the single write path for the wallet ledger.

Every balance change (top-up credit, commission debit, reversal, admin
adjustment) goes through :meth:`WalletService.apply_entry`, which:

1. locks the wallet row (``SELECT ... FOR UPDATE`` serializes concurrent
   writers per tenant),
2. appends a ``wallet_transactions`` row — duplicate application is caught
   by DB constraints (partial unique on (order_id, kind) for commissions /
   reversals, unique ``idempotency_key`` otherwise) and returned as ``None``,
3. maintains the denormalized ``merchant_wallets.balance_cents`` and the
   per-row ``balance_after_cents`` snapshot.

Cache invalidation is the caller's post-commit responsibility via
:meth:`invalidate_cache` (data must be durable before the cache refills).
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.core.entities.plan import get_plan_features
from src.core.entities.wallet import (
    WARNING_LEVEL_BLOCKED,
    WARNING_LEVEL_LOW,
    WARNING_LEVEL_NEGATIVE,
    WARNING_LEVEL_NONE,
    WalletStatus,
    WalletTransactionKind,
)
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    MerchantWalletModel,
    WalletTransactionModel,
)

logger = logging.getLogger(__name__)

_GATE_CACHE_TTL_SECONDS = 60
_BALANCE_CACHE_TTL_SECONDS = 60


class WalletSuspendedError(Exception):
    """Raised when writing to a suspended wallet."""


def gate_cache_key(tenant_id: UUID | str) -> str:
    return f"wallet:gate:{tenant_id}"


def balance_cache_key(tenant_id: UUID | str) -> str:
    return f"wallet:balance:{tenant_id}"


def warning_level_for(
    balance_cents: int,
    *,
    negative_allowance_cents: int,
    low_threshold_cents: int,
) -> int:
    """Pure ladder: 0 healthy, 1 low, 2 negative, 3 below allowance."""
    if balance_cents < -negative_allowance_cents:
        return WARNING_LEVEL_BLOCKED
    if balance_cents < 0:
        return WARNING_LEVEL_NEGATIVE
    if balance_cents < low_threshold_cents:
        return WARNING_LEVEL_LOW
    return WARNING_LEVEL_NONE


class WalletService:
    """Ledger writes, cached reads, and the checkout gate."""

    def __init__(
        self,
        db: AsyncSession,
        cache: RedisCacheService | None = None,
    ) -> None:
        self.db = db
        settings = get_settings()
        self._cache = cache or (RedisCacheService() if settings.redis_host else None)
        self._default_allowance = settings.wallet_negative_allowance_cents
        self._low_threshold = settings.wallet_low_balance_threshold_cents

    # ------------------------------------------------------------------
    # Wallet row
    # ------------------------------------------------------------------

    async def get_or_create_wallet(
        self, tenant_id: UUID, *, for_update: bool = False
    ) -> MerchantWalletModel:
        """Fetch the tenant's wallet, creating it lazily on first touch."""
        stmt = select(MerchantWalletModel).where(
            MerchantWalletModel.tenant_id == tenant_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        wallet = (await self.db.execute(stmt)).scalar_one_or_none()
        if wallet:
            return wallet

        wallet = MerchantWalletModel(tenant_id=tenant_id)
        try:
            # add() INSIDE the savepoint: on rollback the pending object is
            # expunged with it, so a lost race can't poison the session's
            # next flush with a doomed retry of the same INSERT.
            async with self.db.begin_nested():
                self.db.add(wallet)
                await self.db.flush()
        except IntegrityError:
            # Lost a create race — another writer inserted it; re-read.
            wallet = (await self.db.execute(stmt)).scalar_one()
        if for_update:
            wallet = (await self.db.execute(stmt.with_for_update())).scalar_one()
        return wallet

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def apply_entry(
        self,
        *,
        tenant_id: UUID,
        kind: WalletTransactionKind,
        amount_cents: int,
        currency: str = "EGP",
        order_id: UUID | None = None,
        topup_intent_id: UUID | None = None,
        idempotency_key: str | None = None,
        actor_user_id: UUID | None = None,
        note: str | None = None,
        meta: dict | None = None,
    ) -> WalletTransactionModel | None:
        """Append a ledger row and move the balance. ``None`` = already applied.

        Caller owns the transaction (flush here, commit outside) and MUST call
        :meth:`invalidate_cache` after commit.
        """
        wallet = await self.get_or_create_wallet(tenant_id, for_update=True)
        if wallet.status == WalletStatus.SUSPENDED.value:
            raise WalletSuspendedError(f"wallet suspended for tenant {tenant_id}")

        new_balance = wallet.balance_cents + amount_cents
        tx = WalletTransactionModel(
            wallet_id=wallet.id,
            tenant_id=tenant_id,
            kind=kind.value,
            amount_cents=amount_cents,
            balance_after_cents=new_balance,
            currency=currency,
            order_id=order_id,
            topup_intent_id=topup_intent_id,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            note=note,
            meta=meta,
        )
        try:
            # add() inside the savepoint — see get_or_create_wallet.
            async with self.db.begin_nested():
                self.db.add(tx)
                await self.db.flush()
        except IntegrityError:
            # Partial-unique (order_id, kind) or idempotency_key collision:
            # this exact entry was already applied. Idempotent no-op.
            logger.info(
                "wallet_entry_already_applied",
                extra={
                    "tenant_id": str(tenant_id),
                    "kind": kind.value,
                    "order_id": str(order_id) if order_id else None,
                    "idempotency_key": idempotency_key,
                },
            )
            return None

        wallet.balance_cents = new_balance
        await self.db.flush()
        logger.info(
            "wallet_entry_applied",
            extra={
                "tenant_id": str(tenant_id),
                "kind": kind.value,
                "amount_cents": amount_cents,
                "balance_after_cents": new_balance,
                "order_id": str(order_id) if order_id else None,
            },
        )
        return tx

    # ------------------------------------------------------------------
    # Commission
    # ------------------------------------------------------------------

    @staticmethod
    def effective_commission_bps(
        tenant: TenantModel, wallet: MerchantWalletModel | None
    ) -> int:
        """Per-tenant override wins; otherwise the plan's rate; exempt = 0."""
        if wallet is not None:
            if wallet.status == WalletStatus.EXEMPT.value:
                return 0
            if wallet.commission_bps_override is not None:
                return wallet.commission_bps_override
        return get_plan_features(tenant.plan).commission_bps

    # ------------------------------------------------------------------
    # Warning ladder
    # ------------------------------------------------------------------

    def allowance_for(self, wallet: MerchantWalletModel) -> int:
        return (
            wallet.negative_allowance_cents
            if wallet.negative_allowance_cents is not None
            else self._default_allowance
        )

    def current_warning_level(self, wallet: MerchantWalletModel) -> int:
        return warning_level_for(
            wallet.balance_cents,
            negative_allowance_cents=self.allowance_for(wallet),
            low_threshold_cents=self._low_threshold,
        )

    def bump_warning_level(self, wallet: MerchantWalletModel) -> int | None:
        """Persist the ladder position; return the new level when it RISES.

        Callers enqueue the notification only on a rise (dedup); the level
        resets automatically once the balance recovers to healthy.
        """
        level = self.current_warning_level(wallet)
        if level > wallet.last_warning_level:
            wallet.last_warning_level = level
            return level
        if level == WARNING_LEVEL_NONE and wallet.last_warning_level != 0:
            wallet.last_warning_level = WARNING_LEVEL_NONE
        return None

    # ------------------------------------------------------------------
    # Checkout gate + cached reads
    # ------------------------------------------------------------------

    async def checkout_gate_allows(self, tenant_id: UUID) -> bool:
        """Cheap gate for storefront checkout. FAILS OPEN on any error —
        never lose a merchant's sale to our infra; the commission handler
        and reconciliation sweep still charge, so the balance self-corrects.
        """
        try:
            key = gate_cache_key(tenant_id)
            if self._cache:
                cached = await self._cache.get(key)
                if cached in ("ok", "blocked"):
                    return cached == "ok"

            state = await self._compute_gate_state(tenant_id)
            if self._cache:
                await self._cache.set(key, state, expire=_GATE_CACHE_TTL_SECONDS)
            return state == "ok"
        except Exception:  # noqa: BLE001 — fail open by design
            logger.warning(
                "wallet_gate_check_failed_open",
                extra={"tenant_id": str(tenant_id)},
                exc_info=True,
            )
            return True

    async def _compute_gate_state(self, tenant_id: UUID) -> str:
        tenant = (
            await self.db.execute(
                select(TenantModel).where(TenantModel.id == tenant_id)
            )
        ).scalar_one_or_none()
        if tenant is None:
            return "ok"

        # Canary rollout: the gate must be armed globally
        # (ff_wallet_checkout_gate) or per-tenant via feature_flags.
        settings = get_settings()
        tenant_flag = bool((tenant.feature_flags or {}).get("wallet_checkout_gate"))
        if not settings.ff_wallet_checkout_gate and not tenant_flag:
            return "ok"

        wallet = (
            await self.db.execute(
                select(MerchantWalletModel).where(
                    MerchantWalletModel.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()

        if self.effective_commission_bps(tenant, wallet) == 0:
            return "ok"  # not a commission-bearing tenant
        if wallet is None or wallet.status != WalletStatus.ACTIVE.value:
            # exempt/suspended wallets never block shoppers; suspension is
            # enforced on the merchant's own writes, not their customers'.
            return "ok"
        if wallet.balance_cents < -self.allowance_for(wallet):
            return "blocked"
        return "ok"

    async def get_balance_cached(self, tenant_id: UUID) -> int | None:
        """Balance in cents via cache (60s TTL); ``None`` when unavailable."""
        key = balance_cache_key(tenant_id)
        if self._cache:
            cached = await self._cache.get(key)
            if cached is not None:
                try:
                    return int(cached)
                except (TypeError, ValueError):
                    pass
        wallet = (
            await self.db.execute(
                select(MerchantWalletModel).where(
                    MerchantWalletModel.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if wallet is None:
            return None
        if self._cache:
            await self._cache.set(
                key, wallet.balance_cents, expire=_BALANCE_CACHE_TTL_SECONDS
            )
        return wallet.balance_cents

    async def invalidate_cache(self, tenant_id: UUID) -> None:
        """Drop gate + balance cache entries. Call AFTER commit."""
        if not self._cache:
            return
        try:
            await self._cache.delete(gate_cache_key(tenant_id))
            await self._cache.delete(balance_cache_key(tenant_id))
        except Exception:  # noqa: BLE001 — TTL bounds staleness anyway
            logger.warning(
                "wallet_cache_invalidation_failed",
                extra={"tenant_id": str(tenant_id)},
            )
