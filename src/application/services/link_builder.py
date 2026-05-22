"""Build storefront URLs with optional UTM tagging.

Single source of truth for storefront URLs constructed server-side.
Used by the trackable-link endpoint today, and by any future code path
that needs to serialize a storefront URL (campaign send / transactional
emails / QR exports).

Canonical-origin rule mirrors the storefront's TypeScript
``canonicalOriginFor`` (see numu-egyptian-bazaar/src/lib/seo-server.ts):

    custom_domain (when set + non-empty) wins
    subdomain.numueg.app otherwise
    slug.numueg.app as last-resort fallback

The matching ``Store.store_url`` property on the domain entity already
encodes this rule — LinkBuilder leans on it rather than re-implementing.

UTM building rule for campaigns:

    utm_campaign = <kebab-slug>-<short_code>

The slug is human-readable (so merchants can eyeball the link). The
short_code is the stable identifier (so renames don't break attribution).
The pair is the canonical wire format the resolver looks for —
``campaign_resolver._extract_short_code`` matches against the same shape.

When a campaign carries no UTM at all (rare — only when the link
builder is asked to produce a generic storefront URL), no UTM
parameters are appended; the URL stays clean.
"""

from __future__ import annotations

from urllib.parse import urlencode

from slugify import slugify

from src.core.entities.marketing_campaign import (
    CampaignChannel,
    MarketingCampaign,
)
from src.core.entities.product import Product
from src.core.entities.store import Store

# Maps the ``source`` value the merchant picks in the hub to a default
# ``medium`` we stamp into the URL when the caller didn't override.
# Mirrors the table in contracts/merchant-campaign-api.md.
_SOURCE_DEFAULT_MEDIUM: dict[str, str] = {
    "facebook": "social",
    "instagram": "social",
    "whatsapp": "messaging",
    "email": "email",
    "tiktok": "social",
    "sms": "sms",
    "qr": "qr",
    # "other" deliberately has no default — caller can leave medium blank.
}

# When a campaign carries no explicit source but does carry a channel,
# derive ``utm_source`` from the channel so transactional sends don't
# show up as direct traffic.
_CHANNEL_DEFAULT_SOURCE: dict[CampaignChannel, str] = {
    CampaignChannel.EMAIL: "email",
    CampaignChannel.SMS: "sms",
}


class LinkBuilder:
    """Build storefront URLs for one store, optionally UTM-tagged."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.origin = store.store_url.rstrip("/")

    # ── URL builders ────────────────────────────────────────────

    def storefront_url(
        self,
        *,
        campaign: MarketingCampaign | None = None,
        source: str | None = None,
        medium: str | None = None,
        term: str | None = None,
        content: str | None = None,
    ) -> str:
        """Homepage URL with optional UTM tagging."""
        return self._compose(
            path="/",
            campaign=campaign,
            source=source,
            medium=medium,
            term=term,
            content=content,
        )

    def collection_url(
        self,
        *,
        collection_slug: str,
        campaign: MarketingCampaign | None = None,
        source: str | None = None,
        medium: str | None = None,
        term: str | None = None,
        content: str | None = None,
    ) -> str:
        """Collection page URL with optional UTM tagging.

        Storefront renders collections at ``/collections?category=<slug>``
        (see seo-server.ts categoriesSitemapEntries) — we match that.
        """
        if not collection_slug:
            raise ValueError("collection_slug is required")
        # Path stays clean; collection is a query param.
        return self._compose(
            path="/collections",
            extra_query={"category": collection_slug.strip()},
            campaign=campaign,
            source=source,
            medium=medium,
            term=term,
            content=content,
        )

    def product_url(
        self,
        *,
        product: Product,
        campaign: MarketingCampaign | None = None,
        source: str | None = None,
        medium: str | None = None,
        term: str | None = None,
        content: str | None = None,
    ) -> str:
        """Product detail page URL with optional UTM tagging.

        Mirrors the storefront route ``/product/{slug-or-id}``. Slug is
        preferred when set so links remain readable; falls back to the
        product ID for slug-less or pre-slug products.
        """
        slug_or_id = (product.slug or "").strip() or str(product.id)
        return self._compose(
            path=f"/product/{slug_or_id}",
            campaign=campaign,
            source=source,
            medium=medium,
            term=term,
            content=content,
        )

    def custom_url(
        self,
        *,
        path: str,
        campaign: MarketingCampaign | None = None,
        source: str | None = None,
        medium: str | None = None,
        term: str | None = None,
        content: str | None = None,
    ) -> str:
        """Arbitrary storefront path with optional UTM tagging.

        Caller is responsible for having validated the path via
        validate-path first; this method does no validation, only URL
        composition. Path must start with ``/``.
        """
        if not path.startswith("/"):
            raise ValueError(f"custom_url path must start with /: {path!r}")
        return self._compose(
            path=path,
            campaign=campaign,
            source=source,
            medium=medium,
            term=term,
            content=content,
        )

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def utm_campaign_for(campaign: MarketingCampaign) -> str:
        """Canonical ``utm_campaign`` string for a campaign.

        Shape: ``<slug>-<short_code>``. The short_code is the stable
        identifier; the slug exists for human readability. The matching
        ``campaign_resolver._extract_short_code`` only cares about the
        trailing 6-char block, so renames (which would change the slug)
        don't break attribution as long as the short_code is preserved
        in the URL — which is exactly what the link builder guarantees.
        """
        slug = LinkBuilder.slug_from_campaign_name(campaign.name)
        return f"{slug}-{campaign.short_code}"

    @staticmethod
    def slug_from_campaign_name(name: str) -> str:
        """Kebab-case, lowercase, ASCII-only slug.

        Non-ASCII names (e.g., Arabic campaign names like "تخفيضات
        العيد") slugify down to an empty string or noise; in that case
        we fall back to a generic ``campaign`` slug. The short_code
        still gives uniqueness — the slug is purely for human read.
        """
        slug = slugify(name, max_length=40, lowercase=True, separator="-")
        if not slug:
            return "campaign"
        return slug

    # ── Internals ───────────────────────────────────────────────

    def _compose(
        self,
        *,
        path: str,
        extra_query: dict[str, str] | None = None,
        campaign: MarketingCampaign | None,
        source: str | None,
        medium: str | None,
        term: str | None,
        content: str | None,
    ) -> str:
        query: dict[str, str] = dict(extra_query or {})

        resolved_source = source
        resolved_medium = medium

        if campaign is not None:
            query["utm_campaign"] = self.utm_campaign_for(campaign)
            if resolved_source is None:
                resolved_source = _CHANNEL_DEFAULT_SOURCE.get(campaign.channel)
        if resolved_source:
            query["utm_source"] = resolved_source
            if resolved_medium is None:
                resolved_medium = _SOURCE_DEFAULT_MEDIUM.get(resolved_source)
        if resolved_medium:
            query["utm_medium"] = resolved_medium
        if term:
            query["utm_term"] = term
        if content:
            query["utm_content"] = content

        url = f"{self.origin}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url
