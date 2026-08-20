"""Back-compat shim — QR generation moved to ``manual_transfer``.

See :mod:`src.infrastructure.external_services.manual_transfer.qr_generator`.
Only InstaPay has a scannable payload, but the module lives with the rest
of the manual-rail code so ``manual_transfer`` has no import back into
this package (which would be a cycle).
"""

from src.infrastructure.external_services.manual_transfer.qr_generator import (
    build_qr_payload,
    render_qr_data_url,
)

__all__ = ["build_qr_payload", "render_qr_data_url"]
