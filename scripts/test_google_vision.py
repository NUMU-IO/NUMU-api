"""Manual smoke test for GoogleVisionProofService.

Reads ``GOOGLE_VISION_API_KEY`` from env, runs the sanitizer + Vision
extract pipeline against a local image, and prints what came out.
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
    GoogleVisionProofService,
)


async def main(image_path: Path) -> None:
    api_key = os.environ.get("GOOGLE_VISION_API_KEY")
    if not api_key:
        print("GOOGLE_VISION_API_KEY env var not set", file=sys.stderr)
        sys.exit(2)

    raw = image_path.read_bytes()
    print(f"raw_bytes={len(raw)}")

    sanitized = sanitize_proof_image(raw, content_type="image/png")
    print(f"sanitized_bytes={len(sanitized.bytes)}")

    svc = GoogleVisionProofService(api_key=api_key)
    result = await svc.extract(sanitized.bytes)

    print()
    print("=" * 60)
    print(f"status={result.status}")
    print(f"provider={result.provider}")
    print(f"extracted_amount_cents={result.extracted_amount_cents}")
    print(f"extracted_ipa={result.extracted_ipa}")
    print("=" * 60)

    # Full raw_text contains Arabic and possibly emoji; write to a
    # UTF-8 file so the Windows cp1252 console doesn't choke.
    out_path = Path("scripts/_google_vision_last_text.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.raw_text, encoding="utf-8")
    print(f"raw_text written to: {out_path} ({len(result.raw_text)} chars)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <image-path>", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(Path(sys.argv[1])))
