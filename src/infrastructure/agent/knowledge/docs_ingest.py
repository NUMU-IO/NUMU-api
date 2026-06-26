"""Ingest the NUMU developer docs (docs.numueg.app) into Layer A.

docs.numueg.app is a VitePress static site (verified): the page inventory is
embedded in the home page as `__VP_SITE_DATA__.themeConfig.sidebar`. We enumerate
those links, fetch each clean URL, strip the HTML to text, chunk on headings, and
map each page's top-level section to a Knowledge Area. These are *developer* docs
(theme/SDK/CLI/API), so they SUPPLEMENT the authored merchant how-to corpus.

Returns a list of `KnowledgeDoc` (source_kind=docs) ready to embed + upsert.
"""

from __future__ import annotations

import json
import re

import httpx

from src.config import settings as app_settings
from src.config.logging_config import get_logger
from src.core.agent.knowledge import ArticleStatus, KnowledgeDoc, SourceKind

logger = get_logger(__name__)

# VitePress sidebar section path → NUMU Knowledge Area. Dev-doc topics map to the
# closest merchant-facing area (themes/storefront); the rest default to 'themes'.
_SECTION_TO_AREA = {
    "/getting-started/": "themes",
    "/theme-engine/": "themes",
    "/sdk/": "themes",
    "/cli-plugin/": "themes",
    "/storefront/": "storefront",
    "/api/": "storefront",
    "/workflows/": "themes",
    "/reference/": "themes",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _extract_site_data(html: str) -> dict | None:
    m = re.search(
        r"window\.__VP_SITE_DATA__\s*=\s*JSON\.parse\(\"(.*?)\"\);", html, re.DOTALL
    )
    if not m:
        return None
    raw = m.group(1)
    # The payload is a JS string literal containing escaped JSON. Decode escapes.
    try:
        decoded = json.loads(f'"{raw}"')
        return json.loads(decoded)
    except Exception:  # noqa: BLE001
        return None


def _enumerate_links(site_data: dict) -> list[str]:
    sidebar = (site_data.get("themeConfig") or {}).get("sidebar") or {}
    links: list[str] = []
    groups = sidebar.values() if isinstance(sidebar, dict) else sidebar
    for group_list in groups:
        for group in group_list if isinstance(group_list, list) else [group_list]:
            for item in group.get("items", []) if isinstance(group, dict) else []:
                link = item.get("link")
                if link:
                    links.append(link)
    return sorted(set(links))


def _area_for(link: str) -> str:
    for prefix, area in _SECTION_TO_AREA.items():
        if link.startswith(prefix):
            return area
    return "themes"


def _html_to_text(html: str) -> str:
    body = re.search(r"<main[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    fragment = body.group(1) if body else html
    fragment = _SCRIPT_RE.sub(" ", fragment)
    text = _TAG_RE.sub(" ", fragment)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _chunk(text: str, *, max_chars: int = 1200) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 > max_chars and buf:
            chunks.append(buf.strip())
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text[:max_chars]]


async def fetch_docs_corpus(base_url: str | None = None) -> list[KnowledgeDoc]:
    base = (base_url or app_settings.agent_docs_ingest_base_url).rstrip("/")
    docs: list[KnowledgeDoc] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        home = await client.get(f"{base}/")
        home.raise_for_status()
        site_data = _extract_site_data(home.text)
        if not site_data:
            logger.warning("docs_ingest_no_site_data", base=base)
            return []
        links = _enumerate_links(site_data)
        logger.info("docs_ingest_enumerated", base=base, pages=len(links))

        for link in links:
            try:
                resp = await client.get(f"{base}{link}")
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("docs_ingest_page_failed", link=link, error=str(exc))
                continue
            text = _html_to_text(resp.text)
            if not text:
                continue
            title_m = re.search(
                r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL
            )
            title = (title_m.group(1).strip() if title_m else link).replace(
                " | NUMU Developer Docs", ""
            )
            docs.append(
                KnowledgeDoc(
                    source=f"docs.numueg.app{link}",
                    title=title,
                    area=_area_for(link),
                    locale="en",
                    chunks=_chunk(text),
                    section="Developer Docs",
                    source_kind=SourceKind.DOCS,
                    status=ArticleStatus.PUBLISHED,
                )
            )
    return docs
