"""Test webhook receiver.

A lightweight HTTP server that receives and displays NUMU webhook payloads.
Verifies the HMAC-SHA256 signature, prints the event payload, and always
returns 200 so the delivery is marked successful.

Usage:
    python scripts/test_webhook_receiver.py [--port 8099] [--secret <your-secret>]

Then register this URL as a webhook endpoint:
    http://localhost:8099/webhook   (use ngrok if the API is on a remote host)

The server will print every incoming event with full payload details.
"""

import argparse
import hashlib
import hmac
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ANSI colours — used when stdout is a terminal
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

_SECRET: str | None = None  # set by --secret arg
_REQUEST_COUNT = 0


def _colour(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if sys.stdout.isatty() else text


def _verify_signature(secret: str, body: bytes, received_sig: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_sig)


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # suppress default access log
        pass

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, b"OK")
        else:
            self._respond(404, b"Not found")

    def do_POST(self):
        global _REQUEST_COUNT
        _REQUEST_COUNT += 1
        seq = _REQUEST_COUNT

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Headers of interest
        event_type = self.headers.get("X-NUMU-Event", "unknown")
        delivery_id = self.headers.get("X-NUMU-Delivery", "—")
        signature = self.headers.get("X-NUMU-Signature", "")

        now = datetime.now().strftime("%H:%M:%S")

        # Signature check
        sig_status = _colour("✓ valid", GREEN)
        if _SECRET:
            if _verify_signature(_SECRET, body, signature):
                sig_status = _colour("✓ valid", GREEN)
            else:
                sig_status = _colour("✗ INVALID", RED)
        else:
            sig_status = _colour("? (no --secret given)", YELLOW)

        # Parse payload
        try:
            payload = json.loads(body)
            payload_pretty = json.dumps(payload, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            payload_pretty = body.decode(errors="replace")

        # Print summary
        print()
        print(_colour(f"── #{seq} [{now}] ─────────────────────────────────", BOLD))
        print(f"  Event     : {_colour(event_type, CYAN)}")
        print(f"  Delivery  : {delivery_id}")
        print(f"  Signature : {sig_status}")
        if _SECRET and "✗" in sig_status:
            print(f"  Received  : {signature}")
        print("  Payload   :")
        for line in payload_pretty.splitlines():
            print(f"    {line}")

        self._respond(200, b'{"received": true}')

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="NUMU test webhook receiver")
    parser.add_argument(
        "--port", type=int, default=8099, help="Port to listen on (default: 8099)"
    )
    parser.add_argument(
        "--secret", type=str, default=None, help="Webhook signing secret to verify HMAC"
    )
    args = parser.parse_args()

    global _SECRET
    _SECRET = args.secret

    server = HTTPServer(("0.0.0.0", args.port), WebhookHandler)

    print(_colour("NUMU Webhook Test Receiver", BOLD))
    print(f"  Listening on : http://0.0.0.0:{args.port}/webhook")
    print(f"  Health check : http://localhost:{args.port}/health")
    if args.secret:
        print(f"  Signature    : {_colour('HMAC verification enabled', GREEN)}")
    else:
        print(
            f"  Signature    : {_colour('not verifying (pass --secret <s> to enable)', YELLOW)}"
        )
    print()
    print("Register this URL in the API:")
    print("  POST /api/v1/stores/{store_id}/webhooks")
    print(
        f'  Body: {{"url": "http://localhost:{args.port}/webhook", "events": ["order.created", "order.paid", "product.created"]}}'
    )
    print()
    print("Waiting for events... (Ctrl+C to stop)")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nStopped. Received {_REQUEST_COUNT} webhook(s).")


if __name__ == "__main__":
    main()
