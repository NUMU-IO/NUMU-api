"""Register customer use case."""

from uuid import UUID

from src.application.dto.customer import CustomerAuthResponseDTO, CustomerDTO, CustomerRegisterDTO, CustomerTokenDTO
from src.core.entities.customer import Customer
from src.core.exceptions import EntityAlreadyExistsError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class RegisterCustomerUseCase:
    """Use case for registering a new customer for a store."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
    ) -> None:
        self.customer_repository = customer_repository
        self.password_service = password_service
        self.token_service = token_service

    async def execute(
        self, dto: CustomerRegisterDTO, tenant_id: UUID
    ) -> CustomerAuthResponseDTO:
        """Register a new customer and return auth response."""
        store_id = UUID(dto.store_id)
        email = Email(dto.email)

        # Check if email already exists for this store
        if await self.customer_repository.email_exists(store_id, email):
            raise EntityAlreadyExistsError("Customer", "email", dto.email)

        # Hash password
        hashed_password = self.password_service.hash_password(dto.password)

        # Create customer entity
        customer = Customer(
            store_id=store_id,
            email=email,
            first_name=dto.first_name,
            last_name=dto.last_name,
            phone=PhoneNumber(dto.phone) if dto.phone else None,
            password_hash=hashed_password,
            accepts_marketing=dto.accepts_marketing,
        )

        # Save customer
        created_customer = await self.customer_repository.create(customer, tenant_id)

        # Generate tokens (using customer ID as subject)
        access_token = self.token_service.create_customer_access_token(created_customer)
        refresh_token = self.token_service.create_customer_refresh_token(created_customer)

        return CustomerAuthResponseDTO(
            customer=CustomerDTO.from_entity(created_customer),
            tokens=CustomerTokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )
