"""`audit_store_seo` read tool — what is missing, ranked by what it costs.

"Improve my SEO" is not answerable until something has read the store. This
returns the store's real SEO/GEO/AEO state out of `settings["seo"]` as a list
of findings, each naming the key `update_store_settings` writes to fix it, so
the model proposes a specific change instead of generic advice.

The three families are separated because they are answered by different
engines and a merchant should know which one they are buying:

* SEO — classic index and result page: title, description, share image.
* AEO — being the quoted answer: a short answer, and FAQs as FAQPage JSON-LD.
* GEO — being citable by generative engines: AI crawler access, llms.txt, and
  the entity signals (profiles, contact, area served) that let an engine
  decide two mentions are the same business.

Severity is `high` only where the gap loses traffic the store would otherwise
have had, so an agent reading this top-down fixes the expensive things first.
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_PERMISSION = "settings.view"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _finding(
    area: str, severity: str, key: str, title: str, why: str
) -> dict[str, str]:
    return {
        "area": area,
        "severity": severity,
        # The exact key to pass to update_store_settings.
        "fix_with_key": key,
        "issue": title,
        "why": why,
    }


def _audit(seo: dict, store_name: str | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    # ── SEO ───────────────────────────────────────────────────────────────
    if not seo.get("seo_title"):
        out.append(
            _finding(
                "seo",
                "high",
                "seo_title",
                "No SEO title",
                "Google shows the raw store name; a title naming what the "
                "store sells is what a searcher clicks.",
            )
        )
    if not seo.get("seo_description"):
        out.append(
            _finding(
                "seo",
                "high",
                "seo_description",
                "No meta description",
                "Without one the search result quotes whatever text is at the "
                "top of the page, which is usually navigation.",
            )
        )
    if not seo.get("social_image_url"):
        out.append(
            _finding(
                "seo",
                "medium",
                "social_image_url",
                "No social share image",
                "Links shared to WhatsApp and Instagram render as a bare URL, "
                "which is the difference between a tap and a scroll past.",
            )
        )
    if seo.get("robots_indexing_enabled") is False:
        out.append(
            _finding(
                "seo",
                "high",
                "robots_indexing_enabled",
                "Search engines are blocked",
                "robots.txt serves Disallow: / and pages carry noindex — the "
                "store cannot appear in Google at all. Deliberate before "
                "launch; costly after it.",
            )
        )
    if not seo.get("google_site_verification"):
        out.append(
            _finding(
                "seo",
                "low",
                "google_site_verification",
                "Search Console not verified",
                "Without it nobody sees which queries the store already ranks "
                "for, so SEO work stays guesswork.",
            )
        )

    # ── AEO ───────────────────────────────────────────────────────────────
    if not seo.get("short_answer"):
        out.append(
            _finding(
                "aeo",
                "high",
                "short_answer",
                "Nothing quotable about the store",
                "Answer engines quote a passage rather than summarise a page. "
                "A store with no straight answer to 'what does this shop "
                "sell' gets skipped for one that wrote it down.",
            )
        )
    if not seo.get("faqs"):
        out.append(
            _finding(
                "aeo",
                "high",
                "faqs",
                "No FAQs",
                "FAQs are emitted as FAQPage JSON-LD — the only structured "
                "place to answer 'do you deliver to Aswan' in the words a "
                "shopper actually asks it.",
            )
        )

    # ── GEO ───────────────────────────────────────────────────────────────
    if seo.get("ai_crawlers_allowed") is False:
        out.append(
            _finding(
                "geo",
                "medium",
                "ai_crawlers_allowed",
                "AI assistants are blocked from the store",
                "GPTBot, ClaudeBot, PerplexityBot and Google-Extended cannot "
                "read the catalogue, so an assistant asked to recommend a "
                "shop cannot cite this one. A real trade-off, not an "
                "oversight — but worth confirming it was chosen.",
            )
        )
    if not seo.get("same_as"):
        out.append(
            _finding(
                "geo",
                "medium",
                "same_as",
                "No official profiles linked",
                "sameAs is how an engine decides an Instagram page and this "
                "store are the same business rather than two strangers.",
            )
        )
    if not (seo.get("contact_email") or seo.get("contact_phone")):
        out.append(
            _finding(
                "geo",
                "medium",
                "contact_email",
                "No public contact",
                "A shop with no reachable contact reads as unverified to both "
                "search and assistants.",
            )
        )
    if not seo.get("area_served"):
        out.append(
            _finding(
                "geo",
                "low",
                "area_served",
                "No delivery area declared",
                "Answers to 'shops that deliver to X' need the store to have "
                "said where it delivers.",
            )
        )
    if not seo.get("business_type"):
        out.append(
            _finding(
                "geo",
                "low",
                "business_type",
                "Generic business type",
                "The JSON-LD says 'Organization'. A real subtype (ClothingStore, "
                "BookStore …) is what puts the store in the right category.",
            )
        )
    if not store_name:
        out.append(
            _finding(
                "geo",
                "medium",
                "store_name",
                "No storefront name set",
                "The name is the entity. Without it there is nothing for an "
                "engine to attach reviews, profiles or mentions to.",
            )
        )
    return out


async def audit_store_seo(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    from src.api.v1.schemas.tenant.store_seo import normalize_store_seo
    from src.infrastructure.repositories.store_repository import StoreRepository

    store = await StoreRepository(ctx.session).get_by_id(ctx.store_id)
    if store is None:
        return ToolResult.invalid_args("Store not found.")

    settings = store.settings or {}
    seo = normalize_store_seo(settings)
    identity = (settings.get("customization") or {}).get("identity") or {}
    store_name = identity.get("store_name") or getattr(store, "name", None)

    findings = _audit(seo, store_name)
    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: rank.get(f["severity"], 3))

    data = {
        "findings": findings,
        "counts": {
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
            "low": sum(1 for f in findings if f["severity"] == "low"),
        },
        "current": {
            "seo_title": seo.get("seo_title"),
            "seo_description": seo.get("seo_description"),
            "indexing_enabled": seo.get("robots_indexing_enabled"),
            "ai_crawlers_allowed": seo.get("ai_crawlers_allowed"),
            "faq_count": len(seo.get("faqs") or []),
            "profiles_linked": len(seo.get("same_as") or []),
        },
    }
    logger.info(
        "agent_seo_audit",
        store_id=str(ctx.store_id),
        findings=len(findings),
        high=data["counts"]["high"],
    )
    return ToolResult(
        ok=True, data=data, source=[{"type": "store", "id": str(ctx.store_id)}]
    )


SPEC = {
    "name": "audit_store_seo",
    "description": (
        "Check the store's SEO, AEO (being the quoted answer) and GEO (being "
        "citable by AI assistants) and return what is missing, worst first. "
        "Each finding names the exact key to pass to update_store_settings, so "
        "follow it with concrete proposed fixes rather than generic advice. "
        "Use for 'improve my SEO', 'why am I not on Google', 'how do I show up "
        "in ChatGPT'. Read-only — it changes nothing."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": audit_store_seo,
}
