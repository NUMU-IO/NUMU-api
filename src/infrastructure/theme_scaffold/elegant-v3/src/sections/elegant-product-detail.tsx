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
  type ProductVariant,
} from "@numueg/theme-sdk";
import { Minus, Plus, ShoppingBag, Truck, RotateCcw, ShieldCheck, ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Elegant product-detail section.
 *
 * Ported from the proven vionne V3 PDP (gallery-left / info-right) and
 * re-skinned in elegant's warm-brown + serif aesthetic via the `eg-*`
 * design vocabulary defined in src/theme.css. Data/actions are SDK-native:
 * the host wraps the PDP in ProductProvider so the current product comes from
 * `useProductOptional()`; variant pick + selected variant from
 * `useVariantSelection()`; add-to-cart drives `useCart().addItem` via the
 * chosen variant id; related rail from `useRelatedProducts()`.
 *
 * Null-safety: when there's no product in context (editor with no product, or a
 * not-found slug) we render a graceful placeholder instead of crashing.
 */
export default function ElegantProductDetail({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};

  const showRating = s.show_rating ?? true;
  const showStock = s.show_stock ?? true;
  const showGuarantees = s.show_guarantees ?? true;
  const showRelated = s.show_related_products ?? true;
  const relatedCount = asNumber(s.related_products_count, 4);
  const locale = useLocale();

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
          <span className="eg-eyebrow block mb-3 text-[var(--eg-muted)]">{localized(locale, "Product", "المنتج")}</span>
          <h1 className="eg-heading text-2xl md:text-3xl text-[var(--eg-ink)] mb-3">
            {localized(locale, "No product to show yet", "لا يوجد منتج لعرضه بعد")}
          </h1>
          <p className="text-sm text-[var(--eg-muted)] mb-7 leading-relaxed">
            {localized(locale, "Open a product from the shop to see its details here.", "افتح منتجاً من المتجر لعرض تفاصيله هنا.")}
          </p>
          <Link to="/products" className="eg-btn eg-btn-filled inline-flex">
            {localized(locale, "Browse products", "تصفح المنتجات")}
          </Link>
        </div>
      </div>
    );
  }

  const images = product.images ?? [];
  const mainImage = images[activeImage]?.url ?? images[0]?.url;
  // Prefer the (selected or first) VARIANT price — that's the cents value the
  // catalog/Money expects.
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

  // Tags drive the eyebrow labels.
  const tags = (product.tags ?? []).filter(Boolean);

  const options = product.options ?? [];

  return (
    <div className="bg-background" data-testid="storefront-product-detail">
      <div className="container mx-auto px-4 py-6 md:py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 eg-label text-[10px] text-[var(--eg-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--eg-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <Link to="/products" className="hover:text-[var(--eg-ink)] transition-colors">
            {localized(locale, "Shop", "المتجر")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--eg-ink)] line-clamp-1 normal-case tracking-normal">
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
              className="relative overflow-hidden rounded-lg bg-[var(--eg-band)] aspect-[3/4] mb-3"
              data-testid="storefront-product-detail-main-image"
            >
              {mainImage ? (
                <img
                  src={mainImage}
                  alt={product.name}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              ) : (
                <div className="absolute inset-0 eg-shimmer" />
              )}
              {hasDiscount && (
                <span className="absolute top-3 start-3 eg-label px-2.5 py-1 bg-[var(--eg-sale)] text-white rounded-full text-[10px]">
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
                      "w-20 h-24 shrink-0 overflow-hidden rounded transition-all border-b-2 " +
                      (i === activeImage
                        ? "border-[var(--eg-ink)] opacity-100"
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
                  <span key={tag} className="eg-eyebrow text-[10px] text-[var(--eg-muted)]">
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Name */}
            <h1
              className="eg-heading text-3xl md:text-4xl text-[var(--eg-ink)] mb-3"
              data-testid="storefront-product-detail-name"
            >
              {product.name}
            </h1>

            {/* Rating (static — review data not exposed on the SDK product) */}
            {showRating && (
              <div className="flex items-center gap-1 mb-4 text-[var(--eg-muted)]">
                <span className="text-sm text-[hsl(var(--warning))]">★★★★★</span>
                <span className="text-xs">{localized(locale, "(reviews)", "(التقييمات)")}</span>
              </div>
            )}

            {/* Price */}
            <div className="flex items-baseline gap-3 mb-5">
              <span className="text-2xl font-semibold text-[var(--eg-ink)]">
                <Money amount={variantPrice} currency={product.currency} />
              </span>
              {hasDiscount && (
                <span className="text-sm text-[var(--eg-muted)] line-through">
                  <Money amount={compareAt} currency={product.currency} />
                </span>
              )}
            </div>

            {/* Description */}
            {product.description && (
              <p
                className="text-sm text-[var(--eg-muted)] leading-relaxed mb-8"
                data-testid="storefront-product-detail-description"
              >
                {product.description}
              </p>
            )}

            {/* Stock */}
            {showStock && (
              <div className="flex items-center gap-2 mb-6 eg-label text-[10px]">
                <span
                  className={
                    "w-1.5 h-1.5 rounded-full " +
                    (inStock ? "bg-[var(--eg-ink)]/50" : "bg-[var(--eg-sale)]")
                  }
                />
                <span className={inStock ? "text-[var(--eg-muted)]" : "text-[var(--eg-sale)]"}>
                  {inStock
                    ? typeof stockQty === "number" && stockQty > 0 && stockQty <= 5
                      ? localized(locale, `Only ${stockQty} left`, `باقي ${stockQty} فقط`)
                      : localized(locale, "In stock", "متوفر")
                    : localized(locale, "Sold out", "نفذت الكمية")}
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
                  <label className="eg-label text-[10px] text-[var(--eg-muted)] mb-2 block">
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
                              ? "flex items-center gap-2 px-3 py-2 text-sm transition-colors border rounded-md " +
                                (active
                                  ? "border-[var(--eg-ink)]"
                                  : "border-[var(--eg-border)] hover:border-[var(--eg-ink)]/50") +
                                (soldOut ? " opacity-50 line-through" : "")
                              : "min-w-[44px] h-10 px-3 text-sm font-medium transition-colors border rounded-md " +
                                (active
                                  ? "border-[var(--eg-ink)] bg-[var(--eg-ink)] text-[var(--eg-white)]"
                                  : "border-[var(--eg-border)] text-[var(--eg-ink)] hover:border-[var(--eg-ink)]") +
                                (soldOut ? " opacity-50 line-through" : "")
                          }
                          data-testid="storefront-product-detail-variant-option"
                          data-value={value}
                          data-sold-out={soldOut || undefined}
                        >
                          {isColor && (
                            <span
                              className="w-4 h-4 rounded-full border border-[var(--eg-border)]"
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
              <div className="flex items-center border border-[var(--eg-border)] rounded-full">
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
                  without re-implementing it. We only supply elegant styling. */}
              <AddToCartButton
                product={product}
                variant={selectedVariant ?? undefined}
                quantity={quantity}
                className="eg-btn eg-btn-filled flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
                label={
                  <>
                    <ShoppingBag size={16} /> {localized(locale, "Add to bag", "أضف إلى الحقيبة")}
                  </>
                }
                loadingLabel={localized(locale, "Adding…", "جارٍ الإضافة…")}
                soldOutLabel={
                  <>
                    <ShoppingBag size={16} /> {localized(locale, "Sold out", "نفذت الكمية")}
                  </>
                }
                data-testid="storefront-add-to-cart"
              />
            </div>

            {/* Trust guarantees */}
            {showGuarantees && (
              <div className="mt-8 pt-6 border-t border-[var(--eg-border)] flex flex-wrap items-center justify-between gap-4">
                {[
                  { icon: Truck, label: localized(locale, "Fast Shipping", "شحن سريع"), desc: localized(locale, "3-5 days", "3-5 أيام") },
                  { icon: RotateCcw, label: localized(locale, "Easy Returns", "إرجاع سهل"), desc: localized(locale, "14 days", "14 يوم") },
                  { icon: ShieldCheck, label: localized(locale, "Authentic", "أصلي"), desc: localized(locale, "100% Genuine", "100% مضمون") },
                ].map((item) => (
                  <div key={item.label} className="flex items-center gap-2 text-[var(--eg-muted)]">
                    <item.icon size={15} className="shrink-0" />
                    <div>
                      <span className="text-[11px] font-medium text-[var(--eg-ink)]/80">
                        {item.label}
                      </span>
                      <span className="text-[10px] text-[var(--eg-muted)] mx-1">·</span>
                      <span className="text-[10px] text-[var(--eg-muted)]">{item.desc}</span>
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
            className="mt-16 pt-10 border-t border-[var(--eg-border)]"
            data-testid="storefront-related-products"
          >
            <h2 className="eg-eyebrow text-[var(--eg-muted)] mb-6">{localized(locale, "You may also like", "قد يعجبك أيضاً")}</h2>
            <div
              className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-5"
              data-testid="storefront-related-products-grid"
            >
              {related.items.map((p) => (
                <Link
                  key={p.id}
                  to={`/product/${p.slug || p.id}`}
                  className="eg-product-card group block"
                  data-testid="storefront-product-card"
                >
                  <div className="relative aspect-[3/4] overflow-hidden rounded-lg bg-[var(--eg-band)] mb-3">
                    {p.images?.[0]?.url ? (
                      <img
                        src={p.images[0].url}
                        alt={p.name}
                        className="eg-product-image absolute inset-0 w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="absolute inset-0 eg-shimmer" />
                    )}
                    {p.images?.[1]?.url && (
                      <img
                        src={p.images[1].url}
                        alt=""
                        className="eg-product-image-secondary"
                        loading="lazy"
                      />
                    )}
                  </div>
                  <div className="px-1">
                    <h3 className="text-sm font-medium text-[var(--eg-ink)] line-clamp-1">
                      {p.name}
                    </h3>
                    <div className="flex items-baseline gap-2 mt-1">
                      <span className="text-sm font-semibold text-[var(--eg-ink)]">
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
    </div>
  );
}
