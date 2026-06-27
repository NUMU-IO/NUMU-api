"use client";
import { useState } from "react";
import {
  Link,
  Money,
  AddToCartButton,
  useLocale,
  useProductOptional,
  useResolvedSettings,
  useTranslation,
  useVariantSelection,
  useRelatedProducts,
  type ProductVariant,
} from "@numueg/theme-sdk";
import { Minus, Plus, ShoppingBag, Truck, RotateCcw, ShieldCheck, ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * Vionne product-detail section.
 *
 * Faithful port of the V2 Vionne PDP look (gallery-left / info-right, the
 * `vn-*` utility classes + Vionne tokens) re-plumbed on the V3 SDK.
 *
 * V2 sources mirrored:
 *  - themes/sections/product-detail/ProductDetailSection.tsx (settings → flags)
 *  - components/store/shared/BaseProductDetailPage.tsx (markup: breadcrumb,
 *    2-col grid, thumbnail rail, tags/name/price/description, size + color
 *    selectors with sold-out treatment, quantity stepper + add-to-cart,
 *    trust guarantees row, related-products grid)
 *  - components/store/shared/useProductDetail.ts (derived: discount %, related,
 *    per-axis sold-out lookup → here delegated to the SDK's useVariantSelection
 *    availability map)
 *  - components/store/vionne/VionneProductCard.tsx (related card markup +
 *    vn-product-card / vn-product-image / sale-badge classes)
 *
 * Data/actions are SDK-native: the host wraps the PDP in ProductProvider so the
 * current product comes from `useProductOptional()`; variant pick + selected
 * variant from `useVariantSelection()`; add-to-cart drives `useCart().addItem`
 * via the chosen variant id; related rail from `useRelatedProducts()`.
 *
 * Null-safety: when there's no product in context (editor with no product, or a
 * not-found slug) we render a graceful placeholder card with a "Back to shop"
 * link instead of crashing or showing a blank page.
 */
const GALLERY_ASPECT: Record<string, string> = {
  "3-4": "aspect-[3/4]",
  "4-5": "aspect-[4/5]",
  "1-1": "aspect-square",
  "4-3": "aspect-[4/3]",
};

