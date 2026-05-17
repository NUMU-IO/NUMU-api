"""Direct Graph API smoke — runs the user's three curl examples.

Mirrors the curl payloads exactly, just dispatched via httpx so the
SHA-256 step (example 2) and the JSON quoting work cleanly on Windows.

Hits ``graph.facebook.com/v17.0/{pixel_id}/events`` with the user's
test_event_code, prints the full response per example.
"""

import hashlib
import json
import sys
import time

import httpx

# Meta requires event_time within the last 7 days. The user's example
# payloads use a hardcoded 1700000000 (Nov 2023) which Meta rejects.
# Substituting "now" — keeps everything else byte-for-byte identical.
NOW = int(time.time())

PIXEL_ID = "1552896226251388"
ACCESS_TOKEN = (
    "EAAOLtA4NeCgBRfiA0fQuqaaZCA0ZAeGoD2qzPbzA9PZBLdW4ZBBUTpa2NjxPMZBN7"
    "LWhpFthWsKAeXKuyFvu4jnRm6A8kqvBphjEyWtcZAEcJUS8S1dUkUss60ZCoNxYSR"
    "CrPaQ65UVMjxBeaH958GRZCLRP1GJm3HX1l17rZAthvPQpi0ubw5f760eFzW3GEmQ"
    "namwZDZD"
)
TEST_EVENT_CODE = "TEST27073"
ENDPOINT = f"https://graph.facebook.com/v17.0/{PIXEL_ID}/events"


def _post(payload: dict, label: str) -> None:
    """POST and pretty-print."""
    print(f"\n=== {label} ===")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(ENDPOINT, json=payload)
    print(f"HTTP {resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(body, indent=2))
    except Exception:
        print(resp.text[:1000])


def example_1() -> None:
    """Bare Purchase, no user_data, no custom_data."""
    _post(
        {
            "data": [
                {
                    "event_name": "Purchase",
                    "event_time": NOW,
                    "event_id": "purchase-test-1",
                    "action_source": "website",
                }
            ],
            "test_event_code": TEST_EVENT_CODE,
            "access_token": ACCESS_TOKEN,
        },
        "Example 1: bare Purchase",
    )


def example_2() -> None:
    """Purchase with hashed email + custom_data."""
    email = "user@example.com"
    em_hashed = hashlib.sha256(email.encode()).hexdigest()
    print(f"\n[step] SHA-256({email!r}) = {em_hashed}")
    _post(
        {
            "data": [
                {
                    "event_name": "Purchase",
                    "event_time": NOW,
                    "event_id": "purchase-test-2",
                    "action_source": "website",
                    "user_data": {"em": em_hashed},
                    "custom_data": {"currency": "EGP", "value": 150.00},
                }
            ],
            "test_event_code": TEST_EVENT_CODE,
            "access_token": ACCESS_TOKEN,
        },
        "Example 2: hashed email + custom_data",
    )


def example_3() -> None:
    """Batch of 3 Purchase events with action_source=server."""
    _post(
        {
            "data": [
                {
                    "event_name": "Purchase",
                    "event_time": NOW,
                    "event_id": "purchase-batch-1",
                    "action_source": "server",
                },
                {
                    "event_name": "Purchase",
                    "event_time": 1700000001,
                    "event_id": "purchase-batch-2",
                    "action_source": "server",
                },
                {
                    "event_name": "Purchase",
                    "event_time": 1700000002,
                    "event_id": "purchase-batch-3",
                    "action_source": "server",
                },
            ],
            "test_event_code": TEST_EVENT_CODE,
            "access_token": ACCESS_TOKEN,
        },
        "Example 3: batch of 3 server events",
    )


if __name__ == "__main__":
    example_1()
    example_2()
    example_3()
    sys.exit(0)
