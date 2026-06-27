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
  PLACEHOLDER_IMG,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-categories — faithful V3 port of the V2 GildedCategories
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/categories/GildedCategories.tsx).
 *
 * A centered uppercase, wide-tracked eyebrow title over a 2/3-column grid of
 * tall (3:4) category cards. Each card is a `group` Link with an `object-cover`
 * image that hover-zooms to scale-105 (500ms), a `bg-foreground/30` scrim that
 * deepens to `/50` on hover, and a bottom-anchored uppercase label in
 * `text-card`. Cards stagger in (y:30→0, delay i*0.15). All V2 className strings
 * kept verbatim.
 *
 * Data: by default the grid is auto-populated from the store's `useCollections()`
 * (V2 `useProducts().categories` → V3 collections); each collection's
 * `slug`/`id` → `/products?category={id}`, its `image_url` → the card image.
 * Merchants can also curate the grid by hand via repeatable `category` blocks
 * (image + label + link); when any block is present those win.
 *
 * Engine-wired: useResolvedSettings + InlineEditable on the title; image
 * transforms via applyImageTransform; demo/placeholder fallback via useDemo so
 * a real store never inherits the demo photo.
 */

interface CategoryCard {
  key: string;
  href: string;
  label: string;
  imageUrl: string;
  imageStyle: CSSProperties;
}

export default function GildedCategories({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { collections, loading } = useCollections();

  const title = asString(s.title) || localized(locale, "Curated Verticals", "أقسام مختارة بعناية");
  const maxItems = asNumber(s.max_items, 0);

  // Hand-curated blocks win when present; otherwise auto-populate from collections.
  const blocks = readBlocks(instance, "category");
  let cards: CategoryCard[] = [];

  if (blocks.length > 0) {
    cards = blocks
      .map((b, i) => ({
        key: `block-${i}`,
        href: asString(b.link) || "/products",
        label: asString(b.label) || localized(locale, "Category", "فئة"),
        imageUrl: asImageUrl(b.image) || PLACEHOLDER_IMG,
        imageStyle: applyImageTransform(asImageTransform(b.image), "cover"),
      }))
      .filter((c) => c.label || c.imageUrl);
  } else {
    cards = collections.map((cat) => ({
      key: cat.id,
      href: `/products?category=${cat.id}`,
      label: asString(cat.name),
      imageUrl: asImageUrl(cat.image_url) || PLACEHOLDER_IMG,
      imageStyle: {},
    }));
  }

  const displayCategories = maxItems > 0 ? cards.slice(0, maxItems) : cards;

  // Mirror V2: render nothing when there's genuinely no data and we're not loading.
  if (!loading && displayCategories.length === 0) return null;

  return (
    <section className="py-12 md:py-20 lg:py-32" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4">
        <motion.h3
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-[10px] sm:text-xs tracking-[0.2em] sm:tracking-[0.3em] uppercase text-muted-foreground mb-8 md:mb-12 text-center"
        >
          <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
        </motion.h3>

        {loading && displayCategories.length === 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 sm:gap-4 md:gap-6">
            {Array.from({ length: maxItems > 0 ? maxItems : 3 }).map((_, i) => (
              <div key={i} className="aspect-[3/4] bg-muted animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 sm:gap-4 md:gap-6">
            {displayCategories.map((cat, i) => (
              <motion.div
                key={cat.key}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.6, delay: i * 0.15 }}
              >
                <Link
                  to={cat.href}
                  className="group relative aspect-[3/4] overflow-hidden cursor-pointer block"
                >
                  <img
                    src={cat.imageUrl}
                    alt={cat.label}
                    className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                    style={cat.imageStyle}
                    loading="lazy"
                  />
                  <div className="absolute inset-0 bg-foreground/30 transition-opacity duration-300 group-hover:bg-foreground/50" />
                  <div className="absolute inset-0 flex items-end p-4 sm:p-6 md:p-8">
                    <h4 className="text-card text-sm sm:text-lg md:text-xl font-semibold tracking-[0.08em] sm:tracking-[0.15em] uppercase">
                      {cat.label}
                    </h4>
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
