"""Unit tests for Customer entity."""

from uuid import uuid4

import pytest

from src.core.entities.customer import Customer
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class TestCustomerEntity:
    """Tests for the Customer entity."""

    def test_create_customer_with_valid_data(self):
        """Test creating a customer with valid data."""
        store_id = uuid4()
        customer = Customer(
            id=uuid4(),
            store_id=store_id,
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        assert customer.store_id == store_id
        assert customer.email.value == "customer@example.com"
        assert customer.first_name == "Alice"
        assert customer.last_name == "Shopper"
        assert customer.full_name == "Alice Shopper"

    def test_customer_has_account(self):
        """Test has_account property."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            password_hash=None,
        )

        assert customer.has_account is False

        customer_with_password = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            password_hash="$2b$12$hashedpassword",
        )

        assert customer_with_password.has_account is True

    def test_customer_is_linked_to_user(self):
        """Test is_linked_to_user property."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            user_id=None,
        )

        assert customer.is_linked_to_user is False

        customer_linked = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            user_id=uuid4(),
        )

        assert customer_linked.is_linked_to_user is True

    def test_customer_average_order_value(self):
        """Test average_order_value property."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            total_orders=0,
            total_spent=0,
        )

        # No orders
        assert customer.average_order_value == 0.0

        customer_with_orders = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            total_orders=5,
            total_spent=50000,  # $500.00 total in cents
        )

        # $500 / 5 orders = $100 average
        assert customer_with_orders.average_order_value == 100.0

    def test_customer_record_order(self):
        """Test record_order method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            total_orders=2,
            total_spent=10000,
        )

        customer.record_order(order_total=5000)

        assert customer.total_orders == 3
        assert customer.total_spent == 15000

    def test_customer_update_password(self):
        """Test update_password method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            password_hash=None,
        )

        assert customer.has_account is False

        customer.update_password("$2b$12$newhashedpassword")

        assert customer.has_account is True
        assert customer.password_hash == "$2b$12$newhashedpassword"

    def test_customer_verify(self):
        """Test verify method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            is_verified=False,
        )

        customer.verify()

        assert customer.is_verified is True

    def test_customer_set_default_address(self):
        """Test set_default_address method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        address_id = uuid4()
        customer.set_default_address(address_id)

        assert customer.default_address_id == address_id

    def test_customer_clear_default_address(self):
        """Test clear_default_address method."""
        address_id = uuid4()
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            default_address_id=address_id,
        )

        customer.clear_default_address()

        assert customer.default_address_id is None

    def test_customer_add_tag(self):
        """Test add_tag method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        customer.add_tag("VIP")
        customer.add_tag("Wholesale")

        assert "vip" in customer.tags  # Normalized to lowercase
        assert "wholesale" in customer.tags

    def test_customer_add_tag_normalizes(self):
        """Test add_tag normalizes tags."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        customer.add_tag("  VIP  ")
        customer.add_tag("VIP")  # Duplicate

        assert customer.tags == ["vip"]  # Only one, normalized

    def test_customer_remove_tag(self):
        """Test remove_tag method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            tags=["vip", "wholesale"],
        )

        customer.remove_tag("VIP")  # Case insensitive

        assert "vip" not in customer.tags
        assert "wholesale" in customer.tags

    def test_customer_opt_in_marketing(self):
        """Test opt_in_marketing method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            accepts_marketing=False,
        )

        customer.opt_in_marketing()

        assert customer.accepts_marketing is True

    def test_customer_opt_out_marketing(self):
        """Test opt_out_marketing method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            accepts_marketing=True,
        )

        customer.opt_out_marketing()

        assert customer.accepts_marketing is False

    def test_customer_link_to_user(self):
        """Test link_to_user method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        user_id = uuid4()
        customer.link_to_user(user_id)

        assert customer.user_id == user_id
        assert customer.is_linked_to_user is True

    def test_customer_unlink_from_user(self):
        """Test unlink_from_user method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            user_id=uuid4(),
        )

        customer.unlink_from_user()

        assert customer.user_id is None
        assert customer.is_linked_to_user is False

    def test_customer_update_notes(self):
        """Test update_notes method."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
        )

        customer.update_notes("Prefers morning delivery")

        assert customer.notes == "Prefers morning delivery"

        customer.update_notes(None)
        assert customer.notes is None

    def test_customer_with_phone(self):
        """Test customer with phone number."""
        customer = Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            phone=PhoneNumber(value="+201234567890", country_code="EG"),
        )

        assert customer.phone is not None
        assert customer.phone.value == "+201234567890"

    def test_customer_serialization(self):
        """Test customer serialization to dict."""
        store_id = uuid4()
        customer = Customer(
            id=uuid4(),
            store_id=store_id,
            email=Email(value="customer@example.com"),
            first_name="Alice",
            last_name="Shopper",
            tags=["vip"],
            total_orders=5,
            total_spent=50000,
        )

        data = customer.model_dump()
        assert data["first_name"] == "Alice"
        assert data["last_name"] == "Shopper"
        assert data["store_id"] == store_id
        assert data["tags"] == ["vip"]
        assert data["total_orders"] == 5
        assert data["total_spent"] == 50000
