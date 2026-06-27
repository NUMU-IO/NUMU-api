"use client";

import { Link, useCollections, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import type { CSSProperties } from "react";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asNumber,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-categories — faithful V3 port of the V2 LuxCategories
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/categories/LuxCategories.tsx).
 *
 * Centered 10px/0.3em eyebrow title over a square category grid. Each card is a
 * `group` Link: a 1:1 `bg-[hsl(var(--lux-gray))]` frame with an `object-cover`
 * image that hover-zooms to scale-[1.03] over 700ms, and an uppercase
 * wide-tracked label that fades muted → foreground on hover. Cards animate in
 * staggered (y:20→0, delay i*0.1). All V2 className strings kept verbatim.
 *
 * Data: by default the grid is auto-populated from the store's `useCollections()`
 * (V2 `useProducts().categories` → V3 collections; each collection's `slug`/`id`
 * → `/collections/{slug}`, its `image_url` → the card image). Merchants can also
 * curate the grid by hand via repeatable `category` blocks (image + label +
 * link); when any block is present those win over the auto collections list.
 * Engine-wired: useResolvedSettings + InlineEditable on the title.
 */

interface CategoryCard {
  key: string;
  href: string;
  label: string;
  imageUrl: string;
  imageStyle: CSSProperties;
}

export default function LuxCategories({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { collections } = useCollections();

  const title = asString(s.title) || localized(locale, "Shop by Category", "تسوق حسب الفئة");
  const columnsDesktop = asNumber(s.columns_desktop, 5);
  const columnsMobile = asNumber(s.columns_mobile, 2);
  const maxItems = asNumber(s.max_items, 0);

  // Hand-curated cards win when present; otherwise auto-populate from collections.
  const blocks = readBlocks(instance, "category");
  let cards: CategoryCard[] = [];

  if (blocks.length > 0) {
    cards = blocks
      .map((b, i) => {
        const link = asString(b.link);
        const imageUrl = asImageUrl(b.image_url);
        return {
          key: `block-${i}`,
          href: link || "/collections",
          label: asString(b.label) || localized(locale, "Category", "فئة"),
          imageUrl,
          imageStyle: applyImageTransform(asImageTransform(b.image_url), "cover"),
        } satisfies CategoryCard;
      })
      .filter((c) => c.label || c.imageUrl);
  } else {
    cards = collections.map((cat) => ({
      key: cat.id,
      href: `/collections/${cat.slug || cat.id}`,
      label: asString(cat.name),
      imageUrl: asImageUrl(cat.image_url),
      imageStyle: {},
    }));
  }

  const displayCards = maxItems > 0 ? cards.slice(0, maxItems) : cards;

  const cssVars = {
    "--cols-mobile": columnsMobile,
    "--cols-desktop": columnsDesktop,
  } as CSSProperties;

  const gridClassName =
    "grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-14" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        {title && (
          <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground text-center mb-8">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </p>
        )}

        {displayCards.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">
            {localized(locale, "No categories yet", "لا توجد فئات بعد")}
          </p>
        ) : (
          <div className={gridClassName} style={cssVars}>
            {displayCards.map((card, i) => (
              <motion.div
                key={card.key}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
              >
                <Link to={card.href} className="group block text-center">
                  <div className="aspect-square overflow-hidden bg-[hsl(var(--lux-gray))] mb-3">
                    {card.imageUrl && (
                      <img
                        src={card.imageUrl}
                        alt={card.label}
                        className="w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-700"
                        style={card.imageStyle}
                        loading="lazy"
                      />
                    )}
                  </div>
                  <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground group-hover:text-foreground transition-colors">
                    {card.label}
                  </p>
                </Link>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
