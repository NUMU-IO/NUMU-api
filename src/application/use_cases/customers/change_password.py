"""Change customer password use case."""

from uuid import UUID

from src.application.dto.customer import CustomerChangePasswordDTO
from src.core.exceptions import AuthenticationError, EntityNotFoundError
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.services.password_service import IPasswordService


class ChangeCustomerPasswordUseCase:
    """Use case for changing a customer's password."""

    def __init__(
        self,
        customer_repository: ICustomerRepository,
        password_service: IPasswordService,
    ) -> None:
        self.customer_repository = customer_repository
        self.password_service = password_service

    async def execute(self, customer_id: UUID, dto: CustomerChangePasswordDTO) -> bool:
        """Change customer password."""
        customer = await self.customer_repository.get_by_id(customer_id)

        if not customer:
            raise EntityNotFoundError("Customer", str(customer_id))

        # Verify current password
        if not customer.password_hash:
            raise AuthenticationError("Customer has no password set")

        if not self.password_service.verify_password(
            dto.current_password, customer.password_hash
        ):
            raise AuthenticationError("Current password is incorrect")

        # Hash new password
        new_password_hash = self.password_service.hash_password(dto.new_password)

        # Update password
        await self.customer_repository.update_password(customer_id, new_password_hash)

        return True
