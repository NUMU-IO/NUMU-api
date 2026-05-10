"""Server-side customizer undo entries — Phase 6.

The V3 customizer has always supported undo/redo via a client-side
50-FIFO. That FIFO is lost on tab close, full reload, or switching
machines. Phase 6 persists each undo entry server-side so:

* Closing the tab and re-opening preserves the undo stack.
* A merchant editing on desktop and switching to laptop sees the
  same history (no cross-tab merge magic — last-write-wins per user).
* Audit-log events can reference a stable entry_id rather than a
  client-side ordinal that dies with the tab.

The cap is 50 entries per (user, store, theme); on each insert
older rows are pruned in the same transaction. This keeps the
table bounded — at 5k stores × 50 × ~2KB JSON = ~500MB worst-case,
which is fine.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class CustomizerUndoEntry(BaseEntity):
    """One reversible action in the customizer undo stack."""

    tenant_id: UUID
    store_id: UUID
    user_id: UUID
    theme_id: str  # "bazar", "modern", or BYOT UUID
    # The action verb so the UI can label the undo button:
    # "Edit setting", "Move section", "Add block", etc.
    action_label: str
    # The forward+inverse payload. The customizer applies `inverse` on
    # undo, `forward` on redo. Shape is up to the client; we just
    # round-trip the JSON.
    forward: dict[str, Any] = Field(default_factory=dict)
    inverse: dict[str, Any] = Field(default_factory=dict)
