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
import { Minus, Plus, ShoppingBag, Truck, RotateCcw, ShieldCheck, ArrowLeft } from "lucide-react";
import { motion } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Boutique product-detail section.
 *
 * Ported from the proven Vionne V3 PDP (gallery-left / info-right, variant
 * axes, quantity stepper + add-to-cart, trust guarantees, related rail), with
 * the grayscale `vn-*` tokens translated to Boutique's pink palette via the
 * shadcn semantic Tailwind classes the theme.css defines.
 *
 * Data/actions are SDK-native: ProductProvider wraps the PDP so the current
 * product comes from useProductOptional(); variant pick from
 * useVariantSelection(); add-to-cart via the SDK AddToCartButton; related rail
 * from useRelatedProducts(). Never blank: no product → graceful placeholder.
 */
export default function BoutiqueProductDetail({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const showRating = s.show_rating ?? true;
  const showStock = s.show_stock ?? true;
  const showGuarantees = s.show_guarantees ?? true;
  const showRelated = s.show_related_products ?? true;
  const relatedCount = asNumber(s.related_products_count, 4);

  const product = useProductOptional();

  const [quantity, setQuantity] = useState(1);
  const [activeImage, setActiveImage] = useState(0);

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
          <span className="block mb-3 text-xs uppercase tracking-widest text-muted-foreground">{localized(locale, "Product", "منتج")}</span>
          <h1 className="text-2xl md:text-3xl font-bold text-foreground mb-3">
            {localized(locale, "No product to show yet", "لا يوجد منتج لعرضه بعد")}
          </h1>
          <p className="text-sm text-muted-foreground mb-7 leading-relaxed">
            {localized(locale, "Open a product from the shop to see its details here.", "افتحي منتجاً من المتجر لعرض تفاصيله هنا.")}
          </p>
          <Link
            to="/products"
            className="inline-flex items-center gap-2 px-7 py-3 rounded-full font-semibold text-sm text-primary-foreground transition-all hover:scale-[1.02]"
            style={{ background: "hsl(var(--primary))" }}
          >
            {localized(locale, "Browse products", "تصفّحي المنتجات")}
          </Link>
        </div>
      </div>
    );
  }

  const images = product.images ?? [];
  const mainImage = images[activeImage]?.url ?? images[0]?.url;
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

  const tags = (product.tags ?? []).filter(Boolean);
  const options = product.options ?? [];

  return (
    <div className="bg-background" data-testid="storefront-product-detail">
      <div className="container mx-auto px-4 py-6 md:py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-muted-foreground mb-6">
          <Link to="/" className="hover:text-foreground transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowLeft size={10} className="rtl:rotate-180" />
          <Link to="/products" className="hover:text-foreground transition-colors">
            {localized(locale, "Shop", "المتجر")}
          </Link>
          <ArrowLeft size={10} className="rtl:rotate-180" />
          <span className="text-foreground line-clamp-1 normal-case tracking-normal">
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
              className="relative overflow-hidden bg-accent/30 aspect-[3/4] mb-3 rounded-2xl"
              data-testid="storefront-product-detail-main-image"
            >
              {mainImage ? (
                <img
                  src={mainImage}
                  alt={product.name}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              ) : (
                <div className="absolute inset-0 bg-muted animate-pulse" />
              )}
              {hasDiscount && (
                <span className="absolute top-3 start-3 px-2.5 py-1 bg-primary text-primary-foreground rounded-full text-[10px] font-bold">
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
                    aria-label={`${localized(locale, "Image", "صورة")} ${i + 1}`}
                    onClick={() => setActiveImage(i)}
                    className={
                      "w-20 h-24 shrink-0 overflow-hidden transition-all rounded-lg border-2 " +
                      (i === activeImage
                        ? "border-primary opacity-100"
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
                  <span key={tag} className="text-[10px] uppercase tracking-widest text-muted-foreground">
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Name */}
            <h1
              className="text-3xl md:text-4xl font-bold text-foreground mb-3"
              data-testid="storefront-product-detail-name"
            >
              {product.name}
            </h1>

            {/* Rating (static — review data not exposed on the SDK product) */}
            {showRating && (
              <div className="flex items-center gap-1 mb-4 text-muted-foreground">
                <span className="text-sm text-[hsl(var(--warning))]">★★★★★</span>
                <span className="text-xs">{localized(locale, "(reviews)", "(تقييمات)")}</span>
              </div>
            )}

            {/* Price */}
            <div className="flex items-baseline gap-3 mb-5">
              <span className="text-2xl font-bold text-primary">
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
                className="text-sm text-muted-foreground leading-relaxed mb-8"
                data-testid="storefront-product-detail-description"
              >
                {product.description}
              </p>
            )}

            {/* Stock */}
            {showStock && (
              <div className="flex items-center gap-2 mb-6 text-[10px] uppercase tracking-widest">
                <span
                  className={
                    "w-1.5 h-1.5 rounded-full " +
                    (inStock ? "bg-primary/50" : "bg-destructive")
                  }
                />
                <span className={inStock ? "text-muted-foreground" : "text-destructive"}>
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
                  <label className="text-[10px] uppercase tracking-widest text-muted-foreground mb-2 block">
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
                              ? "flex items-center gap-2 px-3 py-2 text-sm transition-colors border rounded-full " +
                                (active
                                  ? "border-primary"
                                  : "border-border hover:border-primary/50") +
                                (soldOut ? " opacity-50 line-through" : "")
                              : "min-w-[44px] h-10 px-3 text-sm font-medium transition-colors border rounded-full " +
                                (active
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "border-border text-foreground hover:border-primary") +
                                (soldOut ? " opacity-50 line-through" : "")
                          }
                          data-testid="storefront-product-detail-variant-option"
                          data-value={value}
                          data-sold-out={soldOut || undefined}
                        >
                          {isColor && (
                            <span
                              className="w-4 h-4 rounded-full border border-border"
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
              <div className="flex items-center border border-border rounded-full">
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

              <AddToCartButton
                product={product}
                variant={selectedVariant ?? undefined}
                quantity={quantity}
                className="flex-1 inline-flex items-center justify-center gap-2 h-12 px-6 rounded-full font-semibold text-sm text-primary-foreground transition-all hover:scale-[1.02] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
                style={{ background: "hsl(var(--primary))" }}
                label={
                  <>
                    <ShoppingBag size={16} /> {localized(locale, "Add to bag", "أضيفي للحقيبة")}
                  </>
                }
                loadingLabel={localized(locale, "Adding…", "جاري الإضافة…")}
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
              <div className="mt-8 pt-6 border-t border-border flex flex-wrap items-center justify-between gap-4">
                {[
                  { icon: Truck, label: localized(locale, "Fast Shipping", "شحن سريع"), desc: localized(locale, "3-5 days", "3-5 أيام") },
                  { icon: RotateCcw, label: localized(locale, "Easy Returns", "إرجاع سهل"), desc: localized(locale, "14 days", "14 يوم") },
                  { icon: ShieldCheck, label: localized(locale, "Authentic", "أصلي"), desc: localized(locale, "100% Genuine", "100% أصلي") },
                ].map((item) => (
                  <div key={item.label} className="flex items-center gap-2 text-muted-foreground">
                    <item.icon size={15} className="shrink-0 text-primary" />
                    <div>
                      <span className="text-[11px] font-medium text-foreground/80">
                        {item.label}
                      </span>
                      <span className="text-[10px] text-muted-foreground mx-1">·</span>
                      <span className="text-[10px] text-muted-foreground">{item.desc}</span>
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
            className="mt-16 pt-10 border-t border-border"
            data-testid="storefront-related-products"
          >
            <h2 className="text-xs uppercase tracking-widest text-muted-foreground mb-6">{localized(locale, "You may also like", "قد يعجبك أيضاً")}</h2>
            <div
              className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-5"
              data-testid="storefront-related-products-grid"
            >
              {related.items.map((p) => (
                <Link
                  key={p.id}
                  to={`/product/${p.slug || p.id}`}
                  className="group block rounded-2xl overflow-hidden bg-card border border-border/50 transition-all hover:shadow-md"
                  data-testid="storefront-product-card"
                >
                  <div className="relative aspect-[3/4] overflow-hidden bg-accent/30">
                    {p.images?.[0]?.url ? (
                      <img
                        src={p.images[0].url}
                        alt={p.name}
                        className="absolute inset-0 w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                        loading="lazy"
                      />
                    ) : (
                      <div className="absolute inset-0 bg-muted animate-pulse" />
                    )}
                  </div>
                  <div className="p-3 text-center">
                    <h3 className="text-sm font-medium text-foreground line-clamp-1 group-hover:text-primary transition-colors">
                      {p.name}
                    </h3>
                    <div className="flex items-baseline justify-center gap-2 mt-1">
                      <span className="text-sm font-bold text-primary">
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
