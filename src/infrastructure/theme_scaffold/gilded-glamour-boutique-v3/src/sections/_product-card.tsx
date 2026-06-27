"use client";

import { useState } from "react";
import { Link, Money, useCart, useLocale, type Product } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { Star, Plus, Check } from "lucide-react";
import { asImageUrl, localized, productHref } from "./_shared";

/**
 * GildedProductCard — shared, faithful V3 port of the V2 GildedProductCard
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedProductCard.tsx).
 *
 * Sharp-edged 3:4 image, hover zoom (group-hover:scale-105 / 500ms), NEW badge
 * top-start (bg-primary), discount badge top-end (bg-foreground "{n}% Off"),
 * desktop hover-reveal + always-on mobile Quick-Add, fly-up "+1 Added" gold chip,
 * uppercase name, gold price (struck compare-at on sale). Engine-wired: SDK
 * Link / Money / useCart; the brand gold reads `--gilded-gold` (global-wired) via
 * arbitrary utilities so the merchant's Accent picker repaints it. Default colour
 * is byte-identical to V2.
 *
 * Used by featured-collection, products-page, search-results, related products.
 */

type ProductExtras = Product & {
  is_new?: boolean;
  isNew?: boolean;
  rating?: number;
};

export function GildedProductCard({
  product,
  showPrice = true,
  showRating = true,
  newBadgeText,
}: {
  product: Product;
  showPrice?: boolean;
  showRating?: boolean;
  newBadgeText?: string;
}) {
  const { addItem } = useCart();
  const locale = useLocale();
  const [added, setAdded] = useState(false);

  const p = product as ProductExtras;
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const discountPercent = hasDiscount
    ? Math.round(((compareAt - price) / compareAt) * 100)
    : 0;
  const primary = asImageUrl(product.images?.[0]);
  const isNew = p.is_new ?? p.isNew ?? false;
  const rating = typeof p.rating === "number" ? p.rating : undefined;

  // More than one variant ⇒ the shopper must choose → let the click bubble to
  // the wrapping <Link> and navigate to the PDP (V2 `requiresSelection`).
  const requiresSelection = (product.variants?.length ?? 0) > 1;

  const handleQuickAdd = (e: React.MouseEvent) => {
    if (requiresSelection) return; // bubble → Link → PDP
    e.preventDefault();
    e.stopPropagation();
    void addItem(product.id, product.variants?.[0]?.id, 1);
    setAdded(true);
    setTimeout(() => setAdded(false), 2000);
  };

  const newLabel = newBadgeText || localized(locale, "New Arrival", "وصل حديثاً");
  const quickAdd = localized(locale, "Quick Add", "أضف بسرعة");
  const addToCart = localized(locale, "Add to Cart", "أضف للسلة");
  const addedDesktop = localized(locale, "Added to Cart", "تمت الإضافة");
  const addedMobile = localized(locale, "Added", "تمت");

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      className="gld-product-card group"
    >
      <Link to={productHref(product.slug || product.id)} className="block">
        <div className="relative aspect-[3/4] overflow-hidden mb-4 bg-muted">
          {primary ? (
            <img
              src={primary}
              alt={product.name}
              className="gld-product-image w-full h-full object-cover"
              loading="lazy"
              width={600}
              height={800}
            />
          ) : (
            <div className="absolute inset-0 gld-shimmer" />
          )}

          {isNew && (
            <span className="absolute top-2 start-2 md:top-4 md:start-4 bg-primary text-primary-foreground px-2 py-1 md:px-3 md:py-1.5 text-[9px] md:text-[10px] font-semibold tracking-[0.1em] md:tracking-[0.15em] uppercase">
              {newLabel}
            </span>
          )}
          {hasDiscount && (
            <span className="absolute top-2 end-2 md:top-4 md:end-4 bg-foreground text-card px-2 py-1 md:px-3 md:py-1.5 text-[9px] md:text-[10px] font-semibold tracking-[0.1em] md:tracking-[0.15em] uppercase">
              {discountPercent}% {localized(locale, "Off", "خصم")}
            </span>
          )}

          {/* Quick Add — desktop hover reveal */}
          <motion.button
            type="button"
            onClick={handleQuickAdd}
            whileTap={{ scale: 0.96 }}
            animate={added ? { scale: [1, 1.05, 1] } : { scale: 1 }}
            transition={{ duration: 0.35 }}
            className={`absolute bottom-4 start-4 end-4 py-3 border border-[var(--gilded-gold)] text-xs font-semibold tracking-[0.15em] uppercase opacity-0 translate-y-2 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-300 hidden md:block ${
              added
                ? "bg-[var(--gilded-gold)] text-card"
                : "bg-card/95 text-[var(--gilded-gold)] hover:bg-[var(--gilded-gold)] hover:text-card"
            }`}
          >
            <span className="flex items-center justify-center gap-2">
              {added ? <Check size={14} /> : <Plus size={14} />}
              {added ? addedDesktop : quickAdd}
            </span>
          </motion.button>

          {/* Quick Add — mobile, always visible */}
          <motion.button
            type="button"
            onClick={handleQuickAdd}
            whileTap={{ scale: 0.96 }}
            animate={added ? { scale: [1, 1.05, 1] } : { scale: 1 }}
            transition={{ duration: 0.35 }}
            className={`absolute bottom-4 start-4 end-4 py-3 border border-[var(--gilded-gold)] text-xs font-semibold tracking-[0.15em] uppercase md:hidden flex items-center justify-center gap-2 ${
              added
                ? "bg-[var(--gilded-gold)] text-card"
                : "bg-card/95 text-[var(--gilded-gold)] hover:bg-[var(--gilded-gold)] hover:text-card"
            }`}
          >
            {added ? <Check size={14} /> : <Plus size={14} />}
            {added ? addedMobile : addToCart}
          </motion.button>

          {/* Fly-up "+1 Added" chip — NOT wrapped in AnimatePresence (it lives
              inside the <Link>; a nav click would race the exit animation). */}
          {added && (
            <motion.span
              initial={{ opacity: 0, y: 0, scale: 0.7 }}
              animate={{ opacity: 1, y: -20, scale: 1 }}
              transition={{ duration: 0.6 }}
              className="pointer-events-none absolute top-4 end-4 px-2.5 py-1 bg-[var(--gilded-gold)] text-card text-[10px] font-semibold tracking-[0.15em] uppercase shadow-md"
            >
              +1 {localized(locale, "Added", "تمت")}
            </motion.span>
          )}
        </div>
      </Link>

      <Link to={productHref(product.slug || product.id)} className="block">
        <h4 className="text-sm font-medium tracking-[0.05em] text-foreground line-clamp-1">
          {product.name}
        </h4>
      </Link>
      {showRating && rating !== undefined && (
        <div className="flex items-center gap-1 mt-1">
          <Star size={11} className="fill-[var(--gilded-gold)] text-[var(--gilded-gold)]" />
          <span className="text-[11px] font-medium text-muted-foreground">{rating}</span>
        </div>
      )}
      {showPrice && (
        <div className="flex items-baseline gap-2 mt-1">
          {hasDiscount ? (
            <>
              <span className="text-sm text-sale font-medium">
                <Money amount={price} currency={product.currency} />
              </span>
              <span className="text-xs text-muted-foreground line-through">
                <Money amount={compareAt} currency={product.currency} />
              </span>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">
              <Money amount={price} currency={product.currency} />
            </span>
          )}
        </div>
      )}
    </motion.div>
  );
}

export default GildedProductCard;
