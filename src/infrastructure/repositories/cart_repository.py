"""Redis-based cart repository implementation."""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import redis.asyncio as redis

from src.config import settings
from src.core.entities.cart import Cart
from src.core.interfaces.repositories.cart_repository import ICartRepository


class RedisCartRepository(ICartRepository):
    """Cart repository implementation using Redis.

    Stores carts with session-based keys and automatic TTL expiration.
    Default TTL is 7 days for cart persistence.
    """

    # Default TTL: 7 days in seconds
    DEFAULT_TTL_SECONDS: int = 7 * 24 * 60 * 60  

    
    SESSION_KEY_PREFIX: str = "cart:session"
    CUSTOMER_KEY_PREFIX: str = "cart:customer"

    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Initialize Redis cart repository.

        Args:
            redis_url: Redis connection URL. Defaults to settings.
            ttl_seconds: TTL for cart in seconds. Defaults to 7 days.
        """
        self.redis_url = redis_url or settings.redis_url
        self.ttl_seconds = ttl_seconds or self.DEFAULT_TTL_SECONDS
        self._client: redis.Redis | None = None

    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None

    def _session_key(self, session_id: str, store_id: UUID) -> str:
        """Generate session-based cart key.

        Args:
            session_id: The session identifier.
            store_id: The store UUID.

        Returns:
            Redis key string.
        """
        return f"{self.SESSION_KEY_PREFIX}:{store_id}:{session_id}"

    def _customer_key(self, customer_id: UUID, store_id: UUID) -> str:
        """Generate customer-based cart key.

        Args:
            customer_id: The customer UUID.
            store_id: The store UUID.

        Returns:
            Redis key string.
        """
        return f"{self.CUSTOMER_KEY_PREFIX}:{store_id}:{customer_id}"

    def _calculate_expires_at(self) -> datetime:
        """Calculate expiration datetime."""
        return datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)

    async def get_by_session_id(
        self,
        session_id: str,
        store_id: UUID,
    ) -> Cart | None:
        """Get cart by session ID and store ID."""
        client = await self._get_client()
        key = self._session_key(session_id, store_id)

        data = await client.get(key)
        if not data:
            return None

        cart_dict = json.loads(data)
        return Cart.from_dict(cart_dict)

    async def get_by_customer_id(
        self,
        customer_id: UUID,
        store_id: UUID,
    ) -> Cart | None:
        """Get cart by customer ID and store ID."""
        client = await self._get_client()
        key = self._customer_key(customer_id, store_id)

        data = await client.get(key)
        if not data:
            return None

        cart_dict = json.loads(data)
        return Cart.from_dict(cart_dict)

    async def save(self, cart: Cart) -> Cart:
        """Save cart to Redis with TTL."""
        client = await self._get_client()

        
        cart.expires_at = self._calculate_expires_at()

        
        cart_data = json.dumps(cart.to_dict())

        
        session_key = self._session_key(cart.session_id, cart.store_id)
        await client.setex(session_key, self.ttl_seconds, cart_data)

        
        if cart.customer_id:
            customer_key = self._customer_key(cart.customer_id, cart.store_id)
            await client.setex(customer_key, self.ttl_seconds, cart_data)

        return cart

    async def delete(self, session_id: str, store_id: UUID) -> bool:
        """Delete cart by session ID and store ID."""
        client = await self._get_client()

        
        cart = await self.get_by_session_id(session_id, store_id)
        if not cart:
            return False

        
        session_key = self._session_key(session_id, store_id)
        await client.delete(session_key)

        
        if cart.customer_id:
            customer_key = self._customer_key(cart.customer_id, cart.store_id)
            await client.delete(customer_key)

        return True

    async def delete_by_customer_id(
        self,
        customer_id: UUID,
        store_id: UUID,
    ) -> bool:
        """Delete cart by customer ID and store ID."""
        client = await self._get_client()

        
        cart = await self.get_by_customer_id(customer_id, store_id)
        if not cart:
            return False

        
        customer_key = self._customer_key(customer_id, store_id)
        await client.delete(customer_key)

        
        session_key = self._session_key(cart.session_id, cart.store_id)
        await client.delete(session_key)

        return True

    async def transfer_to_customer(
        self,
        session_id: str,
        customer_id: UUID,
        store_id: UUID,
    ) -> Cart | None:
        """Transfer a guest cart to a customer."""
        
        guest_cart = await self.get_by_session_id(session_id, store_id)
        if not guest_cart:
            return None

        
        existing_cart = await self.get_by_customer_id(customer_id, store_id)

        if existing_cart:
            
            existing_cart.merge_cart(guest_cart)
            existing_cart.customer_id = customer_id

            
            old_session_key = self._session_key(session_id, store_id)
            client = await self._get_client()
            await client.delete(old_session_key)

            
            return await self.save(existing_cart)
        else:
            
            guest_cart.customer_id = customer_id

            
            return await self.save(guest_cart)

    async def extend_ttl(self, session_id: str, store_id: UUID) -> bool:
        """Extend the TTL of a cart."""
        client = await self._get_client()
        key = self._session_key(session_id, store_id)

        
        if not await client.exists(key):
            return False

        
        await client.expire(key, self.ttl_seconds)

        
        cart = await self.get_by_session_id(session_id, store_id)
        if cart and cart.customer_id:
            customer_key = self._customer_key(cart.customer_id, cart.store_id)
            if await client.exists(customer_key):
                await client.expire(customer_key, self.ttl_seconds)

        return True
