"""SEO synthetic monitor — Phase 6.

Hits a known list of merchant subdomains and asserts the SEO contract
from Phases 1 + 2:

- 200 status on every checked path.
- Non-empty `<title>` and `<meta name="description">`.
- `<link rel="canonical">` on indexable routes (everything but home,
  which the test relaxes since canonical-on-home is optional polish).
- No `<meta name="robots">` with `noindex` on indexable routes
  (catches a regression where Phase 1's robots logic flips for the
  wrong store).
- robots.txt 200 + has Sitemap: directive on active stores.
- sitemap.xml 200 + valid sitemap-or-urlset XML.

Run from cron or GitHub Actions. Exits 1 on any failure so the
scheduler can alert.

Two ways to feed the subdomain list:

1. ``--subdomains foo,bar,baz`` — explicit list. Used by the GitHub
   Actions schedule so the workflow doesn't need DATABASE_URL.
2. No flag → pull `WHERE status='active'` from the database. Used
   when running on the droplet beside the API container; the
   AsyncSessionLocal connection already has DB creds.

Usage:
    # Subdomain list, no DB access (CI-friendly):
    python scripts/seo_synthetic.py --subdomains sawsaw,bon-younes,yarab

    # DB-driven (active stores only):
    python scripts/seo_synthetic.py

    # Common variants:
    python scripts/seo_synthetic.py --max-stores 5  # cap during dev
    python scripts/seo_synthetic.py --host-template "{sub}.test.numueg.app"
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Iterable

import httpx

# Paths that must exist + pass the indexable-route checks. Keep this list
# in sync with the static-sitemap paths in
# `numu-egyptian-bazaar/src/lib/seo-server.ts`.
INDEXABLE_PATHS: list[str] = [
    "/",
    "/products",
    "/about",
    "/contact",
    "/faq",
]


_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
_DESC_RE = re.compile(
    r'<meta\s+[^>]*name="description"[^>]*content="([^"]+)"',
    re.IGNORECASE,
)
_CANONICAL_RE = re.compile(r'<link\s+[^>]*rel="canonical"', re.IGNORECASE)
_NOINDEX_RE = re.compile(
    r'<meta\s+[^>]*name="robots"[^>]*content="[^"]*noindex',
    re.IGNORECASE,
)


async def _check_url(client: httpx.AsyncClient, host: str, path: str) -> list[str]:
    """Return a list of human-readable error strings (empty = pass)."""
    url = f"https://{host}{path}"
    errors: list[str] = []
    try:
        r = await client.get(url, follow_redirects=True, timeout=10.0)
    except httpx.HTTPError as exc:
        return [f"{path}: connection error: {exc}"]
    if r.status_code != 200:
        errors.append(f"{path}: status {r.status_code}")
        return errors
    html = r.text
    if not _TITLE_RE.search(html):
        errors.append(f"{path}: missing <title>")
    if not _DESC_RE.search(html):
        errors.append(f"{path}: missing description")
    if path != "/" and not _CANONICAL_RE.search(html):
        errors.append(f"{path}: missing canonical")
    if _NOINDEX_RE.search(html):
        errors.append(f"{path}: NOINDEX present (regression?)")
    return errors


async def _check_robots_and_sitemap(client: httpx.AsyncClient, host: str) -> list[str]:
    errors: list[str] = []
    try:
        r = await client.get(f"https://{host}/robots.txt", timeout=10.0)
    except httpx.HTTPError as exc:
        errors.append(f"robots.txt: connection error: {exc}")
        return errors
    if r.status_code != 200:
        errors.append(f"robots.txt: status {r.status_code}")
    elif "Sitemap:" not in r.text:
        errors.append("robots.txt: missing Sitemap directive")

    try:
        r2 = await client.get(f"https://{host}/sitemap.xml", timeout=15.0)
    except httpx.HTTPError as exc:
        errors.append(f"sitemap.xml: connection error: {exc}")
        return errors
    if r2.status_code != 200:
        errors.append(f"sitemap.xml: status {r2.status_code}")
    elif "<sitemapindex" not in r2.text and "<urlset" not in r2.text:
        errors.append("sitemap.xml: malformed XML")
    return errors


async def _active_subdomains() -> list[str]:
    """Active stores only — suspended/inactive stores are intentionally
    excluded since they should be serving Disallow: / per Phase 2.

    DB-backed; import is deferred so the CI path (subdomain list passed
    via --subdomains) doesn't need DATABASE_URL or the sqlalchemy stack.
    """
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoreModel.subdomain).where(StoreModel.status == "active")
        )
        return [row[0] for row in result.all() if row[0]]


def _format_failures(failures: dict[str, list[str]]) -> str:
    lines = [f"SEO synthetic failures: {len(failures)} merchants"]
    for host, errs in sorted(failures.items()):
        lines.append(f"  - {host}:")
        for e in errs:
            lines.append(f"      • {e}")
    return "\n".join(lines)


async def _run(
    host_template: str,
    max_stores: int | None,
    explicit_subdomains: list[str] | None,
) -> int:
    if explicit_subdomains is not None:
        subdomains: Iterable[str] = explicit_subdomains
    else:
        subdomains = await _active_subdomains()
    if max_stores is not None:
        subdomains = list(subdomains)[:max_stores]

    failures: dict[str, list[str]] = {}
    async with httpx.AsyncClient() as client:
        for sub in subdomains:
            host = host_template.format(sub=sub)
            errs: list[str] = []
            for path in INDEXABLE_PATHS:
                errs.extend(await _check_url(client, host, path))
            errs.extend(await _check_robots_and_sitemap(client, host))
            if errs:
                failures[host] = errs

    if failures:
        print(_format_failures(failures))
        return 1
    print(f"SEO synthetic OK ({len(list(subdomains))} merchants checked)")
    return 0


def _parse_subdomains(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    items = [s.strip() for s in raw.split(",")]
    return [s for s in items if s]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-template",
        default="{sub}.numueg.app",
        help='Host pattern with `{sub}` placeholder, e.g. "{sub}.numueg.app"',
    )
    parser.add_argument(
        "--max-stores",
        type=int,
        default=None,
        help="Cap merchants checked (useful for dev runs).",
    )
    parser.add_argument(
        "--subdomains",
        default=None,
        help=(
            "Comma-separated explicit list of subdomains to check. When set, "
            "the script skips the DATABASE_URL lookup — required for the "
            "GitHub Actions schedule (which doesn't have DB access)."
        ),
    )
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            _run(
                args.host_template,
                args.max_stores,
                _parse_subdomains(args.subdomains),
            )
        )
    )


if __name__ == "__main__":
    main()
