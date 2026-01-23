"""Login customer use case."""

from uuid import UUID

from src.application.dto.customer import CustomerAuthResponseDTO, CustomerDTO, CustomerLoginDTO, CustomerTokenDTO
from src.core.exceptions import AuthenticationError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.token_service import ITokenService
from src.core.value_objects.email import Email


class LoginCustomerUseCase:
    """Use case for authenticating a customer."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        password_service: IPasswordService,
        token_service: ITokenService,
    ) -> None:
        self.customer_repository = customer_repository
        self.password_service = password_service
        self.token_service = token_service

    async def execute(self, dto: CustomerLoginDTO) -> CustomerAuthResponseDTO:
        """Authenticate customer and return auth response."""
        store_id = UUID(dto.store_id)
        email = Email(dto.email)

        # Get customer by email
        customer = await self.customer_repository.get_by_email(store_id, email)
        if not customer:
            raise AuthenticationError("Invalid email or password")

        # Verify password
        if not customer.password_hash:
            raise AuthenticationError("Invalid email or password")

        if not self.password_service.verify_password(dto.password, customer.password_hash):
            raise AuthenticationError("Invalid email or password")

        # Generate tokens
        access_token = self.token_service.create_customer_access_token(customer)
        refresh_token = self.token_service.create_customer_refresh_token(customer)

        return CustomerAuthResponseDTO(
            customer=CustomerDTO.from_entity(customer),
            tokens=CustomerTokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )
