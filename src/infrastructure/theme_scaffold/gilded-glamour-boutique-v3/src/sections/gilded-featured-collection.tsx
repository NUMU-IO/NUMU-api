"use client";

import { useProducts, useLocale, useResolvedSettings, Link } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import type { CSSProperties } from "react";
import {
  asString,
  asNumber,
  asArray,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";
import { GildedProductCard } from "./_product-card";

/**
 * gilded-featured-collection — faithful V3 port of the V2 GildedFeaturedCollection
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/featured-collection/
 * GildedFeaturedCollection.tsx). All V2 className strings kept VERBATIM:
 *   - `<section className="bg-card py-12 md:py-20 lg:py-32">` warm-white band.
 *   - Header (`mb-8 md:mb-16`, fade+rise on view): optional eyebrow subtitle
 *     (`text-[10px] sm:text-xs tracking-[0.2em] sm:tracking-[0.3em] uppercase
 *     text-muted-foreground mb-3 md:mb-4`) + uppercase wide-tracked bold title
 *     (`text-xl sm:text-2xl md:text-4xl lg:text-5xl … tracking-[0.04em]
 *     sm:tracking-[0.08em] font-bold`).
 *   - Responsive `--cols-mobile`(2)/`--cols-desktop`(columns) grid with
 *     `gap-3 sm:gap-4 md:gap-6`, each cell a fade+rise `group` wrapper around
 *     the SHARED <GildedProductCard/> (NOT inlined — shared card owns the V2
 *     card look: 3:4 image, hover zoom, NEW/discount badges, quick-add).
 *   - "View All" gold-outline button (`border border-[var(--gilded-gold)] …
 *     hover:bg-[var(--gilded-gold)]`).
 *
 * Engine-wired on the V3 SDK: useResolvedSettings (global tokens + dynamic
 * sources + draft preview) and InlineEditable on every section-level text
 * field. useProducts() returns the SSR-prefetched catalog. Manual product
 * selection (the `product_ids` product_list setting, or `product` blocks) takes
 * precedence; otherwise the catalog is best-effort filtered by `collection_tag`
 * (new/bestseller/featured/sale) so the two home instances render DISTINCT
 * product sets. If a tag yields zero matches we fall back to the full catalog
 * (matching the V2 "fall back to all products if filtered is empty").
 * Renders null when there are no products (no demo data on a real store).
 */
export default function GildedFeaturedCollection({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { products } = useProducts();

  const heading =
    asString(s.title) ||
    localized(locale, "Crafted for the Eternal Wardrobe", "صُمّم ليبقى في خزانتك للأبد");
  const subheading =
    asString(s.subtitle) || localized(locale, "The Artisan Way", "أسلوب الحرفيين");
  const viewAllText =
    asString(s.view_all_text) || localized(locale, "View All", "عرض الكل");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const productCount = asNumber(s.product_count, 5);
  const columns = asNumber(s.columns, 5);

  // Manual product selection: a flat `product_ids` array (product_list setting)
  // or `product` blocks. Each entry may be a bare id/slug string or an object.
  const blockIds = readBlocks(instance, "product")
    .map((b) => asProductId(b.product_id ?? b.product))
    .filter((x) => x.length > 0);
  const settingIds = asArray<unknown>(s.product_ids)
    .map((x) => asProductId(x))
    .filter((x) => x.length > 0);
  const manualIds = blockIds.length > 0 ? blockIds : settingIds;

  // Manual selection takes precedence; otherwise best-effort filter the catalog
  // by collection_tag so the two home instances (new vs bestseller) render
  // DISTINCT product sets. If a tag yields zero matches, fall back to the full
  // catalog (matching the V2 "fall back to all products if filtered is empty").
  const collectionTag = asString(s.collection_tag) || "new";
  const collectionProducts =
    manualIds.length > 0
      ? manualIds
          .map((id) => products.find((p) => p.id === id || p.slug === id))
          .filter((p): p is NonNullable<typeof p> => !!p)
      : filterByTag(products, collectionTag);

  const displayProducts = collectionProducts.slice(0, productCount);

  // Hide entire section if no products (don't show empty state on a new store).
  if (displayProducts.length === 0) return null;

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-desktop": columns,
  } as CSSProperties;

  const gridClassName =
    "grid gap-3 sm:gap-4 md:gap-6 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="bg-card py-12 md:py-20 lg:py-32" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mb-8 md:mb-16"
        >
          {subheading && (
            <p className="text-[10px] sm:text-xs tracking-[0.2em] sm:tracking-[0.3em] uppercase text-muted-foreground mb-3 md:mb-4">
              <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subheading} />
            </p>
          )}
          <h2 className="text-xl sm:text-2xl md:text-4xl lg:text-5xl text-foreground uppercase tracking-[0.04em] sm:tracking-[0.08em] font-bold">
            <InlineEditable sectionId={sectionId} settingKey="title" value={heading} />
          </h2>
        </motion.div>

        <div className={gridClassName} style={cssVars}>
          {displayProducts.map((product) => (
            <GildedProductCard key={product.id} product={product} />
          ))}
        </div>

        {/* View all link */}
        <div className="text-center mt-8 md:mt-12">
          <Link
            to={viewAllLink}
            className="inline-block px-6 sm:px-8 py-2.5 sm:py-3 border border-[var(--gilded-gold)] text-foreground text-xs sm:text-sm tracking-[0.1em] sm:tracking-[0.15em] uppercase font-medium hover:bg-[var(--gilded-gold)] hover:text-foreground transition-all duration-300"
          >
            <InlineEditable sectionId={sectionId} settingKey="view_all_text" value={viewAllText} />
          </Link>
        </div>
      </div>
    </section>
  );
}

/** Optional tag/flag fields the catalog may surface (defensive — not on the
 *  base SDK Product type). */
type TaggedProduct = {
  is_new?: boolean;
  isNew?: boolean;
  is_bestseller?: boolean;
  isBestseller?: boolean;
  is_featured?: boolean;
  isFeatured?: boolean;
  tags?: unknown;
  compare_at_price?: number | null;
  price?: number;
};

function hasTag(tags: unknown, tag: string): boolean {
  return Array.isArray(tags) && tags.some((t) => String(t).toLowerCase() === tag);
}

/** Best-effort filter the catalog by collection_tag. Falls back to the full
 *  list when the tag yields zero matches (never renders empty). */
function filterByTag<T extends { id: string }>(products: T[], tag: string): T[] {
  const match = (p: T): boolean => {
    const x = p as T & TaggedProduct;
    switch (tag) {
      case "new":
        return !!(x.is_new ?? x.isNew);
      case "bestseller":
        return !!(x.is_bestseller ?? x.isBestseller ?? hasTag(x.tags, "bestseller"));
      case "sale":
        return typeof x.compare_at_price === "number" &&
          typeof x.price === "number" &&
          x.compare_at_price > x.price;
      case "featured":
        return !!(x.is_featured ?? x.isFeatured ?? hasTag(x.tags, "featured"));
      default:
        return true;
    }
  };
  const filtered = products.filter(match);
  return filtered.length > 0 ? filtered : products;
}

/** A `product`/`product_ids` entry may be a bare id/slug string or a
 *  `{ id }`/`{ handle }`/`{ slug }` object. */
function asProductId(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const r = v as Record<string, unknown>;
    if (typeof r.id === "string") return r.id;
    if (typeof r.handle === "string") return r.handle;
    if (typeof r.slug === "string") return r.slug;
  }
  return "";
}
