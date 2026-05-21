# Contract — `link_builder` Service (Backend Module)

Internal Python module. Not an HTTP contract — but a contract between the trackable-link endpoint, campaign-send code paths (email/WhatsApp/SMS), and any future place the backend needs to produce a storefront URL with attribution baked in.

**File**: `src/application/services/link_builder.py`

---

## Public surface

```python
class LinkBuilder:
    """Build storefront URLs with optional attribution parameters baked in."""

    def __init__(self, store: Store) -> None:
        """Store is required so the builder can resolve canonical origin."""
        self.store = store
        self.origin = self._resolve_origin(store)

    # ── URL builders ──────────────────────────────────────────────

    def storefront_url(
        self,
        *,
        campaign: MarketingCampaign | None = None,
        source: str | None = None,
        medium: str | None = None,
        term: str | None = None,
        content: str | None = None,
    ) -> str:
        """Return the homepage URL with optional UTM tagging."""

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
        """Return a collection page URL with optional UTM tagging."""

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
        """Return a product detail page URL with optional UTM tagging.

        Uses product.slug if available, falls back to product.id.
        """

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
        """Return an arbitrary storefront path with optional UTM tagging.

        Caller is responsible for having validated the path via validate-path
        first; this method does no validation, only URL composition.
        """

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def utm_campaign_for(campaign: MarketingCampaign) -> str:
        """Return the canonical utm_campaign string for a campaign:
        `<kebab-slug>-<short_code>`. Stable across renames."""

    @staticmethod
    def slug_from_campaign_name(name: str) -> str:
        """Kebab-case, lowercase, ASCII-only slug. Non-ASCII (e.g. Arabic) names
        get transliterated to a short hash prefix; the short_code remains the
        stable identifier."""

    @staticmethod
    def _resolve_origin(store: Store) -> str:
        """Mirror canonicalOriginFor logic from seo-server.ts.
        custom_domain wins → https://{custom_domain}
        else subdomain    → https://{subdomain}.numueg.app  (env-adjusted)
        else apex          → https://numueg.app
        """
```

---

## UTM construction rules

When `campaign` is passed:

- `utm_campaign` = `LinkBuilder.utm_campaign_for(campaign)` (slug + short_code)

When `source` is omitted but `campaign` is passed:

- `utm_source` defaults based on `campaign.channel`:
  - `email` → `email`
  - `sms` → `sms`
  - `whatsapp` → `whatsapp`

When `medium` is omitted but `source` is provided:

- Default from the source-preset table in `merchant-campaign-api.md`.

When the campaign is omitted entirely (e.g., used for storefront URL in a generic notification):

- No UTM params are appended; the URL stays clean.

---

## Sanitization

All UTM values passed in are URL-component-encoded via Python's `urllib.parse.quote_plus`. No additional sanitization here — sanitization is the responsibility of whatever produced the value (the trackable-link endpoint sanitizes merchant input before calling LinkBuilder).

---

## Test surface (unit tests)

| Test | Expected output                                                                                |
| ---- | ---------------------------------------------------------------------------------------------- |
| `storefront_url()` on a subdomain store              | `https://acme.numueg.app/`                                  |
| `storefront_url()` on a custom-domain store          | `https://shop.acme.com/`                                    |
| `product_url(product=p)` on subdomain                | `https://acme.numueg.app/product/{p.slug or p.id}`          |
| `product_url(product=p, campaign=c, source="facebook")` | `...?utm_source=facebook&utm_medium=social&utm_campaign={slug}-{short_code}` |
| `utm_campaign_for(campaign with name="Eid Sale 2026", short_code="AB7K")` | `eid-sale-2026-AB7K`                |
| `utm_campaign_for(campaign with Arabic name)`        | `<hash>-AB7K` (transliterated)                              |
| `custom_url(path="/lookbook/eid")` on subdomain      | `https://acme.numueg.app/lookbook/eid`                      |
| URL params are properly encoded (spaces → `+`, `&` → `%26`) | per Python's `urlencode(..., quote_via=quote_plus)`  |

---

## Used by

- `POST /stores/{store_id}/campaigns/{campaign_id}/trackable-link` (this feature)
- `MarketingCampaignSendService` — when sending an email/WhatsApp/SMS campaign, the body's product/storefront/collection links should be replaced with tagged versions. (Out of v1 scope to retrofit — the link builder is available for it but the send service can opt into using it incrementally.)
- Future: order-confirmation emails, abandoned-checkout emails, anywhere a storefront link is serialized server-side.
