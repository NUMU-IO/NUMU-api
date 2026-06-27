"use client";

import { Link, Money, useLocale, useProducts, useResolvedSettings } from "@numueg/theme-sdk";
import type { CSSProperties } from "react";
import { ArrowLeft } from "lucide-react";
import {
  asArray,
  asNumber,
  asString,
  localized,
  readBlocks,
  asImageUrl,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-featured-collection — faithful V3 port of the V2 LuxFeaturedCollection
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/featured-collection/
 * LuxFeaturedCollection.tsx). All V2 className strings kept VERBATIM:
 *   - header row: `flex items-center justify-between mb-8` with a 10px/0.3em
 *     uppercase title eyebrow on the left, an optional muted subtitle hidden on
 *     mobile, and a "View All" link underlined with `border-b border-foreground`
 *     and a trailing `ArrowLeft size={10}`.
 *   - product grid: `--cols-mobile` (2) / `--cols-desktop` (columns) responsive
 *     grid, `gap-4`. Each card is the inlined V2 LuxProductCard look: a
 *     `aspect-[3/4] bg-[hsl(var(--lux-gray))]` portrait frame, an `object-cover`
 *     primary image (`group-hover:scale-[1.03]` 700ms) with a
 *     `lux-product-image-secondary` swap on hover, the V2 sentence-case product
 *     name (`text-sm text-foreground line-clamp-1 mb-1.5 group-hover:opacity-50`),
 *     and a `<Money>` price row with a struck-through `compare_at_price`.
 *
 * Engine-wired on the V3 SDK: useResolvedSettings (global tokens + dynamic
 * sources resolve) and InlineEditable on every text field. useProducts() returns
 * the SSR-prefetched catalog. Manual `product_ids` (block list) are honoured;
 * `collection_tag` (new/bestseller) is informational only — tag buckets aren't
 * exposed on the listing route, so we fall back to the full catalog (matching
 * the V2 "fall back to all products if filtered is empty"). Renders null when
 * there are no products (no demo data on a real store).
 */
export default function LuxFeaturedCollection({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { products } = useProducts();

  // Default heading follows `collection_tag` so two featured-collection sections
  // on the same page (e.g. New Arrivals + Best Sellers) don't render an
  // identical title. A merchant-entered `title` always wins.
  const collectionTag = asString(s.collection_tag);
  const defaultTitle =
    collectionTag === "bestseller"
      ? localized(locale, "Best Sellers", "الأكثر مبيعاً")
      : localized(locale, "New Arrivals", "وصل حديثاً");
  const title = asString(s.title) || defaultTitle;
  const subtitle = asString(s.subtitle);
  const viewAllLink = asString(s.view_all_link) || "/products";
  const viewAllText = asString(s.view_all_text) || localized(locale, "View All", "عرض الكل");
  const productCount = asNumber(s.product_count, 4);
  const columns = asNumber(s.columns, 4);

  // Manual product ids may be a flat array setting or `product` blocks.
  const blockIds = readBlocks(instance, "product")
    .map((b) => asString(b.product_id) || asProductId(b.product))
    .filter((x) => x.length > 0);
  const settingIds = asArray<unknown>(s.product_ids).filter(
    (x): x is string => typeof x === "string" && x.length > 0,
  );
  const manualIds = blockIds.length > 0 ? blockIds : settingIds;

  // Tag buckets aren't available on the listing route — fall back to the full
  // catalog (matching the V2 "fall back to all products if filtered is empty").
  const collectionProducts =
    manualIds.length > 0
      ? manualIds
          .map((id) => products.find((p) => p.id === id || p.slug === id))
          .filter((p): p is NonNullable<typeof p> => !!p)
      : products;

  const displayProducts = collectionProducts.slice(0, productCount);

  // Hide entire section if no products (don't show empty state for new stores).
  if (displayProducts.length === 0) return null;

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-desktop": columns,
  } as CSSProperties;

  const gridClassName =
    "grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-10" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        <div className="flex items-center justify-between mb-8">
          <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </p>
          {subtitle && (
            <p className="text-xs text-muted-foreground hidden md:block">
              <InlineEditable
                sectionId={sectionId}
                settingKey="subtitle"
                value={subtitle}
                multiline
              />
            </p>
          )}
          {viewAllLink && (
            <Link
              to={viewAllLink}
              className="text-[10px] uppercase tracking-[0.2em] text-foreground flex items-center gap-1 hover:opacity-50 transition-opacity border-b border-foreground pb-0.5"
            >
              <InlineEditable sectionId={sectionId} settingKey="view_all_text" value={viewAllText} />{" "}
              <ArrowLeft size={10} aria-hidden="true" className="rtl:-scale-x-100" />
            </Link>
          )}
        </div>

        <div className={gridClassName} style={cssVars}>
          {displayProducts.map((product) => (
            <Link
              key={product.id}
              to={`/product/${product.slug || product.id}`}
              className="lux-product-card group block"
              data-testid="storefront-product-card"
            >
              <div className="relative aspect-[3/4] overflow-hidden bg-[hsl(var(--lux-gray))] mb-3">
                {asImageUrl(product.images?.[0]) ? (
                  <img
                    src={asImageUrl(product.images?.[0])}
                    alt={product.name}
                    className="lux-product-image absolute inset-0 w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-700 ease-out"
                    loading="lazy"
                  />
                ) : (
                  <div className="absolute inset-0 lux-shimmer" />
                )}
                {asImageUrl(product.images?.[1]) && (
                  <img
                    src={asImageUrl(product.images?.[1])}
                    alt=""
                    className="lux-product-image-secondary"
                    loading="lazy"
                  />
                )}
              </div>
              <p className="text-sm text-foreground line-clamp-1 mb-1.5 group-hover:opacity-50 transition-opacity">
                {product.name}
              </p>
              <div className="flex items-baseline gap-2">
                <span className="text-sm text-foreground">
                  <Money
                    amount={product.variants?.[0]?.price ?? product.price ?? 0}
                    currency={product.currency}
                  />
                </span>
                {typeof product.compare_at_price === "number" &&
                  product.compare_at_price >
                    (product.variants?.[0]?.price ?? product.price ?? 0) && (
                    <span className="text-[11px] text-muted-foreground line-through">
                      <Money amount={product.compare_at_price} currency={product.currency} />
                    </span>
                  )}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

/** A `product` setting may be a bare id string or a `{ id }`/`{ handle }` object. */
function asProductId(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const r = v as Record<string, unknown>;
    if (typeof r.id === "string") return r.id;
    if (typeof r.handle === "string") return r.handle;
  }
  return "";
}
