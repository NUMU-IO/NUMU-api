"""E2E test for Kashier credential configuration via HTTP API."""

import json
import urllib.request

BASE = "http://localhost:8001/api/v1"


def main():
    # Step 1: Login
    print("Step 1: Login...")
    req = urllib.request.Request(
        f"{BASE}/auth/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({
            "email": "yousefmansourss290@gmail.com",
            "password": "Yousef@ceo22434",
        }).encode(),
    )
    res = urllib.request.urlopen(req)
    cookies = res.headers.get_all("Set-Cookie")
    cookie_header = "; ".join([c.split(";")[0] for c in cookies])
    print("  Login OK")

    # Step 2: CSRF
    print("Step 2: Get CSRF token...")
    req = urllib.request.Request(
        f"{BASE}/auth/csrf-token",
        headers={"Cookie": cookie_header},
    )
    res = urllib.request.urlopen(req)
    csrf = json.loads(res.read())["data"]["csrf_token"]
    new_cookies = res.headers.get_all("Set-Cookie")
    if new_cookies:
        cookie_header += "; " + "; ".join([c.split(";")[0] for c in new_cookies])
    print(f"  CSRF: {csrf[:20]}...")

    # Step 3: Configure Kashier credentials
    print("Step 3: Configure Kashier credentials...")
    data = {
        "tenant_id": "7a22dadf-ddaf-4a05-a12f-c8d9799ac4df",
        "service_type": "payment_gateway",
        "service_name": "kashier",
        "credentials": {
            "mid": "MID-44217-177",
            "api_key": "212774e4-c0cd-496b-925b-90b72ebf8595",
            "mode": "test",
        },
    }
    req = urllib.request.Request(
        f"{BASE}/admin/credentials/configure",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie_header,
            "X-CSRF-Token": csrf,
        },
        data=json.dumps(data).encode(),
    )
    try:
        res = urllib.request.urlopen(req)
        result = json.loads(res.read())
        print(f"  SUCCESS: {json.dumps(result, indent=2)}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  FAILED ({e.code}): {body}")
        # Also try to hit the endpoint without CSRF to see if it's a routing issue
        print("\n  Debug: Testing if route exists...")
        try:
            req2 = urllib.request.Request(
                f"{BASE}/admin/credentials/configure",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie_header,
                },
                data=json.dumps(data).encode(),
            )
            urllib.request.urlopen(req2)
        except urllib.error.HTTPError as e2:
            print(f"  Route responds with: {e2.code} - {e2.read().decode()}")


if __name__ == "__main__":
    main()
