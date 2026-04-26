"""Per-use-case dependency factories for the email-template feature.

These wire the use cases declared in
:mod:`src.application.use_cases.email_templates` to their concrete
infrastructure dependencies (repos, email service, renderer).

Each factory returns a fresh use-case instance per request — use cases
themselves are stateless beyond the repos they hold.
"""

from typing import Annotated

from fastapi import Depends

from src.api.dependencies.repositories import (
    get_email_template_repository,
    get_store_repository,
)
from src.api.dependencies.services import (
    get_email_service,
    get_email_template_renderer,
)
from src.application.services.email_template_renderer import EmailTemplateRenderer
from src.application.use_cases.email_templates import (
    CreateEmailTemplateUseCase,
    DeleteEmailTemplateUseCase,
    GetDefaultTemplateUseCase,
    GetEmailTemplateUseCase,
    ListEmailTemplatesUseCase,
    SendTestEmailUseCase,
    UpdateEmailTemplateUseCase,
)
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.email_service import IEmailService


def get_create_email_template_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> CreateEmailTemplateUseCase:
    """Build the create-email-template use case."""
    return CreateEmailTemplateUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
    )


def get_update_email_template_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> UpdateEmailTemplateUseCase:
    """Build the update-email-template use case."""
    return UpdateEmailTemplateUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
    )


def get_delete_email_template_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> DeleteEmailTemplateUseCase:
    """Build the delete-email-template use case."""
    return DeleteEmailTemplateUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
    )


def get_get_email_template_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> GetEmailTemplateUseCase:
    """Build the get-email-template use case."""
    return GetEmailTemplateUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
    )


def get_list_email_templates_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> ListEmailTemplatesUseCase:
    """Build the list-email-templates use case."""
    return ListEmailTemplatesUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
    )


def get_default_template_use_case(
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
) -> GetDefaultTemplateUseCase:
    """Build the get-default-template use case (registry-only, no template DB read)."""
    return GetDefaultTemplateUseCase(store_repository=store_repo)


def get_send_test_email_use_case(
    email_template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
    email_service: Annotated[IEmailService, Depends(get_email_service)],
    renderer: Annotated[EmailTemplateRenderer, Depends(get_email_template_renderer)],
) -> SendTestEmailUseCase:
    """Build the send-test-email use case."""
    return SendTestEmailUseCase(
        email_template_repository=email_template_repo,
        store_repository=store_repo,
        email_service=email_service,
        renderer=renderer,
    )


__all__ = [
    "get_create_email_template_use_case",
    "get_update_email_template_use_case",
    "get_delete_email_template_use_case",
    "get_get_email_template_use_case",
    "get_list_email_templates_use_case",
    "get_default_template_use_case",
    "get_send_test_email_use_case",
]
