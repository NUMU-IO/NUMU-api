#!/usr/bin/env python
"""Verify Google Search Console credentials before trusting them in production.

Every failure mode here is a console-configuration mistake, not a code bug, and
they all surface as an indistinguishable 403 at runtime. This script separates
them so you know which step to go back and fix.

Usage
-----
    # from NUMU-api/, with GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON set:
    venv/Scripts/python scripts/check_search_console.py

    # or point straight at the downloaded key, no env var needed:
    venv/Scripts/python scripts/check_search_console.py --key ~/Downloads/key.json

    # dry run by default; add --submit to actually register a sitemap:
    venv/Scripts/python scripts/check_search_console.py --submit vionne

Exit code is 0 only when the credential can list properties AND the target
property is present and owned.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

SCOPE = "https://www.googleapis.com/auth/webmasters"
API_ROOT = "https://www.googleapis.com/webmasters/v3"

OK = "[ok]"
FAIL = "[!!]"
INFO = "[--]"


def load_key(key_path: str | None) -> dict | None:
    """Read the service-account key from --key or the env var."""
    if key_path:
        path = Path(key_path).expanduser()
        if not path.is_file():
            print(f"{FAIL} No such file: {path}")
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    raw = os.getenv("GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON", "").strip()
    if not raw:
        print(
            f"{FAIL} GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON is unset and no --key given."
        )
        return None

    if not raw.startswith("{"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"{FAIL} Value is neither JSON nor valid base64: {e}")
            return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"{FAIL} Credential JSON is malformed: {e}")
        return None


def mint_token(info: dict) -> str | None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        print(f"{FAIL} google-auth is not installed in this interpreter.")
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[SCOPE]
        )
        creds.refresh(Request())
        return creds.token
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} Could not mint a token: {e}")
        print(
            "     A key that parses but won't sign usually means the key was "
            "deleted in the console, or the JSON was truncated on copy."
        )
        return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", help="Path to the service-account JSON key")
    parser.add_argument(
        "--site",
        default=os.getenv("GOOGLE_SEARCH_CONSOLE_SITE_URL", "sc-domain:numueg.app"),
        help="Property id (default: sc-domain:numueg.app)",
    )
    parser.add_argument(
        "--submit",
        metavar="SUBDOMAIN",
        help="Actually submit https://<SUBDOMAIN>.numueg.app/sitemap.xml",
    )
    parser.add_argument(
        "--submit-url",
        metavar="URL",
        help="Submit an arbitrary sitemap URL (e.g. the apex https://numueg.app/sitemap.xml)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List sitemaps already registered on the property, with Google's read status",
    )
    args = parser.parse_args()

    info = load_key(args.key)
    if not info:
        return 1
    print(f"{OK} Credential parsed — service account: {info.get('client_email')}")

    token = mint_token(info)
    if not token:
        return 1
    print(f"{OK} Access token minted.")

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_ROOT}/sites", headers=headers)

        if resp.status_code == 403:
            print(f"{FAIL} 403 listing properties. Two likely causes:")
            print("     - The Search Console API is not enabled on the GCP project.")
            print("       APIs & Services -> Library -> 'Google Search Console API'.")
            print("     - The key belongs to a project where it was never enabled.")
            return 1
        if resp.status_code != 200:
            print(f"{FAIL} {resp.status_code} listing properties: {resp.text[:300]}")
            return 1

        entries = resp.json().get("siteEntry", []) or []
        if not entries:
            print(f"{FAIL} Authenticated, but this account can see NO properties.")
            print(
                f"     Add {info.get('client_email')} in Search Console -> Settings"
                " -> Users and permissions, with the Owner role."
            )
            return 1

        print(f"{OK} Visible properties ({len(entries)}):")
        for e in entries:
            print(f"     {e.get('permissionLevel'):<16} {e.get('siteUrl')}")

        match = next((e for e in entries if e.get("siteUrl") == args.site), None)
        if not match:
            print(f"\n{FAIL} Target property '{args.site}' is not in that list.")
            if any(str(e.get("siteUrl", "")).startswith("http") for e in entries):
                print(
                    "     You have a URL-prefix property but not a Domain property."
                    "\n     A URL-prefix property does NOT cover subdomains, so"
                    "\n     per-store sitemaps will be rejected. Add property ->"
                    "\n     Domain -> numueg.app, verify via Cloudflare DNS TXT."
                )
            return 1

        level = match.get("permissionLevel")
        if level != "siteOwner":
            print(f"\n{FAIL} Permission on {args.site} is '{level}', not 'siteOwner'.")
            print("     Only Owners may submit sitemaps. Re-add with the Owner role.")
            return 1
        print(f"\n{OK} Owner on {args.site} — sitemap submission is authorised.")

        if args.list:
            listing = await client.get(
                f"{API_ROOT}/sites/{quote(args.site, safe='')}/sitemaps",
                headers=headers,
            )
            entries = (
                listing.json().get("sitemap", []) if listing.status_code == 200 else []
            )
            print(f"\n{INFO} Sitemaps registered on {args.site}: {len(entries)}")
            for s in entries:
                # `contents` stays absent until Google has actually fetched and
                # parsed the file — an entry with no counts is submitted but unread,
                # which is a different (and usually transient) state from 0 URLs.
                counts = {
                    c.get("type"): c.get("submitted") for c in s.get("contents", [])
                }
                print(f"     {s.get('path')}")
                print(
                    f"       downloaded={s.get('lastDownloaded', 'not yet')} "
                    f"errors={s.get('errors', 0)} warnings={s.get('warnings', 0)} "
                    f"urls={counts or 'not read yet'}"
                )

        if not args.submit and not args.submit_url:
            if not args.list:
                print(
                    f"\n{INFO} Dry run. Re-run with --submit <subdomain>,"
                    " --submit-url <url>, or --list."
                )
            return 0

        sitemap = args.submit_url or f"https://{args.submit}.numueg.app/sitemap.xml"
        endpoint = (
            f"{API_ROOT}/sites/{quote(args.site, safe='')}"
            f"/sitemaps/{quote(sitemap, safe='')}"
        )
        put = await client.put(endpoint, headers=headers)
        if put.status_code // 100 == 2:
            print(f"{OK} Submitted {sitemap} ({put.status_code}).")
            print(
                f"{INFO} Google reports it under Sitemaps within minutes; indexing"
                " itself takes days."
            )
            return 0

        print(f"{FAIL} {put.status_code} submitting {sitemap}: {put.text[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
