"""Unit tests for ConfigureCredentialsUseCase.

This module tests the configure credentials use case including:
- Successful credential configuration
- Credential validation failure
- Updating existing credentials
- Request status updates
- Audit log creation
- Error handling scenarios
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from src.application.use_cases.configuration.configure_credentials import (
    ConfigureCredentialsUseCase,
)
from src.infrastructure.database.models.tenant.configuration import (
    AuditAction,
    CredentialAuditLog,
    RequestStatus,
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.gateway_validators.base import (
    ValidationResult,
)

# =============================================================================
# Test Fixtures Specific to Configure Credentials
# =============================================================================

@pytest.fixture
def use_case(mock_db_session: AsyncMock) -> ConfigureCredentialsUseCase:
    """Create a ConfigureCredentialsUseCase instance with mocked dependencies."""
    return ConfigureCredentialsUseCase(db=mock_db_session)


@pytest.fixture
def mock_execute_result_no_credential():
    """Create a mock execute result that returns no existing credential."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


@pytest.fixture
def mock_execute_result_with_credential(mock_service_credential):
    """Create a mock execute result that returns an existing credential."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_service_credential
    return result


@pytest.fixture
def mock_execute_result_with_request(mock_configuration_request):
    """Create a mock execute result that returns an existing request."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_configuration_request
    return result


# =============================================================================
# Successful Configuration Tests
# =============================================================================

class TestConfigureCredentialsSuccess:
    """Test successful credential configuration scenarios."""

    @pytest.mark.asyncio
    async def test_configure_new_credentials_success(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
        mock_execute_result_no_credential: MagicMock,
    ):
        """Test configuring new credentials for a service."""
        # Setup mocks
        mock_db_session.execute.return_value = mock_execute_result_no_credential

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            # Setup validator mock
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={"merchant_code": "FWY***789"})
            mock_factory.get_validator.return_value = mock_validator

            # Setup secrets manager mock
            mock_secrets.encrypt_credentials.return_value = b"encrypted_data"

            # Execute
            result = await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
            )

            # Assertions
            assert result.is_configured is True
            assert result.is_active is True
            assert result.is_validated is True
            assert result.tenant_id == tenant_id
            assert result.service_type == ServiceType.PAYMENT_GATEWAY
            assert result.service_name == ServiceName.FAWRY

            # Verify validator was called
            mock_validator.validate.assert_called_once_with(valid_fawry_credentials)

            # Verify encryption was called
            mock_secrets.encrypt_credentials.assert_called_once_with(valid_fawry_credentials)

            # Verify database operations
            mock_db_session.add.assert_called()
            mock_db_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_configure_credentials_with_request_id(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        request_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
        mock_configuration_request: MagicMock,
    ):
        """Test configuring credentials and updating associated request."""
        # Setup mocks - first call returns no credential, second returns request
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # No existing credential
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_configuration_request)),  # Request found
        ]

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={"merchant_code": "FWY***789"})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"encrypted_data"

            # Execute
            result = await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
                request_id=request_id,
                admin_notes="Configured successfully",
            )

            # Assertions
            assert result.is_configured is True

            # Verify request was updated
            assert mock_configuration_request.status == RequestStatus.COMPLETED
            assert mock_configuration_request.completed_at is not None
            assert mock_configuration_request.admin_notes == "Configured successfully"

    @pytest.mark.asyncio
    async def test_update_existing_credentials(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
        mock_service_credential: MagicMock,
    ):
        """Test updating existing credentials for a service."""
        # Setup mock to return existing credential
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_service_credential)
        )

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={"merchant_code": "FWY***NEW"})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"new_encrypted_data"

            # Execute
            result = await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
            )

            # Assertions
            assert result.is_configured is True

            # Verify existing credential was updated
            assert mock_service_credential.encrypted_credentials == b"new_encrypted_data"
            assert mock_service_credential.is_validated is True
            assert mock_service_credential.is_active is True


# =============================================================================
# Validation Failure Tests
# =============================================================================

class TestConfigureCredentialsValidationFailure:
    """Test credential validation failure scenarios."""

    @pytest.mark.asyncio
    async def test_configure_credentials_validation_fails(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_failure: ValidationResult,
    ):
        """Test that configuration fails when validation fails."""
        with patch.object(use_case, 'validator_factory') as mock_factory:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_failure)
            mock_factory.get_validator.return_value = mock_validator

            # Execute and expect ValueError
            with pytest.raises(ValueError) as exc_info:
                await use_case.execute(
                    tenant_id=tenant_id,
                    admin_id=admin_id,
                    service_type=ServiceType.PAYMENT_GATEWAY,
                    service_name=ServiceName.FAWRY,
                    credentials=valid_fawry_credentials,
                )

            # Verify error message contains validation failure info
            assert "validation failed" in str(exc_info.value).lower()

            # Verify no database operations occurred
            mock_db_session.add.assert_not_called()
            mock_db_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_configure_credentials_invalid_service_type(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
    ):
        """Test configuration with mismatched service type and name."""
        with patch.object(use_case, 'validator_factory') as mock_factory:
            # Factory raises error for invalid combination
            mock_factory.get_validator.side_effect = ValueError("Unsupported service")

            with pytest.raises(ValueError):
                await use_case.execute(
                    tenant_id=tenant_id,
                    admin_id=admin_id,
                    service_type=ServiceType.SHIPPING_CARRIER,  # Wrong type for Fawry
                    service_name=ServiceName.FAWRY,
                    credentials=valid_fawry_credentials,
                )


