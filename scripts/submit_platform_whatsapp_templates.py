"""Submit the rich (Bosta-style) WhatsApp system templates to the PLATFORM WABA.

Platform-managed stores share NUMU's single Meta WABA, and the in-app
``SubmitTemplateUseCase`` is BYO-only by design (FR-026). This one-off admin
script submits the canonical templates from ``src/core/whatsapp_rich_templates.py``
to the platform WABA out-of-band, using the platform credentials in settings.

Idempotent: templates already present on the WABA (matched by name+language)
are skipped. Newly created templates land in Meta's review queue (status
PENDING); the existing status-poll task / webhook flips the local rows to
APPROVED when Meta approves.

Usage (run inside the API container / venv so settings + creds resolve):

    python -m scripts.submit_platform_whatsapp_templates            # submit
    python -m scripts.submit_platform_whatsapp_templates --dry-run  # print only

Run against the TEST stack first, verify approval + rendering, then run on prod.
"""

import argparse
import asyncio
import sys

from src.config import settings
from src.core.whatsapp_rich_templates import RICH_TEMPLATES
from src.infrastructure.external_services.meta.template_client import TemplateClient


def _build_components(tmpl: dict) -> list[dict]:
    """Build Meta's ``components`` array, including the required body example."""
    components: list[dict] = []
    body: dict = {"type": "BODY", "text": tmpl["body"]}
    examples = tmpl.get("body_examples")
    if examples:
        # Meta requires example.body_text for any BODY with {{n}} placeholders.
        body["example"] = {"body_text": [examples]}
    components.append(body)
    if tmpl.get("footer"):
        components.append({"type": "FOOTER", "text": tmpl["footer"]})
    if tmpl.get("buttons"):
        components.append({"type": "BUTTONS", "buttons": tmpl["buttons"]})
    return components


async def _run(dry_run: bool) -> int:
    waba_id = settings.whatsapp_business_account_id
    token = settings.whatsapp_access_token
    if not waba_id or not token:
        print(
            "ERROR: platform WABA creds missing "
            "(whatsapp_business_account_id / whatsapp_access_token).",
            file=sys.stderr,
        )
        return 2

    client = TemplateClient(waba_id=waba_id, access_token=token)
    try:
        # Page through existing templates to skip ones already submitted.
        existing: set[tuple[str, str]] = set()
        after: str | None = None
        while True:
            page = await client.list_templates(limit=100, after=after)
            for t in page.get("data", []):
                existing.add((t.get("name"), t.get("language")))
            after = (page.get("paging", {}).get("cursors", {}) or {}).get("after")
            if not after or not page.get("data"):
                break

        created = skipped = failed = 0
        for tmpl in RICH_TEMPLATES:
            key = (tmpl["name"], tmpl["language"])
            if key in existing:
                print(f"  skip (exists): {key[0]} [{key[1]}]")
                skipped += 1
                continue
            if dry_run:
                print(f"  would submit: {key[0]} [{key[1]}] ({tmpl['category']})")
                continue
            try:
                resp = await client.create_template(
                    name=tmpl["name"],
                    category=tmpl["category"],
                    language=tmpl["language"],
                    components=_build_components(tmpl),
                )
                print(
                    f"  submitted: {key[0]} [{key[1]}] -> "
                    f"id={resp.get('id')} status={resp.get('status', 'PENDING')}"
                )
                created += 1
            except Exception as exc:  # noqa: BLE001 — report + continue
                detail = ""
                err_data = getattr(exc, "error_data", None)
                if isinstance(err_data, dict):
                    detail = (
                        f" [subcode={err_data.get('error_subcode')}: "
                        f"{err_data.get('error_user_title')} — "
                        f"{err_data.get('error_user_msg')}]"
                    )
                print(f"  FAILED: {key[0]} [{key[1]}]: {exc}{detail}", file=sys.stderr)
                failed += 1

        print(f"\nDone. created={created} skipped={skipped} failed={failed}")
        return 1 if failed else 0
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be submitted without calling Meta.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.dry_run)))


if __name__ == "__main__":
    main()