export default function VionneProductDetail({ instance, sectionId }: SectionRenderProps) {
  const locale = useLocale();
  const { t } = useTranslation();
  const s = useResolvedSettings(instance);

  const showRating = s.show_rating ?? true;
  const showStock = s.show_stock ?? true;
  const showGuarantees = s.show_guarantees ?? true;
  const showRelated = s.show_related_products ?? true;
  const showDescription = s.show_description !== false;
  const relatedCount = asNumber(s.related_products_count, 4);
  const relatedTitle =
    asString(s.related_title) || localized(locale, "You may also like", "ممكن يعجبك كمان");
  const galleryAspectClass =
    GALLERY_ASPECT[asString(s.gallery_aspect, "3-4")] ?? GALLERY_ASPECT["3-4"];
  // Trust guarantees — V2 copy as bilingual defaults, now each editable
  // (label + detail) instead of hardcoded in the markup. Icons stay fixed
  // per slot (shipping / returns / authentic).
  const guarantees = [
    { icon: Truck, key: "guarantee_1", label: asString(s.guarantee_1_label) || localized(locale, "Fast Shipping", "شحن سريع"), desc: asString(s.guarantee_1_desc) || localized(locale, "3-5 days", "٣-٥ أيام") },
    { icon: RotateCcw, key: "guarantee_2", label: asString(s.guarantee_2_label) || localized(locale, "Easy Returns", "إرجاع سهل"), desc: asString(s.guarantee_2_desc) || localized(locale, "14 days", "١٤ يوم") },
    { icon: ShieldCheck, key: "guarantee_3", label: asString(s.guarantee_3_label) || localized(locale, "Authentic", "أصلي"), desc: asString(s.guarantee_3_desc) || localized(locale, "100% Genuine", "أصلي ١٠٠٪") },
  ];

  const product = useProductOptional();

  const [quantity, setQuantity] = useState(1);
  const [activeImage, setActiveImage] = useState(0);

  // useVariantSelection wants a {options, variants}-shaped object; it tolerates
  // missing axes (single-variant products get `{}` selections). Always call the
  // hook (hooks can't be conditional) — pass a safe empty shell when product is
  // null so the rules-of-hooks stay satisfied.
  const vs = useVariantSelection(
    product ?? { options: [], variants: [] },
  );

  const related = useRelatedProducts(showRelated && product ? product.id : null, {
    limit: relatedCount,
  });

  // ── Graceful no-product state (editor preview / not-found) ─────────
  if (!product) {
    return (
      <div className="bg-background min-h-[50vh] flex items-center justify-center px-4 py-20 text-center">
        <div className="max-w-sm">
          <span className="vn-eyebrow block mb-3 text-[var(--vn-muted)]">{localized(locale, "Product", "المنتج")}</span>
          <h1 className="vn-heading text-2xl md:text-3xl text-[var(--vn-ink)] mb-3">
            {localized(locale, "No product to show yet", "لا يوجد منتج لعرضه بعد")}
          </h1>
          <p className="text-sm text-[var(--vn-muted)] mb-7 leading-relaxed">
            {localized(locale, "Open a product from the shop to see its details here.", "افتحي أي منتج من المتجر علشان تشوفي تفاصيله هنا.")}
          </p>
          <Link to="/products" className="vn-btn vn-btn-filled inline-flex">
            {localized(locale, "Browse products", "تصفّحي المنتجات")}
          </Link>
        </div>
      </div>
    );
  }

  const images = product.images ?? [];
  const mainImage = images[activeImage]?.url ?? images[0]?.url;
  // Prefer the (selected or first) VARIANT price — that's the cents value the
  // catalog/Money expects. `product.price` is a different scale on some rows
  // (showed 100×), so only use it as a last resort, matching the home grid's
  // `variants?.[0]?.price ?? price` logic.
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

  // Selected variant stock — used for the stock badge + buy-button disable.
  const selectedVariant: ProductVariant | null = vs.variant;
  const inStock = selectedVariant
    ? selectedVariant.is_in_stock !== false
    : product.in_stock !== false;
  const stockQty = selectedVariant?.inventory_quantity;

  // Tags drive the eyebrow labels (V2 showed product.tags as uppercase chips).
  const tags = (product.tags ?? []).filter(Boolean);

  const options = product.options ?? [];

  return (
    <div className="bg-background" data-testid="storefront-product-detail">
      {/* Extra mobile bottom padding clears the sticky add-to-cart bar (which
          sits above the dock); md restores the normal rhythm (no sticky bar). */}
      <div className="container mx-auto px-4 pt-6 pb-16 md:py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 vn-label text-[10px] text-[var(--vn-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--vn-ink)] transition-colors">
            {t("nav.home", localized(locale, "Home", "الرئيسية"))}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <Link to="/products" className="hover:text-[var(--vn-ink)] transition-colors">
            {localized(locale, "Shop", "المتجر")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--vn-ink)] line-clamp-1 normal-case tracking-normal">
            {product.name}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-12">
          {/* ── Gallery (left) ─────────────────────────────────────── */}
          <div>
            <motion.div
              key={activeImage}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={`relative overflow-hidden bg-[var(--vn-band)] ${galleryAspectClass} mb-3`}
              data-testid="storefront-product-detail-main-image"
            >
              {mainImage ? (
                <img
                  src={mainImage}
                  alt={product.name}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              ) : (
                <div className="absolute inset-0 vn-shimmer" />
              )}
              {hasDiscount && (
                <span className="absolute top-3 start-3 vn-label px-2.5 py-1 bg-[var(--vn-sale)] text-white rounded-full text-[10px]">
                  -{discountPercent}%
                </span>
              )}
            </motion.div>

            {images.length > 1 && (
              <div className="flex gap-2 overflow-x-auto">
                {images.map((img, i) => (
                  <button
                    type="button"
                    key={img.id ?? i}
                    aria-label={`Image ${i + 1}`}
                    onClick={() => setActiveImage(i)}
                    className={
                      "w-20 h-24 shrink-0 overflow-hidden transition-all border-b-2 " +
                      (i === activeImage
                        ? "border-[var(--vn-ink)] opacity-100"
                        : "border-transparent opacity-60 hover:opacity-90")
                    }
                    data-testid="storefront-product-detail-thumbnail"
                  >
                    <img
                      src={img.url}
                      alt=""
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ── Info (right) ───────────────────────────────────────── */}
          <div>
            {/* Tags / eyebrow */}
            {tags.length > 0 && (
              <div className="flex items-center gap-3 mb-3 flex-wrap">
                {tags.map((tag) => (
                  <span key={tag} className="vn-eyebrow text-[10px] text-[var(--vn-muted)]">
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Name */}
            <h1
              className="vn-heading text-3xl md:text-4xl text-[var(--vn-ink)] mb-3"
              data-testid="storefront-product-detail-name"
            >
              {product.name}
            </h1>

            {/* Rating (static — review data not exposed on the SDK product) */}
            {showRating && (
              <div className="flex items-center gap-1 mb-4 text-[var(--vn-muted)]">
                <span className="text-sm">★★★★★</span>
                <span className="text-xs">{localized(locale, "(reviews)", "(تقييمات)")}</span>
              </div>
            )}

            {/* Price */}
            <div className="flex items-baseline gap-3 mb-5">
              <span className="text-2xl font-semibold text-[var(--vn-ink)]">
                <Money amount={variantPrice} currency={product.currency} />
              </span>
              {hasDiscount && (
                <span className="text-sm text-[var(--vn-muted)] line-through">
                  <Money amount={compareAt} currency={product.currency} />
                </span>
              )}
            </div>

            {/* Description */}
            {showDescription && product.description && (
              <p
                className="text-sm text-[var(--vn-muted)] leading-relaxed mb-8"
                data-testid="storefront-product-detail-description"
              >
                {product.description}
              </p>
            )}

            {/* Stock */}
            {showStock && (
              <div className="flex items-center gap-2 mb-6 vn-label text-[10px]">
                <span
                  className={
                    "w-1.5 h-1.5 rounded-full " +
                    (inStock ? "bg-[var(--vn-ink)]/50" : "bg-[var(--vn-sale)]")
                  }
                />
                <span className={inStock ? "text-[var(--vn-muted)]" : "text-[var(--vn-sale)]"}>
                  {inStock
                    ? typeof stockQty === "number" && stockQty > 0 && stockQty <= 5
                      ? localized(locale, `Only ${stockQty} left`, `باقي ${stockQty} بس`)
                      : localized(locale, "In stock", "متوفر")
                    : localized(locale, "Sold out", "خلص المخزون")}
                </span>
              </div>
            )}

            {/* Variant option axes (Size / Color / ...) */}
            {options.map((opt) => {
              const selected = vs.selection[opt.name];
              const avail = vs.availability[opt.name];
              const isColor = opt.name.toLowerCase().includes("color")
                || opt.name.toLowerCase().includes("colour")
                || opt.name.toLowerCase().includes("لون");
              return (
                <div key={opt.name} className="mb-6">
                  <label className="vn-label text-[10px] text-[var(--vn-muted)] mb-2 block">
                    {opt.name}
                    {selected ? `: ${selected}` : ""}
                  </label>
                  <div className="flex gap-2 flex-wrap">
                    {opt.values.map((value) => {
                      // availability map only lists values for *unselected* axes;
                      // when an axis is already chosen treat all its values as
                      // pickable (so the customer can switch).
                      const soldOut = avail ? !avail.has(value) : false;
                      const active = selected === value;
                      return (
                        <button
                          type="button"
                          key={value}
                          onClick={() => vs.select(opt.name, value)}
                          aria-label={soldOut ? `${value} — sold out` : value}
                          className={
                            isColor
                              ? "flex items-center gap-2 px-3 py-2 text-sm transition-colors border " +
                                (active
                                  ? "border-[var(--vn-ink)]"
                                  : "border-[var(--vn-border)] hover:border-[var(--vn-ink)]/50") +
                                (soldOut ? " opacity-50 line-through" : "")
                              : "min-w-[44px] h-10 px-3 text-sm font-medium transition-colors border " +
                                (active
                                  ? "border-[var(--vn-ink)] bg-[var(--vn-ink)] text-[var(--vn-white)]"
                                  : "border-[var(--vn-border)] text-[var(--vn-ink)] hover:border-[var(--vn-ink)]") +
                                (soldOut ? " opacity-50 line-through" : "")
                          }
                          data-testid="storefront-product-detail-variant-option"
                          data-value={value}
                          data-sold-out={soldOut || undefined}
                        >
                          {isColor && (
                            <span
                              className="w-4 h-4 rounded-full border border-[var(--vn-border)]"
                              style={{ backgroundColor: value }}
                            />
                          )}
                          {value}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}

            {/* Quantity + Add to cart */}
            <div className="flex items-center gap-3 mb-4">
              <div className="flex items-center border border-[var(--vn-border)] rounded-full">
                <button
                  type="button"
                  aria-label="Decrease quantity"
                  onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                  className="p-3 hover:opacity-70 transition-opacity"
                >
                  <Minus size={14} />
                </button>
                <span
                  className="px-4 text-sm font-medium tabular-nums"
                  data-testid="storefront-product-detail-quantity"
                >
                  {quantity}
                </span>
                <button
                  type="button"
                  aria-label="Increase quantity"
                  onClick={() => setQuantity((q) => q + 1)}
                  className="p-3 hover:opacity-70 transition-opacity"
                >
                  <Plus size={14} />
                </button>
              </div>

              {/* The SDK's <AddToCartButton> owns the loading/disabled/error
                  state machine and calls useCart().addItem(product, variant,
                  quantity) under the hood, so we get a variant-aware add
                  without re-implementing it. We only supply Vionne styling. */}
              <AddToCartButton
                product={product}
                variant={selectedVariant ?? undefined}
                quantity={quantity}
                className="vn-btn vn-btn-filled flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
                label={
                  <>
                    <ShoppingBag size={16} /> {localized(locale, "Add to bag", "أضيفي للشنطة")}
                  </>
                }
                loadingLabel={localized(locale, "Adding…", "بنضيف…")}
                soldOutLabel={
                  <>
                    <ShoppingBag size={16} /> {localized(locale, "Sold out", "خلص المخزون")}
                  </>
                }
                data-testid="storefront-add-to-cart"
              />
            </div>

            {/* Trust guarantees */}
            {showGuarantees && (
              <div className="mt-8 pt-6 border-t border-[var(--vn-border)] flex flex-wrap items-center justify-between gap-4">
                {guarantees.map((item) => (
                  <div key={item.key} className="flex items-center gap-2 text-[var(--vn-muted)]">
                    <item.icon size={15} className="shrink-0" />
                    <div>
                      <span className="text-[11px] font-medium text-[var(--vn-ink)]/80">
                        <InlineEditable sectionId={sectionId} settingKey={`${item.key}_label`} value={item.label} />
                      </span>
                      <span className="text-[10px] text-[var(--vn-muted)] mx-1">·</span>
                      <span className="text-[10px] text-[var(--vn-muted)]">
                        <InlineEditable sectionId={sectionId} settingKey={`${item.key}_desc`} value={item.desc} />
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Related products ─────────────────────────────────────── */}
        {showRelated && related.items.length > 0 && (
          <section
            className="mt-16 pt-10 border-t border-[var(--vn-border)]"
            data-testid="storefront-related-products"
          >
            <h2 className="vn-eyebrow text-[var(--vn-muted)] mb-6">
              <InlineEditable sectionId={sectionId} settingKey="related_title" value={relatedTitle} />
            </h2>
            <div
              className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-5"
              data-testid="storefront-related-products-grid"
            >
              {related.items.map((p) => (
                <Link
                  key={p.id}
                  to={`/product/${p.slug || p.id}`}
                  className="vn-product-card group block"
                  data-testid="storefront-product-card"
                >
                  <div className="relative aspect-[3/4] overflow-hidden bg-[var(--vn-band)] mb-3">
                    {p.images?.[0]?.url ? (
                      <img
                        src={p.images[0].url}
                        alt={p.name}
                        className="vn-product-image absolute inset-0 w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="absolute inset-0 vn-shimmer" />
                    )}
                    {p.images?.[1]?.url && (
                      <img
                        src={p.images[1].url}
                        alt=""
                        className="vn-product-image-secondary"
                        loading="lazy"
                      />
                    )}
                  </div>
                  <div className="px-1">
                    <h3 className="text-sm font-medium text-[var(--vn-ink)] line-clamp-1">
                      {p.name}
                    </h3>
                    <div className="flex items-baseline gap-2 mt-1">
                      <span className="text-sm font-semibold text-[var(--vn-ink)]">
                        <Money amount={p.variants?.[0]?.price ?? p.price ?? 0} currency={p.currency} />
                      </span>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Mobile sticky add-to-cart bar. md:hidden, and offset upward by the
          header's bottom-dock height so the two never stack (the dock is
          hidden ≥768px). Mirrors the selected variant + quantity. */}
      <div
        className="md:hidden fixed inset-x-0 z-30 bg-[var(--vn-white)] border-t border-[var(--vn-border)] px-4 py-2.5 flex items-center gap-3"
        style={{ bottom: "calc(64px + env(safe-area-inset-bottom))" }}
        data-testid="storefront-pdp-sticky-bar"
      >
        <div className="min-w-0 flex-1">
          <p className="text-xs text-[var(--vn-ink)] font-medium truncate">{product.name}</p>
          <p className="text-sm font-semibold text-[var(--vn-ink)]">
            <Money amount={variantPrice} currency={product.currency} />
          </p>
        </div>
        <AddToCartButton
          product={product}
          variant={selectedVariant ?? undefined}
          quantity={quantity}
          className="vn-btn vn-btn-filled shrink-0 px-5 disabled:opacity-50 disabled:cursor-not-allowed"
          label={
            <>
              <ShoppingBag size={16} /> {localized(locale, "Add", "أضيفي")}
            </>
          }
          loadingLabel={localized(locale, "Adding…", "بنضيف…")}
          soldOutLabel={localized(locale, "Sold out", "خلص المخزون")}
        />
      </div>
    </div>
  );
}
