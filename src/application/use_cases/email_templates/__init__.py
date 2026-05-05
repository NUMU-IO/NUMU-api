"""Email template use cases module."""

from src.application.use_cases.email_templates.create_email_template import (
    CreateEmailTemplateUseCase,
)
from src.application.use_cases.email_templates.delete_email_template import (
    DeleteEmailTemplateUseCase,
)
from src.application.use_cases.email_templates.get_default_template import (
    GetDefaultTemplateUseCase,
)
from src.application.use_cases.email_templates.get_email_template import (
    GetEmailTemplateUseCase,
)
from src.application.use_cases.email_templates.list_email_templates import (
    ListEmailTemplatesUseCase,
)
from src.application.use_cases.email_templates.send_test_email import (
    SendTestEmailUseCase,
)
from src.application.use_cases.email_templates.update_email_template import (
    UpdateEmailTemplateUseCase,
)

__all__ = [
    "CreateEmailTemplateUseCase",
    "DeleteEmailTemplateUseCase",
    "GetDefaultTemplateUseCase",
    "GetEmailTemplateUseCase",
    "ListEmailTemplatesUseCase",
    "SendTestEmailUseCase",
    "UpdateEmailTemplateUseCase",
]
