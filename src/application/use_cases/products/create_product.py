"""Create product use case."""

import uuid
from decimal import Decimal
from uuid import UUID

from slugify import slugify

from src.application.dto.product import CreateProductDTO, ProductDTO
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency, Money

# Validation constants
MIN_PRODUCT_NAME_LENGTH = 1
MAX_PRODUCT_NAME_LENGTH = 255
MAX_SKU_LENGTH = 100
MAX_SHORT_DESCRIPTION_LENGTH = 500
VALID_PRODUCT_TYPES = {"physical", "digital", "service"}
VALID_CURRENCIES = {c.value for c in Currency}


class CreateProductUseCase:
    """Use case for creating a new product."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
        category_repository: ICategoryRepository | None = None,
        onboarding_repository: IOnboardingRepository | None = None,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository
        self.category_repository = category_repository
        self.onboarding_repository = onboarding_repository

    def _validate_product_data(
        self, dto: CreateProductDTO, store_id: UUID
    ) -> list[str]:
        """Validate product data and return list of errors.

        Args:
            dto: The create product DTO.
            store_id: The store UUID.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Required field: name
        if not dto.name:
            errors.append("Product name is required")
        elif len(dto.name.strip()) < MIN_PRODUCT_NAME_LENGTH:
            errors.append(
                f"Product name must be at least {MIN_PRODUCT_NAME_LENGTH} character(s)"
            )
        elif len(dto.name) > MAX_PRODUCT_NAME_LENGTH:
            errors.append(
                f"Product name must not exceed {MAX_PRODUCT_NAME_LENGTH} characters"
            )

        # Required field: price (must be > 0)
        if dto.price is None:
            errors.append("Product price is required")
        elif dto.price <= Decimal("0"):
            errors.append("Product price must be greater than 0")

        # Validate compare_at_price if provided
        if dto.compare_at_price is not None:
            if dto.compare_at_price < Decimal("0"):
                errors.append("Compare at price cannot be negative")
            elif dto.price is not None and dto.compare_at_price <= dto.price:
                errors.append("Compare at price must be greater than the regular price")

        # Validate cost_price if provided
        if dto.cost_price is not None and dto.cost_price < Decimal("0"):
            errors.append("Cost price cannot be negative")

        # Validate quantity
        if dto.quantity is not None and dto.quantity < 0:
            errors.append("Quantity cannot be negative")

        # Validate low_stock_threshold
        if dto.low_stock_threshold is not None and dto.low_stock_threshold < 0:
            errors.append("Low stock threshold cannot be negative")

        # Validate SKU length if provided
        if dto.sku and len(dto.sku) > MAX_SKU_LENGTH:
            errors.append(f"SKU must not exceed {MAX_SKU_LENGTH} characters")

        # Validate short_description length if provided
        if (
            dto.short_description
            and len(dto.short_description) > MAX_SHORT_DESCRIPTION_LENGTH
        ):
            errors.append(
                f"Short description must not exceed {MAX_SHORT_DESCRIPTION_LENGTH} characters"
            )

        # Validate product_type
        if dto.product_type and dto.product_type.lower() not in VALID_PRODUCT_TYPES:
            errors.append(
                f"Invalid product type. Valid types: {', '.join(VALID_PRODUCT_TYPES)}"
            )

        # Validate currency
        if dto.price_currency and dto.price_currency.upper() not in VALID_CURRENCIES:
            errors.append(
                f"Invalid currency. Valid currencies: {', '.join(VALID_CURRENCIES)}"
            )

        # Validate images are URLs (basic check)
        if dto.images:
            for i, image in enumerate(dto.images):
                if not isinstance(image, str):
                    errors.append(f"Image at index {i} must be a string URL")
                elif not image.startswith(("http://", "https://")):
                    errors.append(
                        f"Image at index {i} must be a valid URL starting with http:// or https://"
                    )

        return errors

    async def _validate_category_exists(
        self, category_id: UUID, store_id: UUID
    ) -> str | None:
        """Validate that category exists and belongs to the store.

        Args:
            category_id: The category UUID.
            store_id: The store UUID.

        Returns:
            Error message if validation fails, None if valid.
        """
        if not self.category_repository:
            # Skip validation if no category repository provided
            return None

        category = await self.category_repository.get_by_id(category_id)
        if not category:
            return f"Category with ID {category_id} not found"

        # Verify category belongs to the same store
        if hasattr(category, "store_id") and category.store_id != store_id:
            return f"Category with ID {category_id} does not belong to this store"

        return None

    async def execute(
        self,
        dto: CreateProductDTO,
        store_id: UUID,
        user_id: UUID,
    ) -> ProductDTO:
        """Create a new product.

        Args:
            dto: The create product DTO.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            ProductDTO with created product data.

        Raises:
            EntityNotFoundError: If store or category not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If product data is invalid.
        """
        # Verify store exists and user has permission
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to add products to this store"
            )

        # Validate product data
        validation_errors = self._validate_product_data(dto, store_id)

        # Validate category exists if provided
        if dto.category_id:
            category_error = await self._validate_category_exists(
                dto.category_id, store_id
            )
            if category_error:
                validation_errors.append(category_error)

        # Raise validation error if any errors found
        if validation_errors:
            raise ValidationError(
                "Product validation failed:\n• " + "\n• ".join(validation_errors)
            )

        # Generate slug if not provided
        slug = dto.slug or slugify(dto.name)

        # Check if slug already exists in store
        existing = await self.product_repository.get_by_slug(store_id, slug)
        if existing:
            slug = f"{slug}-{str(uuid.uuid4())[:8]}"

        # Parse currency and create Money
        try:
            currency = Currency(dto.price_currency.upper())
        except ValueError:
            currency = Currency.USD

        price = Money(amount=dto.price, currency=currency)
        compare_at_price = (
            Money(amount=dto.compare_at_price, currency=currency)
            if dto.compare_at_price
            else None
        )
        cost_price = (
            Money(amount=dto.cost_price, currency=currency) if dto.cost_price else None
        )

        # Parse product type
        try:
            product_type = ProductType(dto.product_type.lower())
        except ValueError:
            product_type = ProductType.PHYSICAL

        # Create product entity
        product = Product(
            store_id=store_id,
            tenant_id=store.tenant_id,
            name=dto.name.strip(),
            slug=slug,
            sku=dto.sku.strip() if dto.sku else None,
            description=dto.description,
            short_description=dto.short_description,
            product_type=product_type,
            status=ProductStatus.DRAFT,
            price=price,
            compare_at_price=compare_at_price,
            cost_price=cost_price,
            quantity=dto.quantity or 0,
            low_stock_threshold=dto.low_stock_threshold or 5,
            images=dto.images or [],
            category_id=dto.category_id,
            tags=dto.tags or [],
            attributes=dto.attributes or {},
        )

        # Save product
        created_product = await self.product_repository.create(product)

        # Check if this is the merchant's first product -> send onboarding email
        try:
            total_products = await self.product_repository.count_by_store(store_id)
            if total_products == 1:
                from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                    send_first_product_email_task,
                )

                merchant_email = store.contact_email or None
                if merchant_email:
                    send_first_product_email_task.delay(
                        email=merchant_email,
                        merchant_name=store.name,
                        product_name=dto.name,
                        language=store.default_language,
                    )
        except Exception:
            pass  # Never block product creation for onboarding emails

        # Auto-complete the add_product onboarding step
        if self.onboarding_repository:
            from src.application.use_cases.onboarding.auto_complete import (
                try_complete_onboarding_step,
            )
            from src.core.entities.onboarding import OnboardingStepKey

            await try_complete_onboarding_step(
                self.onboarding_repository,
                store_id,
                OnboardingStepKey.ADD_PRODUCT,
            )

        return ProductDTO.from_entity(created_product)
