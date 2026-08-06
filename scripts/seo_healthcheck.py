#!/usr/bin/env python
"""Daily SEO regression check for numueg.app and every merchant storefront.

This does NOT improve rankings — nothing scheduled can. Resubmitting sitemaps on
a timer is a no-op at best. What it does is catch the two things that silently
destroy rankings you already earned, and measure whether the brand work is
landing.

It exists because of a real, expensive miss: between the Vercel migration and
2026-08-06 every URL on numueg.app served the homepage's
``<link rel="canonical">``. Google read all 15 pages as duplicates of the
homepage and indexed one. The site looked perfect to a human the whole time.
Three months, 14 pages. This check would have failed on day one.

What it verifies
----------------
Per URL in each sitemap (the apex, plus every live storefront):
  * resolves 200 without a redirect hop
  * ``rel=canonical`` points at itself, not somewhere else
  * no ``noindex``
  * has a non-empty ``<title>`` that is not a duplicate of another page's

Per property, via the Search Console API (optional, needs credentials):
  * sitemaps still parse — errors/warnings counts
  * position and impressions for the brand queries you care about, so "are we
    winning our own name yet" is a number instead of a feeling

Usage
-----
    python scripts/seo_healthcheck.py                    # apex + all storefronts
    python scripts/seo_healthcheck.py --sample 25        # cap URLs per sitemap
    python scripts/seo_healthcheck.py --skip-stores      # apex only, fast
    python scripts/seo_healthcheck.py --brands numu,vionne,vionneegy

Exit code is non-zero when any check fails, so CI turns red.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote, urlparse

import httpx

# Titles are Arabic and URLs can carry unicode slugs. Windows consoles default to
# cp1252, so printing a finding would raise UnicodeEncodeError and take the whole
# check down — a monitor that dies while reporting a problem is worse than none.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APEX = "https://numueg.app"
DIRECTORY_ENDPOINT = f"{APEX}/api/v1/public/stores"
UA = "NUMU-SEO-Healthcheck/1.0 (+https://numueg.app)"

CANONICAL_RE = re.compile(
    r"<link[^>]*rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)[\"']", re.I
)
# Also matches the reversed attribute order, which hand-written head tags hit
# often enough to be worth a second pattern rather than a fragile mega-regex.
CANONICAL_ALT_RE = re.compile(
    r"<link[^>]*href=[\"']([^\"']+)[\"'][^>]*rel=[\"']canonical[\"']", re.I
)
ROBOTS_RE = re.compile(
    r"<meta[^>]*name=[\"']robots[\"'][^>]*content=[\"']([^\"']*)[\"']", re.I
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _first(*matches: re.Match | None) -> str | None:
    for m in matches:
        if m:
            return m.group(1).strip()
    return None


def _norm(url: str) -> str:
    """Compare canonicals ignoring only a trailing slash difference."""
    return url.rstrip("/") or url


async def fetch_sitemap_urls(client: httpx.AsyncClient, sitemap: str) -> list[str]:
    r = await client.get(sitemap)
    r.raise_for_status()
    return LOC_RE.findall(r.text)


async def check_url(
    client: httpx.AsyncClient, url: str, report: Report, titles: dict[str, list[str]]
) -> None:
    try:
        # follow_redirects=False on purpose: a sitemap entry that redirects is
        # itself the defect ("Page with redirect" in Search Console), so we must
        # see the 3xx rather than silently follow it to a 200.
        r = await client.get(url, follow_redirects=False)
    except httpx.HTTPError as e:
        report.fail(f"{url} — request failed: {type(e).__name__}")
        return

    report.checked += 1

    if r.status_code != 200:
        loc = r.headers.get("location", "")
        report.fail(
            f"{url} — HTTP {r.status_code}"
            + (f" -> {loc}" if loc else "")
            + " (in sitemap, so it must be a live 200)"
        )
        return

    html = r.text

    robots = _first(ROBOTS_RE.search(html)) or ""
    if "noindex" in robots.lower():
        report.fail(
            f"{url} — meta robots '{robots}' blocks indexing, but it is in the sitemap"
        )

    canonical = _first(CANONICAL_RE.search(html), CANONICAL_ALT_RE.search(html))
    if not canonical:
        report.warn(f"{url} — no rel=canonical")
    elif _norm(canonical) != _norm(url):
        report.fail(
            f"{url} — canonical points to {canonical} (self-canonical expected)"
        )

    title = _first(TITLE_RE.search(html))
    if not title:
        report.fail(f"{url} — empty or missing <title>")
    else:
        titles[title].append(url)


async def check_sitemap(
    client: httpx.AsyncClient, sitemap: str, report: Report, sample: int | None
) -> None:
    label = urlparse(sitemap).netloc
    try:
        urls = await fetch_sitemap_urls(client, sitemap)
    except Exception as e:  # noqa: BLE001
        report.fail(f"{sitemap} — unreadable: {e}")
        return

    if not urls:
        report.fail(f"{sitemap} — parsed but contains 0 <loc> entries")
        return

    total = len(urls)
    if sample and total > sample:
        # Head + tail: newest and oldest entries fail differently (fresh pages
        # 404 on a bad deploy, old ones rot), and a contiguous slice would miss
        # one of those classes entirely.
        half = sample // 2
        urls = urls[:half] + urls[-half:]

    titles: dict[str, list[str]] = defaultdict(list)
    sem = asyncio.Semaphore(8)

    async def guarded(u: str) -> None:
        async with sem:
            await check_url(client, u, report, titles)

    await asyncio.gather(*(guarded(u) for u in urls))

    for title, owners in titles.items():
        if len(owners) > 1:
            report.fail(
                f"{label} — {len(owners)} pages share the title {title!r}: "
                + ", ".join(owners[:4])
                + (" …" if len(owners) > 4 else "")
            )

    print(f"  {label}: {len(urls)}/{total} URLs checked")


async def search_console_section(client: httpx.AsyncClient, brands: list[str]) -> None:
    """Sitemap health + brand-query position. Skipped silently without creds."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from src.infrastructure.external_services import google_search_console as gsc
    except Exception as e:  # noqa: BLE001
        print(f"\n[--] Search Console section skipped (import failed: {e})")
        return

    if not gsc.is_configured():
        print("\n[--] Search Console section skipped (no credentials configured)")
        return

    token = await asyncio.to_thread(gsc._fetch_access_token_sync)
    if not token:
        print("\n[!!] Search Console credentials present but no token could be minted")
        return

    headers = {"Authorization": f"Bearer {token}"}
    site = quote(gsc.SITE_URL, safe="")

    r = await client.get(
        f"{gsc.API_ROOT}/sites/{site}/sitemaps", headers=headers, follow_redirects=True
    )
    if r.status_code == 200:
        entries = r.json().get("sitemap", []) or []
        print(f"\n=== Search Console sitemaps ({len(entries)}) ===")
        for s in entries:
            errors, warnings = int(s.get("errors", 0)), int(s.get("warnings", 0))
            flag = "[!!]" if errors else ("[ ~]" if warnings else "[ok]")
            counts = {c.get("type"): c.get("submitted") for c in s.get("contents", [])}
            print(
                f"  {flag} {s.get('path')} — errors={errors} warnings={warnings} "
                f"urls={counts or 'not read yet'}"
            )

    # Search Console data lags ~2-3 days; asking for yesterday returns nothing
    # and reads as "we lost all our rankings", so the window starts further back.
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=28)
    r = await client.post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query",
        headers=headers,
        json={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query"],
            "rowLimit": 25000,
        },
        follow_redirects=True,
    )
    if r.status_code != 200:
        print(f"\n[!!] searchAnalytics failed: {r.status_code} {r.text[:200]}")
        return

    rows = r.json().get("rows", []) or []
    print(f"\n=== Brand queries, {start} -> {end} ===")
    if not rows:
        print("  (no data yet — expected until pages have been indexed a while)")
        return

    for brand in brands:
        hits = [r_ for r_ in rows if brand.lower() in r_["keys"][0].lower()]
        if not hits:
            print(f"  {brand:<12} no impressions yet")
            continue
        hits.sort(key=lambda x: -x["impressions"])
        for h in hits[:3]:
            print(
                f"  {brand:<12} {h['keys'][0]!r} — pos {h['position']:.1f}, "
                f"{int(h['impressions'])} impr, {int(h['clicks'])} clicks"
            )


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sample", type=int, default=40, help="max URLs per sitemap (0 = all)"
    )
    p.add_argument("--skip-stores", action="store_true")
    p.add_argument("--brands", default="numu,vionne")
    args = p.parse_args()

    report = Report()
    sample = args.sample or None

    async with httpx.AsyncClient(
        timeout=25.0, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:
        print("=== Sitemap URL health ===")
        await check_sitemap(client, f"{APEX}/sitemap.xml", report, sample)

        if not args.skip_stores:
            try:
                r = await client.get(DIRECTORY_ENDPOINT)
                stores = (
                    r.json().get("data", {}).get("stores", [])
                    if r.status_code == 200
                    else []
                )
            except Exception:  # noqa: BLE001
                stores = []

            if not stores:
                report.warn(
                    f"{DIRECTORY_ENDPOINT} returned no stores — storefronts went unchecked"
                )
            for s in stores:
                await check_sitemap(client, f"{s['url']}/sitemap.xml", report, sample)

        await search_console_section(
            client, [b.strip() for b in args.brands.split(",") if b.strip()]
        )

    print(f"\n=== Result — {report.checked} URLs checked ===")
    for w in report.warnings:
        print(f"  [ ~] {w}")
    for f in report.failures:
        print(f"  [!!] {f}")

    if report.failures:
        print(f"\nFAILED — {len(report.failures)} problem(s).")
        return 1
    print("\nPASS — no indexing regressions detected.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