# =============================================================================
# Audit Log Tests
# =============================================================================

class TestConfigureCredentialsAuditLog:
    """Test audit log creation during credential configuration."""

    @pytest.mark.asyncio
    async def test_audit_log_created_on_new_configuration(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
    ):
        """Test that audit log is created when configuring new credentials."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"encrypted"

            await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
            )

            # Verify audit log was added
            add_calls = mock_db_session.add.call_args_list

            # Should have at least 2 add calls: credential and audit log
            assert len(add_calls) >= 2

            # Find the audit log call
            audit_log_added = False
            for call in add_calls:
                obj = call[0][0]
                if isinstance(obj, CredentialAuditLog):
                    audit_log_added = True
                    assert obj.tenant_id == tenant_id
                    assert obj.user_id == admin_id
                    assert obj.action == AuditAction.CREDENTIALS_CONFIGURED
                    break

            assert audit_log_added, "Audit log should be created"

    @pytest.mark.asyncio
    async def test_audit_log_indicates_update_vs_create(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
        mock_service_credential: MagicMock,
    ):
        """Test that audit log correctly indicates if it's an update."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_service_credential)
        )

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"encrypted"

            await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
            )

            # Find audit log and check is_update flag
            for call in mock_db_session.add.call_args_list:
                obj = call[0][0]
                if isinstance(obj, CredentialAuditLog):
                    assert obj.details.get("is_update") is True
                    break


# =============================================================================
# Get Status Tests
# =============================================================================

class TestGetCredentialStatus:
    """Test getting credential status."""

    @pytest.mark.asyncio
    async def test_get_status_configured(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        mock_service_credential: MagicMock,
    ):
        """Test getting status for configured credentials."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_service_credential)
        )

        result = await use_case.get_status(
            tenant_id=tenant_id,
            service_type=ServiceType.PAYMENT_GATEWAY,
            service_name=ServiceName.FAWRY,
        )

        assert result is not None
        assert result.is_configured is True
        assert result.is_active == mock_service_credential.is_active
        assert result.is_validated == mock_service_credential.is_validated

    @pytest.mark.asyncio
    async def test_get_status_not_configured(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
    ):
        """Test getting status for non-configured service."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        result = await use_case.get_status(
            tenant_id=tenant_id,
            service_type=ServiceType.PAYMENT_GATEWAY,
            service_name=ServiceName.FAWRY,
        )

        assert result is None


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestConfigureCredentialsEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_database_error_during_commit(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
    ):
        """Test handling of database errors during commit."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )
        mock_db_session.commit.side_effect = Exception("Database connection lost")

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"encrypted"

            with pytest.raises(Exception) as exc_info:
                await use_case.execute(
                    tenant_id=tenant_id,
                    admin_id=admin_id,
                    service_type=ServiceType.PAYMENT_GATEWAY,
                    service_name=ServiceName.FAWRY,
                    credentials=valid_fawry_credentials,
                )

            assert "Database connection lost" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_encryption_error(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
    ):
        """Test handling of encryption errors."""
        mock_db_session.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.side_effect = Exception("Encryption failed")

            with pytest.raises(Exception) as exc_info:
                await use_case.execute(
                    tenant_id=tenant_id,
                    admin_id=admin_id,
                    service_type=ServiceType.PAYMENT_GATEWAY,
                    service_name=ServiceName.FAWRY,
                    credentials=valid_fawry_credentials,
                )

            assert "Encryption failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_credentials(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        mock_validation_result_failure: ValidationResult,
    ):
        """Test configuration with empty credentials."""
        with patch.object(use_case, 'validator_factory') as mock_factory:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_failure)
            mock_factory.get_validator.return_value = mock_validator

            with pytest.raises(ValueError):
                await use_case.execute(
                    tenant_id=tenant_id,
                    admin_id=admin_id,
                    service_type=ServiceType.PAYMENT_GATEWAY,
                    service_name=ServiceName.FAWRY,
                    credentials={},  # Empty credentials
                )

    @pytest.mark.asyncio
    async def test_request_not_found(
        self,
        use_case: ConfigureCredentialsUseCase,
        mock_db_session: AsyncMock,
        tenant_id: UUID,
        admin_id: UUID,
        request_id: UUID,
        valid_fawry_credentials: dict,
        mock_validation_result_success: ValidationResult,
    ):
        """Test configuration when request_id is provided but not found."""
        # First call returns no credential, second returns no request
        mock_db_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]

        with patch.object(
            use_case, 'validator_factory'
        ) as mock_factory, patch.object(
            use_case, 'secrets_manager'
        ) as mock_secrets:
            mock_validator = AsyncMock()
            mock_validator.validate = AsyncMock(return_value=mock_validation_result_success)
            mock_validator.get_display_info = MagicMock(return_value={})
            mock_factory.get_validator.return_value = mock_validator
            mock_secrets.encrypt_credentials.return_value = b"encrypted"

            # Should still succeed, just not update any request
            result = await use_case.execute(
                tenant_id=tenant_id,
                admin_id=admin_id,
                service_type=ServiceType.PAYMENT_GATEWAY,
                service_name=ServiceName.FAWRY,
                credentials=valid_fawry_credentials,
                request_id=request_id,
            )

            assert result.is_configured is True
