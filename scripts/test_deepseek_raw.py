"""Direct gradio_client call against DeepSeek-OCR-Demo /run.

No timeout, no soft-fail wrapper — surfaces the raw exception or
return value so we can adjust the production service to match.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from gradio_client import Client, handle_file


def main(image_path: Path) -> None:
    print("Connecting to merterbak/DeepSeek-OCR-Demo...")
    t0 = time.monotonic()
    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    print(f"  hf_token={'<set>' if hf_token else '<unset>'}")
    client = (
        Client("merterbak/DeepSeek-OCR-Demo", token=hf_token)
        if hf_token
        else Client("merterbak/DeepSeek-OCR-Demo")
    )
    print(f"  connected in {time.monotonic() - t0:.1f}s")

    print()
    print(f"Calling /run with image={image_path.name}...")
    t0 = time.monotonic()
    try:
        result = client.predict(
            image=handle_file(str(image_path)),
            file_path=handle_file(str(image_path)),
            task="📝 Free OCR",
            custom_prompt="",
            page_num=1,
            api_name="/run",
        )
        print(f"  succeeded in {time.monotonic() - t0:.1f}s")
        # Windows cp1252 console can't render emoji the Space returns;
        # write the full payload to a UTF-8 file and only echo the
        # path so the terminal stays happy.
        import json

        out_path = Path("scripts/_deepseek_last_result.txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            f.write(f"type={type(result).__name__}\n")
            if isinstance(result, list | tuple):
                f.write(f"length={len(result)}\n\n")
                for i, item in enumerate(result):
                    f.write(f"--- [{i}] ({type(item).__name__}) ---\n")
                    if isinstance(item, str):
                        f.write(item)
                    elif isinstance(item, list | dict):
                        f.write(json.dumps(item, ensure_ascii=False, indent=2))
                    else:
                        f.write(repr(item))
                    f.write("\n\n")
            else:
                f.write(repr(result))
        print(f"  wrote: {out_path}")
    except Exception as exc:
        print(f"  FAILED in {time.monotonic() - t0:.1f}s: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <image-path>", file=sys.stderr)
        sys.exit(2)
    main(Path(sys.argv[1]))
