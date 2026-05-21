"""EmailTemplate entity for store-level transactional email customization.

Each template is keyed by (store_id, event_type, language) and contains the
subject + HTML body that will be rendered (via Jinja) when the corresponding
event fires. The set of valid ``event_type`` values lives in the email-event
registry at the application layer; this entity intentionally keeps the field
as a free-form string and defers semantic validation to the use-case layer.
"""

import re
from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from src.core.entities.base import BaseEntity

# Pre-compiled to avoid re-compilation on every entity instance.
# Matches Jinja-style ``{{ var }}`` references and captures the variable name.
# Intentionally tolerant — full Jinja parsing happens elsewhere; this helper
# only flags obvious unknown identifiers.
_VAR_REF_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)")


class EmailTemplate(BaseEntity):
    """Per-store email template entity.

    Templates are scoped to a store and an (event_type, language) pair —
    only one row may exist per triple, enforced by a unique index at the
    DB layer. Disabled templates are skipped on send.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    event_type: str = Field(..., max_length=50)
    language: str = Field(..., pattern=r"^(ar|en)$")
    name: str = Field(..., max_length=255)
    subject: str = Field(..., min_length=1, max_length=500)
    html_body: str = Field(..., min_length=1)
    is_enabled: bool = True
    from_name: str | None = Field(default=None, max_length=255)
    reply_to: EmailStr | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)

    def references_unknown_variables(self, allowed: set[str]) -> set[str]:
        """Return the set of ``{{ variable }}`` names not present in ``allowed``.

        This is a convenience helper for surfacing obvious typos in template
        bodies before they reach the renderer. It is **not** a full Jinja
        parser — control structures, filters, and dotted-attribute access
        are not introspected. The authoritative validation happens in the
        use-case layer against the email-event registry.

        Args:
            allowed: Set of permitted variable names for this event type.

        Returns:
            Set of variable names referenced by ``subject`` or ``html_body``
            that are not members of ``allowed``. Empty set when all
            references resolve cleanly.
        """
        referenced: set[str] = set()
        for source in (self.subject, self.html_body):
            referenced.update(_VAR_REF_RE.findall(source))
        return referenced - allowed
