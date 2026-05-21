"""Scrape product data from public Instagram/Facebook post URLs.

Uses Instagram's /embed/captioned/ endpoint which returns server-rendered HTML
with real CDN image URLs and caption text — no API keys or OAuth required.

Supported URL formats:
  - https://www.instagram.com/p/ABC123/
  - https://www.instagram.com/reel/ABC123/
  - https://www.facebook.com/pagename/posts/123456
  - https://www.facebook.com/photo?fbid=123456
"""

import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

# Price extraction patterns (EGP, LE, جنيه)
# Require currency keyword directly adjacent and price >= 2 digits to avoid matching dates
_PRICE_PATTERNS = [
    re.compile(r"(\d{2}[\d,]*)\s*(?:EGP|egp|LE|le|جنيه|ج\.م)", re.UNICODE),
    re.compile(r"(?:EGP|egp|LE|le)\s*(\d{2}[\d,]*)", re.UNICODE),
    re.compile(r"(?:Price|السعر|سعر)[:\s]*(\d{2}[\d,]*)", re.IGNORECASE | re.UNICODE),
]

# Patterns that look like prices but are actually dates (e.g. "30/3", "15/4/2026")
_DATE_PATTERN = re.compile(r"(\d{1,2})\s*/\s*\d{1,2}")


@dataclass
class ScrapedPost:
    """Data scraped from a social media post URL."""

    url: str
    platform: str  # "instagram" or "facebook"
    image_url: str | None = None  # Primary image (browser-accessible preview)
    image_urls: list[str] | None = (
        None  # All CDN images for server-side download (carousel)
    )
    caption: str | None = None
    author: str | None = None
    suggested_name: str | None = None
    suggested_price: int | None = None
    error: str | None = None


def detect_platform(url: str) -> str | None:
    """Detect platform from URL."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    return None


def _extract_shortcode(url: str) -> str | None:
    """Extract Instagram shortcode from URL."""
    match = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def _extract_price(text: str) -> int | None:
    """Try to extract a price from text.

    Filters out false positives like dates (30/3) by checking if the
    matched number is immediately followed by a slash+digit.
    """
    # Collect all date-like numbers to exclude (e.g. "30" from "30/3")
    date_numbers: set[str] = set()
    for m in _DATE_PATTERN.finditer(text):
        date_numbers.add(m.group(1))

    for pattern in _PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1).replace(",", "")
            # Skip if this number appears as part of a date
            if raw in date_numbers:
                continue
            try:
                return int(raw)
            except ValueError:
                continue
    return None


def _clean_name(caption: str, author: str | None = None) -> str:
    """Extract a clean product name from caption text."""
    # Take first line / first sentence
    first_line = caption.split("\n")[0].strip()

    # Strip author handle from the beginning (e.g. "adidasarabia It's Predator...")
    # The caption often starts with the username (without @)
    # Author from embed may differ from caption prefix (e.g. "adidasarabiaandadidasuae" vs "adidasarabia")
    if author:
        for prefix in [author, author.replace("and", " & "), author.split("and")[0]]:
            prefix = prefix.strip()
            if prefix and first_line.lower().startswith(prefix.lower()):
                first_line = first_line[len(prefix) :].strip()
                break
    # Also strip any leading @username pattern
    first_line = (
        re.sub(r"^@?[a-zA-Z0-9_.]+\s+", "", first_line, count=1)
        if re.match(r"^@?[a-zA-Z0-9_.]+\s", first_line)
        and len(first_line.split()[0]) > 3
        else first_line
    )

    if ". " in first_line:
        first_line = first_line.split(". ")[0]

    # Remove emojis
    cleaned = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        r"\U0001F900-\U0001F9FF\U00002702-\U000027B0\U0001FA00-\U0001FA6F"
        r"\U00002600-\U000026FF\U0000FE00-\U0000FE0F\U0000200D]+",
        "",
        first_line,
    )
    # Remove common IG filler
    for filler in [
        "DM to order",
        "Link in bio",
        "Available now",
        "NEW",
        "اطلب الان",
        "لينك في البايو",
        "متوفر الان",
    ]:
        cleaned = re.sub(re.escape(filler), "", cleaned, flags=re.IGNORECASE)

    cleaned = cleaned.strip(" -—|•!.,\"'#@")
    if len(cleaned) > 100:
        cleaned = cleaned[:97] + "..."
    return cleaned or "Imported Product"


def _extract_resolution(url: str) -> int:
    """Extract pixel size from a CDN URL's stp parameter for sorting.

    E.g. stp=dst-jpg_e35_p1080x1080 → 1080
    """
    m = re.search(r"_p(\d+)x(\d+)", url)
    if m:
        return int(m.group(1))
    # Fallback: s240x240 format
    m = re.search(r"_s(\d+)x(\d+)", url)
    if m:
        return int(m.group(1))
    return 0


def _extract_image_id(url: str) -> str | None:
    """Extract the unique image filename from a CDN URL.

    E.g. .../657163294_18083591612378754_2245192462733759832_n.jpg?... → 657163294_...n.jpg
    """
    m = re.search(r"/(\d+_\d+_\d+_n\.(?:jpg|webp|png))", url)
    return m.group(1) if m else None


def _parse_embed_html(html: str) -> tuple[list[str], str | None, str | None, int]:
    """Parse Instagram embed HTML for image URLs, caption, and author.

    Returns (image_urls, caption, author, total_slides).
    Only returns the actual post image(s), filtering out profile pictures
    and unrelated thumbnails. Without auth, Instagram only exposes the
    cover image of carousel posts.
    """
    # --- Images ---
    # Post images use t51.82787-15, profile pics use t51.82787-19.
    # Only match post images (t51.82787-15).
    #
    # Instagram serves CDN images from one of three host shapes,
    # depending on the time / region / which experiment they're
    # running:
    #   * scontent.cdninstagram.com                  (current default)
    #   * scontent-<airport>.cdninstagram.com        (regional variant)
    #   * instagram.<airport>.fbcdn.net              (legacy)
    # The legacy form was the only one the original regex matched, so
    # any post served from the newer hosts came back with zero images
    # and the route reported "Could not extract image".
    raw_urls = re.findall(
        r"(https://"
        r"(?:scontent[a-z0-9.-]*\.cdninstagram\.com"
        r"|scontent[a-z0-9.-]*\.fbcdn\.net"
        r"|instagram\.[a-z0-9.-]+\.fbcdn\.net)"
        r'/v/t51\.82787-15/[^"<>\s]+)',
        html,
    )
    decoded = [unescape(u) for u in raw_urls]

    # Group by unique image filename, keep the highest resolution variant
    best_per_image: dict[str, tuple[int, str]] = {}
    for url in decoded:
        img_id = _extract_image_id(url) or url
        res = _extract_resolution(url)
        if img_id not in best_per_image or res > best_per_image[img_id][0]:
            best_per_image[img_id] = (res, url)

    # Only keep images >= 640px (real post images, not thumbnails)
    image_urls = [url for res, url in best_per_image.values() if res >= 640]

    # --- Caption ---
    caption = None
    cap_match = re.search(r'class="Caption"[^>]*>(.*?)</div>', html, re.DOTALL)
    if cap_match:
        caption_html = cap_match.group(1)
        caption = re.sub(r"<[^>]+>", " ", caption_html)
        caption = re.sub(r"\s+", " ", caption).strip()
        caption = re.sub(r"\s*View all \d+ comments?\s*$", "", caption).strip()

    # --- Author ---
    author = None
    author_match = re.search(
        r'class="[^"]*HeaderText[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL
    )
    if not author_match:
        author_match = re.search(
            r'class="[^"]*Username[^"]*"[^>]*>(.*?)</', html, re.DOTALL
        )
    if author_match:
        author = re.sub(r"<[^>]+>", "", author_match.group(1)).strip()

    # --- Total slides hint ---
    # The embed references edge_sidecar_to_children for carousels
    sidecar_refs = len(re.findall(r"edge_sidecar_to_children", html))
    # If sidecar refs exist, it's a carousel; actual count unknown from embed alone
    total_slides = len(image_urls) if not sidecar_refs else max(len(image_urls), 3)

    return image_urls, caption, author, total_slides


async def _scrape_instagram(url: str) -> ScrapedPost:
    """Scrape an Instagram post using the /embed/captioned/ endpoint.

    This endpoint returns server-rendered HTML with real CDN image URLs
    and caption text, bypassing Instagram's client-side rendering wall.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return ScrapedPost(
            url=url,
            platform="instagram",
            error="Could not extract post shortcode from URL",
        )

    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"

    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(embed_url)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        return ScrapedPost(
            url=url,
            platform="instagram",
            error=f"HTTP {e.response.status_code} — post may be private or deleted",
        )
    except Exception as e:
        return ScrapedPost(url=url, platform="instagram", error=str(e))

    image_urls, caption, author, _total_slides = _parse_embed_html(html)

    if not image_urls:
        return ScrapedPost(
            url=url,
            platform="instagram",
            error="Could not extract image — post may be private or a video-only post",
        )

    # Browser-accessible preview URL (redirects to CDN with valid hash)
    preview_url = f"https://www.instagram.com/p/{shortcode}/media/?size=l"

    suggested_name = _clean_name(caption, author) if caption else "Imported Product"
    suggested_price = _extract_price(caption) if caption else None

    return ScrapedPost(
        url=url,
        platform="instagram",
        image_url=preview_url,
        image_urls=image_urls,
        caption=caption,
        author=author,
        suggested_name=suggested_name,
        suggested_price=suggested_price,
    )


