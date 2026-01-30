"""Unit tests for Cart and CartItem entities."""

from uuid import uuid4

import pytest

from src.core.entities.cart import Cart, CartItem


class TestCartItem:
    """Tests for the CartItem entity."""

    def test_create_cart_item(self):
        """Test creating a cart item."""
        cart_id = uuid4()
        product_id = uuid4()
        item = CartItem(
            cart_id=cart_id,
            product_id=product_id,
            quantity=3,
        )

        assert item.cart_id == cart_id
        assert item.product_id == product_id
        assert item.quantity == 3
        assert item.variant_id is None

    def test_create_cart_item_with_variant(self):
        """Test creating a cart item with variant."""
        variant_id = uuid4()
        item = CartItem(
            cart_id=uuid4(),
            product_id=uuid4(),
            quantity=1,
            variant_id=variant_id,
        )

        assert item.variant_id == variant_id

    def test_update_quantity(self):
        """Test updating item quantity."""
        item = CartItem(cart_id=uuid4(), product_id=uuid4(), quantity=2)
        item.update_quantity(5)
        assert item.quantity == 5

    def test_update_quantity_invalid_raises(self):
        """Test updating with quantity < 1 raises ValueError."""
        item = CartItem(cart_id=uuid4(), product_id=uuid4(), quantity=2)

        with pytest.raises(ValueError, match="at least 1"):
            item.update_quantity(0)

    def test_increment(self):
        """Test incrementing quantity."""
        item = CartItem(cart_id=uuid4(), product_id=uuid4(), quantity=2)
        item.increment(3)
        assert item.quantity == 5

    def test_increment_default(self):
        """Test incrementing quantity by default amount (1)."""
        item = CartItem(cart_id=uuid4(), product_id=uuid4(), quantity=2)
        item.increment()
        assert item.quantity == 3


class TestCart:
    """Tests for the Cart entity."""

    def _create_cart(self, **kwargs):
        """Helper to create a test cart."""
        defaults = {
            "store_id": uuid4(),
            "customer_id": uuid4(),
        }
        defaults.update(kwargs)
        return Cart(**defaults)

    def test_create_empty_cart(self):
        """Test creating an empty cart."""
        cart = self._create_cart()

        assert cart.is_empty is True
        assert cart.item_count == 0
        assert len(cart.items) == 0

    def test_add_item(self):
        """Test adding an item to the cart."""
        cart = self._create_cart()
        product_id = uuid4()

        item = cart.add_item(product_id=product_id, quantity=2)

        assert cart.is_empty is False
        assert cart.item_count == 2
        assert len(cart.items) == 1
        assert item.product_id == product_id
        assert item.quantity == 2

    def test_add_item_merge_duplicate(self):
        """Test adding same product merges quantities."""
        cart = self._create_cart()
        product_id = uuid4()

        cart.add_item(product_id=product_id, quantity=2)
        cart.add_item(product_id=product_id, quantity=3)

        assert len(cart.items) == 1
        assert cart.items[0].quantity == 5
        assert cart.item_count == 5

    def test_add_item_different_variants_not_merged(self):
        """Test that different variants are separate items."""
        cart = self._create_cart()
        product_id = uuid4()
        variant_a = uuid4()
        variant_b = uuid4()

        cart.add_item(product_id=product_id, quantity=1, variant_id=variant_a)
        cart.add_item(product_id=product_id, quantity=2, variant_id=variant_b)

        assert len(cart.items) == 2
        assert cart.item_count == 3

    def test_find_item(self):
        """Test finding an item by product_id."""
        cart = self._create_cart()
        product_id = uuid4()
        cart.add_item(product_id=product_id, quantity=1)

        found = cart.find_item(product_id)
        assert found is not None
        assert found.product_id == product_id

    def test_find_item_not_found(self):
        """Test finding a non-existent item."""
        cart = self._create_cart()
        assert cart.find_item(uuid4()) is None

    def test_find_item_by_id(self):
        """Test finding an item by its ID."""
        cart = self._create_cart()
        item = cart.add_item(product_id=uuid4(), quantity=1)

        found = cart.find_item_by_id(item.id)
        assert found is not None
        assert found.id == item.id

    def test_update_item(self):
        """Test updating an item's quantity."""
        cart = self._create_cart()
        item = cart.add_item(product_id=uuid4(), quantity=2)

        updated = cart.update_item(item.id, 5)
        assert updated.quantity == 5

    def test_update_item_not_found_raises(self):
        """Test updating a non-existent item raises."""
        cart = self._create_cart()

        with pytest.raises(ValueError, match="not found"):
            cart.update_item(uuid4(), 3)

    def test_remove_item(self):
        """Test removing an item from the cart."""
        cart = self._create_cart()
        item = cart.add_item(product_id=uuid4(), quantity=2)
        cart.add_item(product_id=uuid4(), quantity=1)

        cart.remove_item(item.id)

        assert len(cart.items) == 1
        assert cart.item_count == 1

    def test_remove_item_not_found_raises(self):
        """Test removing a non-existent item raises."""
        cart = self._create_cart()

        with pytest.raises(ValueError, match="not found"):
            cart.remove_item(uuid4())

    def test_clear(self):
        """Test clearing the cart."""
        cart = self._create_cart()
        cart.add_item(product_id=uuid4(), quantity=2)
        cart.add_item(product_id=uuid4(), quantity=1)

        cart.clear()

        assert cart.is_empty is True
        assert cart.item_count == 0
        assert len(cart.items) == 0
