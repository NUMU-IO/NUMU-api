"""Back-compat shim — the rules engine moved to ``manual_transfer``.

See :mod:`src.infrastructure.external_services.manual_transfer.auto_approval`.
It was always rail-agnostic; it now lives next to the rest of the
manual-payment code instead of under the InstaPay-only name.
"""

from src.infrastructure.external_services.manual_transfer.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
    evaluate,
)

__all__ = [
    "AutoApprovalConfig",
    "AutoApprovalDecision",
    "AutoApprovalFacts",
    "evaluate",
]