async def _scrape_facebook(url: str) -> ScrapedPost:
    """Scrape a Facebook post using OG meta tags.

    Facebook still serves OG tags in initial HTML for public posts/pages.
    """
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={
                **_HEADERS,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return ScrapedPost(url=url, platform="facebook", error=str(e))

    # Facebook still serves OG tags for public pages
    def _og(tag: str) -> str | None:
        for pattern in [
            rf'<meta\s+property="{tag}"\s+content="([^"]+)"',
            rf'<meta\s+content="([^"]+)"\s+property="{tag}"',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                return unescape(m.group(1))
        return None

    image_url = _og("og:image")
    title = _og("og:title")
    description = _og("og:description")

    if not image_url:
        return ScrapedPost(
            url=url,
            platform="facebook",
            error="Could not extract image — post may be private",
        )

    caption = description or title
    suggested_name = _clean_name(caption) if caption else "Imported Product"
    suggested_price = _extract_price(caption) if caption else None

    return ScrapedPost(
        url=url,
        platform="facebook",
        image_url=image_url,
        caption=caption,
        suggested_name=suggested_name,
        suggested_price=suggested_price,
    )


async def scrape_post(url: str) -> ScrapedPost:
    """Scrape product data from a public Instagram or Facebook post URL."""
    platform = detect_platform(url)
    if platform == "instagram":
        return await _scrape_instagram(url)
    elif platform == "facebook":
        return await _scrape_facebook(url)
    else:
        return ScrapedPost(
            url=url,
            platform="unknown",
            error="URL must be from instagram.com or facebook.com",
        )


async def scrape_posts(urls: list[str]) -> list[ScrapedPost]:
    """Scrape multiple post URLs concurrently."""
    import asyncio

    tasks = [scrape_post(url) for url in urls]
    return await asyncio.gather(*tasks)
