"""Manual smoke test for DeepSeekHFProofService.

Runs the real provider against a local image. Prints sanitized-bytes
size, the OCR result fields, and the extracted parser values.

Usage:
    python scripts/test_deepseek_extract.py <path-to-image>
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from src.infrastructure.external_services.image.proof_sanitizer import (
    sanitize_proof_image,
)
from src.infrastructure.external_services.vision.proof_vision_service import (
    DeepSeekHFProofService,
)


async def main(image_path: Path) -> None:
    raw = image_path.read_bytes()
    print(f"raw_bytes={len(raw)}")

    sanitized = sanitize_proof_image(raw, content_type="image/png")
    print(f"sanitized_bytes={len(sanitized.bytes)}")
    print(f"perceptual_hash={sanitized.perceptual_hash}")

    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    print(f"hf_token={'<set>' if hf_token else '<unset — anonymous>'}")
    svc = DeepSeekHFProofService(hf_token=hf_token)
    result = await svc.extract(sanitized.bytes)
    print()
    print("=" * 60)
    print(f"status={result.status}")
    print(f"provider={result.provider}")
    print(f"extracted_amount_cents={result.extracted_amount_cents}")
    print(f"extracted_ipa={result.extracted_ipa}")
    print("--- raw_text ---")
    print(result.raw_text)
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <image-path>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(Path(sys.argv[1])))
