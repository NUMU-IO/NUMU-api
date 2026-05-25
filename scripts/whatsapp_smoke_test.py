"""WhatsApp Business API smoke test (backend-030).

Pre-flight check before a real end-to-end send. Run from the deployed
environment (droplet / staging) so the env vars match production.

Usage:
    # Validate config + connectivity only (no send):
    python scripts/whatsapp_smoke_test.py

    # Send a test order_confirmation template to a real phone:
    python scripts/whatsapp_smoke_test.py --send --to +201001234567 --name "Test"

Steps:
    1. Validate env vars are set.
    2. Read phone metadata from Meta (proves access_token + phone_number_id).
    3. Read WABA metadata (proves whatsapp_business_management scope).
    4. List templates (proves the platform WABA has at least one APPROVED
       template — the migration's seeded templates need Meta-side approval
       before they can actually send).
    5. Optionally: dispatch a test order_confirmation template to a real
       phone via the messaging service (full guard path).

Exit codes:
    0  all checks passed (or send succeeded if --send)
    1  config error (missing env vars)
    2  Meta API connectivity failure
    3  send failure (only when --send is passed)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def _warn(msg: str) -> None:
    print(f"  warn  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _header(msg: str) -> None:
    print(f"\n=== {msg} ===")


def _check_env() -> tuple[bool, dict[str, str | None]]:
    """Step 1 — required env vars."""
    _header("Step 1 — env vars")
    required = [
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
        "META_APP_ID",
    ]
    optional = [
        "WHATSAPP_BUSINESS_API_VERSION",
        "WHATSAPP_ENABLED",
    ]
    values: dict[str, str | None] = {}
    missing: list[str] = []
    for key in required:
        v = os.environ.get(key)
        values[key] = v
        if not v or v.startswith("your_"):
            missing.append(key)
            _fail(f"{key} is not set (or still placeholder)")
        else:
            shown = (v[:6] + "..." + v[-4:]) if len(v) > 12 else "***"
            _ok(f"{key} = {shown}")
    for key in optional:
        v = os.environ.get(key)
        values[key] = v
        _ok(f"{key} = {v or '(unset; defaults apply)'}")
    if (os.environ.get("WHATSAPP_ENABLED", "false") or "false").lower() not in (
        "true",
        "1",
        "yes",
    ):
        _warn(
            "WHATSAPP_ENABLED is not true — sends will short-circuit. "
            "Set WHATSAPP_ENABLED=true for live testing."
        )
    return len(missing) == 0, values


async def _check_meta_connectivity(values: dict[str, str | None]) -> bool:
    """Steps 2 + 3 + 4 — phone read, WABA read, template list."""
    _header("Step 2-4 — Meta API connectivity")
    try:
        from src.infrastructure.external_services.meta.whatsapp_client import (
            WhatsAppClient,
        )
    except Exception as exc:
        _fail(f"Could not import WhatsAppClient: {exc}")
        return False

    client = WhatsAppClient(
        phone_number_id=values["WHATSAPP_PHONE_NUMBER_ID"] or "",
        access_token=values["WHATSAPP_ACCESS_TOKEN"] or "",
        waba_id=values["WHATSAPP_BUSINESS_ACCOUNT_ID"],
    )
    try:
        # Step 2 — phone metadata
        try:
            phone_info = await client.get_phone_number_info()
            display = phone_info.get("display_phone_number", "?")
            verified = phone_info.get("verified_name", "?")
            quality = phone_info.get("quality_rating", "?")
            _ok(f"Phone metadata: {display} ({verified}) quality={quality}")
        except Exception as exc:
            _fail(f"Phone metadata read failed: {exc}")
            return False

        # Step 3 — WABA info
        try:
            waba_info = await client.get_waba_info()
            _ok(f"WABA: id={waba_info.get('id')} name={waba_info.get('name', '?')}")
        except Exception as exc:
            _fail(f"WABA info read failed: {exc}")
            return False

        # Step 4 — templates list
        try:
            templates = await client.list_templates(limit=100)
            data = templates.get("data", []) if templates else []
            approved = [
                t for t in data if (t.get("status") or "").upper() == "APPROVED"
            ]
            _ok(f"Templates: {len(data)} total, {len(approved)} APPROVED")
            # Highlight the system templates that the migration seeded locally.
            wanted = {
                "order_confirmation",
                "payment_received",
                "order_shipped",
                "order_delivered",
                "abandoned_cart",
                "optout_confirmation",
            }
            approved_names = {t.get("name") for t in approved}
            missing_at_meta = wanted - approved_names
            if missing_at_meta:
                _warn(
                    "These system templates are NOT yet APPROVED at Meta — "
                    "sends using them will fail. Submit at Meta Business "
                    f"Manager: {sorted(missing_at_meta)}"
                )
            else:
                _ok("All canonical system templates are APPROVED at Meta")
        except Exception as exc:
            _fail(f"Template list failed: {exc}")
            return False
    finally:
        await client.close()
    return True


async def _send_test_message(
    *,
    to_phone: str,
    customer_name: str,
    language: str = "ar",
) -> bool:
    """Dispatch a real order_confirmation template via the messaging
    service (the full guard path runs)."""
    _header(f"Step 5 — send test order_confirmation to {to_phone}")
    try:
        from src.core.interfaces.services.messaging_service import (
            MessageRecipient,
        )
        from src.infrastructure.external_services.whatsapp.messaging_service import (
            WhatsAppMessagingService,
        )
    except Exception as exc:
        _fail(f"Could not import WhatsAppMessagingService: {exc}")
        return False

    service = WhatsAppMessagingService()
    recipient = MessageRecipient(phone=to_phone, name=customer_name, language=language)
    try:
        result = await service.send_order_confirmation(
            recipient,
            order_number="SMOKE-TEST-001",
            total="0.00 EGP",
            store_name="NUMU Smoke Test",
            tracking_url=None,
        )
    except Exception as exc:
        _fail(f"Send raised: {exc}")
        return False

    if result.success:
        _ok(f"Send succeeded; message_id={result.message_id}")
        _ok("Check the recipient phone — message should arrive within 30s")
        return True
    _fail(
        f"Send failed; status={result.status} "
        f"error_code={result.error_code} error={result.error_message}"
    )
    return False


async def _main() -> int:
    ap = argparse.ArgumentParser(description="WhatsApp Business API smoke test")
    ap.add_argument(
        "--send",
        action="store_true",
        help="Also dispatch a real order_confirmation to --to",
    )
    ap.add_argument("--to", help="E.164 phone (with +) for the test send")
    ap.add_argument(
        "--name", default="Test Customer", help="Recipient name placeholder"
    )
    ap.add_argument(
        "--lang",
        default="ar",
        choices=("ar", "en"),
        help="Template language (must be APPROVED at Meta)",
    )
    args = ap.parse_args()

    env_ok, values = _check_env()
    if not env_ok:
        return 1

    meta_ok = await _check_meta_connectivity(values)
    if not meta_ok:
        return 2

    if args.send:
        if not args.to:
            _fail("--send requires --to <E.164 phone>")
            return 3
        if not args.to.startswith("+"):
            _fail("--to must be E.164 (must start with +)")
            return 3
        sent = await _send_test_message(
            to_phone=args.to,
            customer_name=args.name,
            language=args.lang,
        )
        return 0 if sent else 3

    _header("All connectivity checks passed")
    _ok("Re-run with --send --to +<phone> --name <name> to send a real test message")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
