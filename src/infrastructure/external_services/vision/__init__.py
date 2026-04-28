"""InstaPay payment-proof OCR — multi-provider vision module.

Exposes :class:`IProofVisionService` and concrete implementations
that extract amount + recipient IPA from a sanitized proof image.
The use case calls one provider per submission, picked per-store
via :func:`get_proof_vision_service_for_store`.
"""

from src.infrastructure.external_services.vision.proof_vision_service import (
    DeepSeekHFProofService,
    GlmHFProofService,
    GoogleVisionProofService,
    IProofVisionService,
    NoopProofVisionService,
    ProofVisionResult,
)

__all__ = [
    "IProofVisionService",
    "ProofVisionResult",
    "GoogleVisionProofService",
    "DeepSeekHFProofService",
    "GlmHFProofService",
    "NoopProofVisionService",
]
