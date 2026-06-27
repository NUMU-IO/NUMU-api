"use client";
import { useState } from "react";
import {
  Link,
  Money,
  AddToCartButton,
  useProductOptional,
  useVariantSelection,
  useRelatedProducts,
  useLocale,
  useResolvedSettings,
  type ProductVariant,
} from "@numueg/theme-sdk";
import {
  Check,
  RotateCcw,
  ShieldCheck,
  ShoppingCart,
  Star,
  Truck,
} from "lucide-react";
import { motion } from "framer-motion";
import {
  asBool,
  asNumber,
  asString,
  asImageUrl,
  localized,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";
import { GildedProductCard } from "./_product-card";

/**
 * gilded-product-detail — faithful V3 port of the V2 Gilded PDP
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/
 * GildedProductDetailPage.tsx, wrapped by GildedProductDetailSection).
 *
 * V2 layout, ported verbatim (only the data layer swaps V2 contexts → SDK
 * hooks, raw settings → useResolvedSettings):
 *   - Breadcrumb `text-xs tracking-[0.2em] uppercase text-muted-foreground`
 *     "Home / Shop / {name}", links hover gold.
 *   - Two-col `flex flex-wrap -mx-4` (md `w-1/2` each).
 *   - LEFT gallery: main `aspect-square border border-gold/20 object-cover`,
 *     discount badge `bg-sale text-card "{n}% Off"`, fade on image change;
 *     thumbnail rail `flex gap-3 overflow-x-auto snap-x scrollbar-hide`, active
 *     `ring-1 ring-gold`.
 *   - RIGHT: title Montserrat `text-2xl md:text-3xl lg:text-4xl font-bold
 *     uppercase tracking-[0.08em]`; category eyebrow; price (current
 *     `text-2xl md:text-3xl font-bold` + struck compare); gold star rating;
 *     description `text-muted-foreground leading-relaxed`; stock dot
 *     (olive in / sale out) + `uppercase tracking-[0.2em]`; option axes — size
 *     buttons (`min-w-[56px] h-11 border`, selected `bg-gold border-2
 *     shadow-[0_0_0_3px]`) / colour swatches (`w-11 h-11 rounded-full`, selected
 *     `ring-2 ring-gold ring-offset-2 scale-110`); qty stepper `border w-10 h-10`;
 *     Add-to-Cart gold-fill `py-4` + ShoppingCart icon; trust badges row
 *     (ShieldCheck / Truck / RotateCcw / MessageCircle) gated by show_guarantees.
 *   - BELOW: related products grid (gated by show_related_products /
 *     related_products_count), heading "Similar Products", using the shared
 *     GildedProductCard.
 *
 * Engine-wired (AUTHORING-CONTRACT): `useResolvedSettings` (never raw
 * `instance.settings`), `InlineEditable` on every STATIC label, SDK
 * Link/Money/AddToCartButton + useProductOptional/useVariantSelection/
 * useRelatedProducts. Product/price/description/variants are LIVE catalog data
 * (not editable text). Bilingual EN/AR, RTL-correct. Never blank / never crashes.
 */
export default function GildedProductDetail({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const showRating = asBool(s.show_rating, true);
  const showStock = asBool(s.show_stock, true);
  const showGuarantees = asBool(s.show_guarantees, true);
  const showRelated = asBool(s.show_related_products, true);
  const relatedCount = asNumber(s.related_products_count, 4);

  // Static, editable copy (the live product fields are NOT editable).
  const breadcrumbHome = asString(s.breadcrumb_home) || localized(locale, "Home", "الرئيسية");
  const breadcrumbShop = asString(s.breadcrumb_shop) || localized(locale, "Shop", "المتجر");
  const relatedTitle =
    asString(s.related_title) || localized(locale, "Similar Products", "منتجات مشابهة");
  const addToCartLabel =
    asString(s.add_to_cart_text) || localized(locale, "Add to Cart", "أضف إلى السلة");
  const soldOutLabel = asString(s.sold_out_text) || localized(locale, "Out of Stock", "نفذت الكمية");
  const quantityLabel = asString(s.quantity_label) || localized(locale, "Quantity", "الكمية");

  const secureLabel = asString(s.secure_label) || localized(locale, "Authentic", "أصلي");
  const secureDesc =
    asString(s.secure_desc) || localized(locale, "100% Genuine", "أصلي ١٠٠٪");
  const returnsLabel = asString(s.returns_label) || localized(locale, "Easy Returns", "إرجاع سهل");
  const returnsDesc = asString(s.returns_desc) || localized(locale, "14 days", "خلال ١٤ يوم");
  const shippingLabel = asString(s.shipping_label) || localized(locale, "Fast Shipping", "شحن سريع");
  const shippingDesc = asString(s.shipping_desc) || localized(locale, "3-5 days", "٣-٥ أيام");

  const product = useProductOptional();

  const [quantity, setQuantity] = useState(1);
  const [activeImage, setActiveImage] = useState(0);
  const [justAdded, setJustAdded] = useState(false);

  // useVariantSelection wants a {options, variants}-shaped object; hooks can't be
  // conditional, so always call it (a safe empty shell when product is null).
  const vs = useVariantSelection(product ?? { options: [], variants: [] });

  const related = useRelatedProducts(showRelated && product ? product.id : null, {
    limit: relatedCount,
  });

  // ── Graceful no-product state (editor preview / not-found) ─────────
  if (!product) {
    return (
      <div className="bg-background min-h-[50vh] flex items-center justify-center px-4 py-20 text-center">
        <div className="max-w-sm">
          <span className="gld-eyebrow block mb-3">{localized(locale, "Product", "المنتج")}</span>
          <h1 className="gld-heading text-2xl md:text-3xl text-foreground mb-3">
            {localized(locale, "No product to show yet", "مفيش منتج للعرض دلوقتي")}
          </h1>
          <p className="text-sm text-muted-foreground mb-7 leading-relaxed">
            {localized(
              locale,
              "Open a product from the shop to see its details here.",
              "افتح منتج من المتجر علشان تشوف تفاصيله هنا.",
            )}
          </p>
          <Link to="/products" className="gld-btn inline-flex">
            {localized(locale, "Browse products", "تصفّح المنتجات")}
          </Link>
        </div>
      </div>
    );
  }

  const images = product.images ?? [];
  const mainImage = asImageUrl(images[activeImage]) || asImageUrl(images[0]);

  // Prefer the (selected or first) VARIANT price — the cents value Money expects.
  const variantPrice =
    vs.variant?.price ?? product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt =
    vs.variant?.compare_at_price ??
    product.variants?.[0]?.compare_at_price ??
    product.compare_at_price ??
    undefined;
  const hasDiscount = typeof compareAt === "number" && compareAt > variantPrice;
  const discountPercent = hasDiscount
    ? Math.round(((compareAt - variantPrice) / compareAt) * 100)
    : 0;

  // Selected variant stock — drives the stock badge + buy-button disable.
  const selectedVariant: ProductVariant | null = vs.variant;
  const inStock = selectedVariant
    ? selectedVariant.is_in_stock !== false
    : product.in_stock !== false;
  const stockQty = selectedVariant?.inventory_quantity;
  const maxQty =
    typeof stockQty === "number" && stockQty > 0 ? stockQty : 99;

  // Rating is not exposed on the SDK Product, so render static gold stars (V2
  // showed product.rating; the engine product has no equivalent field yet).
  const ratingExtras = product as typeof product & {
    rating?: number;
    review_count?: number;
  };
  const rating = typeof ratingExtras.rating === "number" ? ratingExtras.rating : 0;
  const filledStars = rating > 0 ? Math.round(rating) : 5;
  const reviewCount =
    typeof ratingExtras.review_count === "number" ? ratingExtras.review_count : 0;

  // Category eyebrow — only render a human label (never the raw UUID).
  const categoryName =
    typeof product.category === "string" &&
    !/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(product.category)
      ? product.category
      : "";

  const options = product.options ?? [];

  const guarantees = [
    { icon: ShieldCheck, label: secureLabel, desc: secureDesc, keyPrefix: "secure" },
    { icon: RotateCcw, label: returnsLabel, desc: returnsDesc, keyPrefix: "returns" },
    { icon: Truck, label: shippingLabel, desc: shippingDesc, keyPrefix: "shipping" },
  ];

  return (
    <div className="bg-background min-h-screen pb-24 md:pb-0" data-testid="storefront-product-detail">
      <div className="container mx-auto px-4 py-10 md:py-16">
        {/* Breadcrumb */}
        <nav className="text-xs tracking-[0.2em] uppercase text-muted-foreground mb-8">
          <Link to="/" className="hover:text-[var(--gilded-gold)] transition-colors">
            <InlineEditable sectionId={sectionId} settingKey="breadcrumb_home" value={breadcrumbHome} />
          </Link>
          <span className="mx-2">/</span>
          <Link to="/products" className="hover:text-[var(--gilded-gold)] transition-colors">
            <InlineEditable sectionId={sectionId} settingKey="breadcrumb_shop" value={breadcrumbShop} />
          </Link>
          <span className="mx-2">/</span>
          <span className="text-foreground normal-case tracking-normal">{product.name}</span>
        </nav>

        <div className="flex flex-wrap -mx-4">
          {/* ── Product Images (left) ────────────────────────────── */}
          <div className="w-full md:w-1/2 px-4 mb-8">
            <div className="relative bg-card overflow-hidden mb-4 aspect-square border border-[var(--gilded-gold)]/20">
              {hasDiscount && (
                <div className="absolute top-4 start-4 z-10 bg-sale text-card px-3 py-1 text-[11px] font-semibold tracking-[0.15em] uppercase">
                  {discountPercent}% {localized(locale, "Off", "خصم")}
                </div>
              )}
              {mainImage ? (
                <motion.img
                  key={activeImage}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.3 }}
                  src={mainImage}
                  alt={product.name}
                  className="w-full h-full object-cover"
                  data-testid="storefront-product-detail-main-image"
                />
              ) : (
                <div className="absolute inset-0 gld-shimmer" />
              )}
            </div>

            {/* Thumbnails — horizontal-scroll strip, hidden native scrollbar,
                snap stops keep tap targets aligned. */}
            {images.length > 1 && (
              <div className="flex gap-3 sm:gap-4 sm:justify-center overflow-x-auto snap-x snap-mandatory scrollbar-hide px-1 -mx-1">
                {images.map((img, i) => (
                  <button
                    type="button"
                    key={img.id ?? i}
                    onClick={() => setActiveImage(i)}
                    aria-label={`${localized(locale, "View image", "عرض صورة")} ${i + 1}`}
                    className={
                      "size-14 sm:size-20 shrink-0 snap-start overflow-hidden transition-all duration-300 " +
                      (i === activeImage
                        ? "ring-1 ring-[var(--gilded-gold)] opacity-100"
                        : "opacity-60 hover:opacity-100")
                    }
                    data-testid="storefront-product-detail-thumbnail"
                  >
                    <img src={asImageUrl(img)} alt="" className="size-full object-cover" loading="lazy" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ── Product Details (right) ──────────────────────────── */}
          <div className="w-full md:w-1/2 px-4">
            <h1
              className="gld-heading text-2xl md:text-3xl lg:text-4xl text-foreground mb-2"
              data-testid="storefront-product-detail-name"
            >
              {product.name}
            </h1>

            {categoryName && (
              <p className="text-xs tracking-[0.3em] uppercase text-muted-foreground mb-4">
                {categoryName}
              </p>
            )}

            {/* Price */}
            <div className="flex items-baseline gap-3 mb-5">
              <span className="text-2xl md:text-3xl font-bold text-foreground">
                <Money amount={variantPrice} currency={product.currency} />
              </span>
              {hasDiscount && (
                <span className="text-lg text-muted-foreground line-through">
                  <Money amount={compareAt} currency={product.currency} />
                </span>
              )}
            </div>

            {/* Rating */}
            {showRating && (
              <div className="flex items-center mb-5">
                <div className="flex items-center gap-0.5">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Star
                      key={i}
                      size={18}
                      className={
                        i < filledStars
                          ? "fill-[var(--gilded-gold)] text-[var(--gilded-gold)]"
                          : "text-[var(--gilded-gold)]/30"
                      }
                    />
                  ))}
                </div>
                {rating > 0 && (
                  <span className="ms-2 text-sm text-muted-foreground">
                    {rating.toFixed(1)}
                    {reviewCount
                      ? ` (${reviewCount} ${localized(locale, "reviews", "تقييم")})`
                      : ""}
                  </span>
                )}
              </div>
            )}

            {/* Description */}
            {product.description && (
              <p
                className="text-sm md:text-base text-muted-foreground leading-relaxed mb-6"
                data-testid="storefront-product-detail-description"
              >
                {product.description}
              </p>
            )}

            {/* Stock */}
            {showStock && (
              <div className="flex items-center gap-2 text-xs tracking-[0.2em] uppercase mb-6">
                <span
                  className={
                    "inline-block w-1.5 h-1.5 rounded-full " +
                    (inStock ? "bg-[hsl(var(--olive))]" : "bg-sale")
                  }
                />
                <span className={inStock ? "text-muted-foreground" : "text-sale"}>
                  {inStock
                    ? typeof stockQty === "number" && stockQty > 0 && stockQty <= 5
                      ? localized(locale, `Only ${stockQty} left`, `فاضل ${stockQty} بس`)
                      : localized(locale, "In Stock", "متوفّر")
                    : localized(locale, "Out of Stock", "نفذت الكمية")}
                </span>
              </div>
            )}

            {/* Variant option axes — Size buttons / Color swatches / dynamic */}
            {options.map((opt) => {
              const selected = vs.selection[opt.name];
              const avail = vs.availability[opt.name];
              const isColor =
                opt.name.toLowerCase().includes("color") ||
                opt.name.toLowerCase().includes("colour") ||
                opt.name.toLowerCase().includes("لون");
              return (
                <div key={opt.name} className="mb-6">
                  <h3 className="text-xs tracking-[0.2em] uppercase text-muted-foreground mb-3 font-semibold">
                    {opt.name}
                    {selected && (
                      <span className="ms-2 text-foreground normal-case tracking-normal font-normal">
                        — {selected}
                      </span>
                    )}
                  </h3>
                  {isColor ? (
                    <div className="flex gap-3 flex-wrap">
                      {opt.values.map((value) => {
                        const soldOut = avail ? !avail.has(value) : false;
                        const active = selected === value;
                        return (
                          <button
                            type="button"
                            key={value}
                            onClick={() => vs.select(opt.name, value)}
                            aria-label={soldOut ? `${value} — ${localized(locale, "sold out", "نفذت")}` : value}
                            aria-pressed={active ? "true" : "false"}
                            title={value}
                            className={
                              "relative w-11 h-11 rounded-full transition-all duration-150 " +
                              (active
                                ? "ring-2 ring-[var(--gilded-gold)] ring-offset-2 ring-offset-background scale-110"
                                : "ring-1 ring-border hover:ring-[var(--gilded-gold)]/60 hover:scale-105") +
                              (soldOut ? " opacity-50" : "")
                            }
                            style={{ backgroundColor: value }}
                            data-testid="storefront-product-detail-variant-option"
                            data-value={value}
                            data-sold-out={soldOut || undefined}
                          >
                            {active && (
                              <Check
                                size={16}
                                className="absolute inset-0 m-auto text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.6)]"
                              />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="flex gap-2.5 flex-wrap">
                      {opt.values.map((value) => {
                        const soldOut = avail ? !avail.has(value) : false;
                        const active = selected === value;
                        return (
                          <button
                            type="button"
                            key={value}
                            onClick={() => vs.select(opt.name, value)}
                            aria-label={soldOut ? `${value} — ${localized(locale, "sold out", "نفذت")}` : value}
                            aria-pressed={active ? "true" : "false"}
                            className={
                              "min-w-[56px] h-11 px-4 text-sm font-semibold tracking-[0.1em] uppercase transition-all duration-150 " +
                              (active
                                ? "bg-[var(--gilded-gold)] text-foreground border-2 border-[var(--gilded-gold)] shadow-[0_0_0_3px_color-mix(in_srgb,var(--gilded-gold)_15%,transparent)]"
                                : "bg-background border border-border text-foreground hover:border-[var(--gilded-gold)] hover:-translate-y-0.5") +
                              (soldOut ? " opacity-50 line-through" : "")
                            }
                            data-testid="storefront-product-detail-variant-option"
                            data-value={value}
                            data-sold-out={soldOut || undefined}
                          >
                            {value}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}

            {/* Quantity */}
            <div className="mb-6">
              <h3 className="text-xs tracking-[0.2em] uppercase text-muted-foreground mb-3 font-semibold">
                <InlineEditable sectionId={sectionId} settingKey="quantity_label" value={quantityLabel} />
              </h3>
              <div className="inline-flex items-center border border-border">
                <button
                  type="button"
                  onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                  aria-label={localized(locale, "Decrease quantity", "إنقاص الكمية")}
                  className="w-10 h-10 text-lg font-light hover:bg-[var(--gilded-gold)]/10 transition-colors"
                >
                  −
                </button>
                <span
                  className="w-12 text-center text-sm font-semibold tabular-nums"
                  data-testid="storefront-product-detail-quantity"
                >
                  {quantity}
                </span>
                <button
                  type="button"
                  onClick={() => setQuantity((q) => Math.min(maxQty, q + 1))}
                  aria-label={localized(locale, "Increase quantity", "زيادة الكمية")}
                  className="w-10 h-10 text-lg font-light hover:bg-[var(--gilded-gold)]/10 transition-colors"
                >
                  +
                </button>
              </div>
            </div>

            {/* Add to Cart — SDK button owns the loading/disabled/error machine
                (calls useCart().addItem(product, variant, quantity) under the
                hood); we supply the Gilded gold-fill styling + success state. */}
            <AddToCartButton
              product={product}
              variant={selectedVariant ?? undefined}
              quantity={quantity}
              onAdded={() => {
                setJustAdded(true);
                window.setTimeout(() => setJustAdded(false), 2000);
              }}
              className={
                "w-full flex items-center justify-center gap-2 py-4 text-sm font-semibold tracking-[0.15em] uppercase transition-all disabled:cursor-not-allowed " +
                (justAdded
                  ? "bg-[hsl(var(--olive))] text-card"
                  : "bg-[var(--gilded-gold)] text-foreground hover:bg-[var(--gilded-gold-dark)] disabled:bg-muted disabled:text-muted-foreground")
              }
              label={
                justAdded ? (
                  <>
                    <Check size={16} /> {localized(locale, "Added", "تمت الإضافة")}
                  </>
                ) : (
                  <>
                    <ShoppingCart size={16} /> {addToCartLabel}
                  </>
                )
              }
              loadingLabel={localized(locale, "Adding…", "بنضيف…")}
              soldOutLabel={soldOutLabel}
              data-testid="storefront-add-to-cart"
            />

            {/* Trust badges */}
            {showGuarantees && (
              <div className="mt-8 pt-6 border-t border-[var(--gilded-gold)]/15 grid grid-cols-3 gap-3">
                {guarantees.map((item) => (
                  <div key={item.keyPrefix} className="flex flex-col items-start gap-1">
                    <item.icon size={16} className="text-[var(--gilded-gold)]" aria-hidden="true" />
                    <div className="text-[10px] tracking-[0.2em] uppercase font-semibold text-foreground">
                      <InlineEditable
                        sectionId={sectionId}
                        settingKey={`${item.keyPrefix}_label`}
                        value={item.label}
                      />
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      <InlineEditable
                        sectionId={sectionId}
                        settingKey={`${item.keyPrefix}_desc`}
                        value={item.desc}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Related products ─────────────────────────────────────── */}
      {showRelated && related.items.length > 0 && (
        <section className="py-12 md:py-16 bg-card border-t border-[var(--gilded-gold)]/10" data-testid="storefront-related-products">
          <div className="container mx-auto px-4">
            <motion.h2
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-2xl font-bold tracking-[0.08em] uppercase mb-10 text-foreground"
            >
              <InlineEditable sectionId={sectionId} settingKey="related_title" value={relatedTitle} />
            </motion.h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6" data-testid="storefront-related-products-grid">
              {related.items.map((p) => (
                <GildedProductCard key={p.id} product={p} />
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Mobile sticky Add-to-Cart bar — replaces the in-flow CTA on phones so
          it stays reachable while scrolling. */}
      <div className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-background border-t border-[var(--gilded-gold)]/30 px-4 py-3 flex items-center gap-3 shadow-[0_-4px_12px_rgba(0,0,0,0.08)]">
        <div className="flex-1 min-w-0">
          <p className="text-[10px] tracking-[0.2em] uppercase text-muted-foreground truncate">
            {product.name}
          </p>
          <p className="text-sm font-bold text-foreground tabular-nums" dir="ltr">
            <Money amount={variantPrice * quantity} currency={product.currency} />
          </p>
        </div>
        <AddToCartButton
          product={product}
          variant={selectedVariant ?? undefined}
          quantity={quantity}
          onAdded={() => {
            setJustAdded(true);
            window.setTimeout(() => setJustAdded(false), 2000);
          }}
          className={
            "flex-shrink-0 inline-flex items-center justify-center gap-2 px-5 py-3 text-xs font-semibold tracking-[0.15em] uppercase transition-all disabled:cursor-not-allowed " +
            (justAdded
              ? "bg-[hsl(var(--olive))] text-card"
              : "bg-[var(--gilded-gold)] text-foreground hover:bg-[var(--gilded-gold-dark)] disabled:bg-muted disabled:text-muted-foreground")
          }
          label={
            justAdded ? (
              <>
                <Check size={14} /> {localized(locale, "Added", "تمت الإضافة")}
              </>
            ) : (
              <>
                <ShoppingCart size={14} /> {addToCartLabel}
              </>
            )
          }
          loadingLabel={localized(locale, "Adding…", "بنضيف…")}
          soldOutLabel={soldOutLabel}
        />
      </div>
    </div>
  );
}
