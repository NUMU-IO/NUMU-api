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
import { Minus, Plus, ArrowRight, ShoppingCart, Check, Truck, RotateCcw, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";
import { asBool, asNumber, asString, asImageUrl, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-product-detail — faithful V3 port of the V2 LuxProductDetailSection
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/product-detail/…,
 * which wraps BaseProductDetailPage with the luxury-minimal `styles` object).
 *
 * Layout (V2 verbatim): `container mx-auto px-4 py-8` →
 * `grid grid-cols-1 md:grid-cols-2 gap-12`. Left = gallery (main `aspect-[3/4]`
 * over `bg-[hsl(var(--lux-gray))]` + a `scrollbar-hide` thumb rail, active
 * `opacity-100`). Right = breadcrumb (`text-[10px]/0.2em`), name
 * (`text-xl md:text-2xl`), price (`text-xl`) + compare `line-through`,
 * description (`text-sm text-muted-foreground`), size selector (`min-w-[40px]
 * h-10 border hover:border-foreground`, active `border-foreground bg-foreground
 * text-background`), colour swatches (`w-8 h-8 border-2`, active
 * `border-foreground scale-110`), a quantity stepper and a full-width `lux-btn`
 * Add-to-Cart (success → `bg-[hsl(var(--success))] text-white`). Related grid
 * (`grid-cols-2 md:grid-cols-4`) is gated by `show_related_products` /
 * `related_products_count`. Mono palette, SHARP edges, uppercase tracked type.
 *
 * Engine-wired: `useResolvedSettings` (never `instance.settings` — that bypasses
 * global tokens + dynamic sources), `InlineEditable` on every STATIC label.
 * Product/price/description/variants are LIVE catalog data (not editable text).
 *
 * Data/actions are SDK-native: the host wraps the PDP in ProductProvider so the
 * current product comes from `useProductOptional()`; variant pick + selected
 * variant from `useVariantSelection()`; add-to-cart via the SDK button; related
 * rail from `useRelatedProducts()`. Never blank / never crashes.
 */
export default function LuxProductDetail({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const showRating = asBool(s.show_rating, false);
  const showStock = asBool(s.show_stock, false);
  const showGuarantees = asBool(s.show_guarantees, true);
  const showRelated = asBool(s.show_related_products, true);
  const relatedCount = asNumber(s.related_products_count, 4);

  // Static, editable copy (the live product fields are NOT editable).
  const breadcrumbHome = asString(s.breadcrumb_home) || localized(locale, "Home", "الرئيسية");
  const breadcrumbShop = asString(s.breadcrumb_shop) || localized(locale, "Products", "المنتجات");
  const relatedTitle =
    asString(s.related_title) || localized(locale, "Similar Products", "منتجات مشابهة");
  const addToCartLabel =
    asString(s.add_to_cart_text) || localized(locale, "Add to Cart", "أضف إلى السلة");
  const soldOutLabel = asString(s.sold_out_text) || localized(locale, "Out of stock", "نفذت الكمية");
  const shippingLabel = asString(s.shipping_label) || localized(locale, "Fast Shipping", "شحن سريع");
  const shippingDesc = asString(s.shipping_desc) || localized(locale, "3-5 days", "3-5 أيام");
  const returnsLabel = asString(s.returns_label) || localized(locale, "Returns", "الإرجاع");
  const returnsDesc = asString(s.returns_desc) || localized(locale, "14 days", "14 يوم");
  const authenticLabel = asString(s.authentic_label) || localized(locale, "Authentic", "أصلي");
  const authenticDesc =
    asString(s.authentic_desc) || localized(locale, "100% Guaranteed", "مضمون 100%");

  const product = useProductOptional();

  const [quantity, setQuantity] = useState(1);
  const [activeImage, setActiveImage] = useState(0);
  const [justAdded, setJustAdded] = useState(false);

  const vs = useVariantSelection(product ?? { options: [], variants: [] });

  const related = useRelatedProducts(showRelated && product ? product.id : null, {
    limit: relatedCount,
  });

  // ── Graceful no-product state (editor preview / not-found) ─────────
  if (!product) {
    return (
      <div className="bg-background min-h-[50vh] flex items-center justify-center px-4 py-20 text-center">
        <div className="max-w-sm">
          <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-3">
            {localized(locale, "Product", "المنتج")}
          </p>
          <h1 className="lux-heading text-2xl md:text-3xl text-foreground mb-3">
            {localized(locale, "No product to show yet", "لا يوجد منتج لعرضه بعد")}
          </h1>
          <p className="text-sm text-muted-foreground mb-7 leading-relaxed">
            {localized(
              locale,
              "Open a product from the shop to see its details here.",
              "افتح منتجاً من المتجر لعرض تفاصيله هنا.",
            )}
          </p>
          <Link to="/products" className="lux-btn inline-flex">
            {localized(locale, "Browse products", "تصفح المنتجات")}
          </Link>
        </div>
      </div>
    );
  }

  const images = product.images ?? [];
  const mainImage = asImageUrl(images[activeImage]) || asImageUrl(images[0]);
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

  const selectedVariant: ProductVariant | null = vs.variant;
  const inStock = selectedVariant
    ? selectedVariant.is_in_stock !== false
    : product.in_stock !== false;
  const stockQty = selectedVariant?.inventory_quantity;

  const options = product.options ?? [];

  const guarantees = [
    { icon: Truck, label: shippingLabel, desc: shippingDesc },
    { icon: RotateCcw, label: returnsLabel, desc: returnsDesc },
    { icon: ShieldCheck, label: authenticLabel, desc: authenticDesc },
  ];

  return (
    <div className="bg-background" data-lux-section={sectionId} data-testid="storefront-product-detail">
      <div className="container mx-auto px-4 py-8">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-8">
          <Link to="/" className="hover:text-foreground transition-colors">
            <InlineEditable sectionId={sectionId} settingKey="breadcrumb_home" value={breadcrumbHome} />
          </Link>
          <ArrowRight size={10} aria-hidden="true" />
          <Link to="/products" className="hover:text-foreground transition-colors">
            <InlineEditable sectionId={sectionId} settingKey="breadcrumb_shop" value={breadcrumbShop} />
          </Link>
          <ArrowRight size={10} aria-hidden="true" />
          <span className="text-foreground line-clamp-1">
            {product.name}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
          {/* ── Gallery (left) ─────────────────────────────────────── */}
          <div>
            <motion.div
              key={activeImage}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="aspect-[3/4] overflow-hidden bg-[hsl(var(--lux-gray))] mb-3 relative"
              data-testid="storefront-product-detail-main-image"
            >
              {mainImage ? (
                <img
                  src={mainImage}
                  alt={product.name}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              ) : (
                <div className="absolute inset-0 lux-shimmer" />
              )}
              {hasDiscount && (
                <span className="absolute top-0 end-0 px-3 py-1 text-[10px] font-medium uppercase tracking-wide bg-foreground text-background">
                  {discountPercent}% OFF
                </span>
              )}
            </motion.div>

            {images.length > 1 && (
              <div className="flex gap-2 overflow-x-auto scrollbar-hide">
                {images.map((img, i) => (
                  <button
                    type="button"
                    key={img.id ?? i}
                    aria-label={`Image ${i + 1}`}
                    onClick={() => setActiveImage(i)}
                    className={
                      "w-16 h-20 shrink-0 overflow-hidden bg-[hsl(var(--lux-gray))] transition-opacity " +
                      (i === activeImage ? "opacity-100" : "opacity-50 hover:opacity-75")
                    }
                    data-testid="storefront-product-detail-thumbnail"
                  >
                    <img src={asImageUrl(img)} alt="" className="w-full h-full object-cover" loading="lazy" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ── Info (right) ───────────────────────────────────────── */}
          <div className="md:pt-4">
            {/* Name */}
            <h1
              className="text-xl md:text-2xl mb-4 text-foreground"
              data-testid="storefront-product-detail-name"
            >
              {product.name}
            </h1>

            {/* Rating (static) */}
            {showRating && (
              <div className="flex items-center gap-1 mb-4 text-muted-foreground">
                <span className="text-sm">★★★★★</span>
                <span className="text-xs">{localized(locale, "(reviews)", "(التقييمات)")}</span>
              </div>
            )}

            {/* Price */}
            <div className="flex items-baseline gap-3 mb-5">
              <span className="text-xl text-foreground">
                <Money amount={variantPrice} currency={product.currency} />
              </span>
              {hasDiscount && (
                <span className="text-sm text-muted-foreground line-through">
                  <Money amount={compareAt} currency={product.currency} />
                </span>
              )}
            </div>

            {/* Description */}
            {product.description && (
              <p
                className="text-muted-foreground text-sm leading-relaxed mb-8"
                data-testid="storefront-product-detail-description"
              >
                {product.description}
              </p>
            )}

            {/* Stock */}
            {showStock && (
              <div className="flex items-center gap-2 mb-6 text-xs uppercase tracking-wide">
                <span
                  className={"w-1.5 h-1.5 " + (inStock ? "bg-foreground/40" : "bg-destructive")}
                />
                <span className={"font-medium " + (inStock ? "text-muted-foreground" : "text-destructive")}>
                  {inStock
                    ? typeof stockQty === "number" && stockQty > 0 && stockQty <= 5
                      ? localized(locale, `Only ${stockQty} left`, `باقي ${stockQty} فقط`)
                      : localized(locale, "In Stock", "متوفر")
                    : localized(locale, "Out of Stock", "نفذت الكمية")}
                </span>
              </div>
            )}

            {/* Variant option axes (Size buttons / Color swatches) */}
            {options.map((opt) => {
              const selected = vs.selection[opt.name];
              const avail = vs.availability[opt.name];
              const isColor =
                opt.name.toLowerCase().includes("color") ||
                opt.name.toLowerCase().includes("colour") ||
                opt.name.toLowerCase().includes("لون");
              return (
                <div key={opt.name} className="mb-6">
                  <label className="text-[10px] uppercase tracking-[0.2em] mb-3 block text-muted-foreground">
                    {opt.name}
                    {selected ? `: ${selected}` : ""}
                  </label>
                  <div className="flex gap-2 flex-wrap">
                    {opt.values.map((value) => {
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
                              ? "w-8 h-8 border-2 transition-all " +
                                (active ? "border-foreground scale-110" : "border-transparent hover:border-border") +
                                (soldOut ? " opacity-50" : "")
                              : "min-w-[40px] h-10 px-3 text-xs border transition-all " +
                                (active
                                  ? "border-foreground bg-foreground text-background"
                                  : "border-border hover:border-foreground") +
                                (soldOut ? " opacity-50 line-through" : "")
                          }
                          data-testid="storefront-product-detail-variant-option"
                          data-value={value}
                          data-sold-out={soldOut || undefined}
                        >
                          {isColor ? (
                            <span className="w-full h-full block" style={{ backgroundColor: value }} />
                          ) : (
                            value
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}

            {/* Quantity + Add to cart */}
            <div className="flex items-center gap-3 mb-6">
              <div className="flex items-center border border-border">
                <button
                  type="button"
                  aria-label="Decrease quantity"
                  onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                  className="p-3 hover:bg-muted transition-colors"
                >
                  <Minus size={14} />
                </button>
                <span className="px-4 text-sm" data-testid="storefront-product-detail-quantity">
                  {quantity}
                </span>
                <button
                  type="button"
                  aria-label="Increase quantity"
                  onClick={() => setQuantity((q) => q + 1)}
                  className="p-3 hover:bg-muted transition-colors"
                >
                  <Plus size={14} />
                </button>
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
                  "flex-1 py-3.5 flex items-center justify-center gap-2 transition-all disabled:opacity-50 lux-btn " +
                  (justAdded ? "bg-[hsl(var(--success))] text-white" : "")
                }
                label={
                  justAdded ? (
                    <>
                      <Check size={16} /> {localized(locale, "Added!", "تمت الإضافة!")}
                    </>
                  ) : (
                    <>
                      <ShoppingCart size={16} /> {addToCartLabel}
                    </>
                  )
                }
                loadingLabel={localized(locale, "Adding…", "جارٍ الإضافة…")}
                soldOutLabel={
                  <>
                    <ShoppingCart size={16} /> {soldOutLabel}
                  </>
                }
                data-testid="storefront-add-to-cart"
              />
            </div>

            {/* Trust guarantees */}
            {showGuarantees && (
              <div className="mt-8 pt-6 border-t border-border flex flex-wrap items-center justify-between gap-4">
                {guarantees.map((item, i) => (
                  <div key={item.label || i} className="flex items-center gap-2 text-muted-foreground">
                    <item.icon size={15} className="shrink-0" aria-hidden="true" />
                    <div>
                      <span className="text-[11px] font-medium text-foreground/70">
                        <InlineEditable
                          sectionId={sectionId}
                          settingKey={
                            i === 0 ? "shipping_label" : i === 1 ? "returns_label" : "authentic_label"
                          }
                          value={item.label}
                        />
                      </span>
                      <span className="text-[10px] text-muted-foreground mx-1">·</span>
                      <span className="text-[10px] text-muted-foreground">
                        <InlineEditable
                          sectionId={sectionId}
                          settingKey={
                            i === 0 ? "shipping_desc" : i === 1 ? "returns_desc" : "authentic_desc"
                          }
                          value={item.desc}
                        />
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
          <section className="mt-16 mb-6" data-testid="storefront-related-products">
            <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-8">
              <InlineEditable sectionId={sectionId} settingKey="related_title" value={relatedTitle} />
            </p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4" data-testid="storefront-related-products-grid">
              {related.items.map((p) => (
                <Link
                  key={p.id}
                  to={`/product/${p.slug || p.id}`}
                  className="lux-product-card group block"
                  data-testid="storefront-product-card"
                >
                  <div className="relative aspect-[3/4] overflow-hidden bg-[hsl(var(--lux-gray))] mb-3">
                    {asImageUrl(p.images?.[0]) ? (
                      <img
                        src={asImageUrl(p.images?.[0])}
                        alt={p.name}
                        className="lux-product-image absolute inset-0 w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="absolute inset-0 lux-shimmer" />
                    )}
                    {asImageUrl(p.images?.[1]) && (
                      <img src={asImageUrl(p.images?.[1])} alt="" className="lux-product-image-secondary" loading="lazy" />
                    )}
                  </div>
                  <p className="text-sm text-foreground line-clamp-1 mb-1.5 group-hover:opacity-50 transition-opacity">
                    {p.name}
                  </p>
                  <p className="text-sm text-foreground">
                    <Money amount={p.variants?.[0]?.price ?? p.price ?? 0} currency={p.currency} />
                  </p>
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
