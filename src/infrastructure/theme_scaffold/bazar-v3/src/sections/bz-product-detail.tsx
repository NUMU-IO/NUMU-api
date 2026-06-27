"use client";

import { useRef, useState } from "react";
import {
  Image,
  Link,
  Money,
  useCart,
  useLocale,
  useProductOptional,
  useResolvedSettings,
  useVariantSelection,
} from "@numueg/theme-sdk";
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  Minus,
  Plus,
  RotateCcw,
  ShieldCheck,
  ShoppingBag,
  Truck,
} from "lucide-react";
import {
  asNumber,
  asString,
  demoOrPlaceholder,
  localized,
  PLACEHOLDER_IMG,
  useDemo,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface TrustBadge {
  label: string;
  desc: string;
}

const fallbackBadges = (locale: string | undefined): TrustBadge[] => [
  { label: localized(locale, "AUTHENTIC", "أصلي"), desc: localized(locale, "100% Guaranteed", "مضمون ١٠٠٪") },
  { label: localized(locale, "RETURNS", "إرجاع"), desc: localized(locale, "14 days", "خلال ١٤ يوم") },
  { label: localized(locale, "SHIPPING", "شحن"), desc: localized(locale, "All Egypt", "لكل مصر") },
];

// Demo product used ONLY in marketplace preview (no real product context).
const DEMO_IMAGES = [
  "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=900&q=70",
  "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=900&q=70",
];

/**
 * bz-product-detail — the PDP body, ported from V2 BzProductDetailPage.
 *
 * Layout: a split grid — a vertical stack of every product photo on one
 * side (each framed with the bazar 2px border + offset shadow, decorative
 * blobs behind), and a sticky buy-box on a wavy amber panel on the other.
 * The buy-box has a breadcrumb, name, price (+ SAVE chip when on sale),
 * stock state, description, variant swatches (driven by the SDK's
 * `useVariantSelection`), a quantity stepper, the add-to-cart CTA, and a
 * trust-badge row. A mobile sticky bar mirrors price + add-to-cart.
 *
 * Wiring: `useProductOptional()` for the product (graceful demo fallback
 * in preview), `useVariantSelection()` for the option axes + resolved
 * variant, `useCart().addItem` for the purchase. Prices via `<Money>`.
 */
export default function BzProductDetail({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const productCtx = useProductOptional();
  const { addItem, loading } = useCart();
  const demo = useDemo();
  const locale = useLocale();

  // Editable buy-box labels.
  const addToCartLabel = asString(s.add_to_cart_label) || localized(locale, "ADD TO CART", "أضف للسلة");
  const addedLabel = asString(s.added_label) || localized(locale, "ADDED!", "تمت الإضافة!");
  const outOfStockLabel = asString(s.out_of_stock_label) || localized(locale, "OUT OF STOCK", "غير متوفر");
  const quantityLabel = asString(s.quantity_label) || localized(locale, "QUANTITY", "الكمية");
  const homeLabel = asString(s.home_label) || localized(locale, "HOME", "الرئيسية");
  const shopLabel = asString(s.shop_label) || localized(locale, "SHOP", "تسوّق");
  const inStockLabel = asString(s.in_stock_label) || localized(locale, "IN STOCK", "متوفر");

  // The product to render. With a real context we use it directly; in the
  // marketplace preview (no context) we synthesise a demo product, and
  // outside demo mode we blank it to neutral placeholders.
  const product = productCtx ?? {
    id: "demo",
    name: demo ? localized(locale, "Bazar Tote Bag", "شنطة توت بازار") : "",
    slug: "demo",
    description: demo
      ? localized(
          locale,
          "A roomy everyday tote in heavyweight cotton canvas — screen-printed by hand, built to carry the whole souk run.",
          "شنطة توت واسعة لكل يوم من قماش الكانفاس القطني التقيل — مطبوعة بطباعة شاشة يدوي، معمولة تشيل توضيبة السوق كلها.",
        )
      : "",
    price: demo ? 350 : 0,
    compare_at_price: demo ? 450 : undefined,
    currency: "EGP",
    images: (demo ? DEMO_IMAGES : [PLACEHOLDER_IMG]).map((url, i) => ({
      id: String(i),
      url,
      position: i,
    })),
    options: [],
    variants: [],
    in_stock: true,
  };

  const isFallback = !productCtx;

  const {
    selection,
    variant,
    select,
    availability,
    isComplete,
  } = useVariantSelection(product);

  const [quantity, setQuantity] = useState(1);
  const [added, setAdded] = useState(false);
  const addInFlight = useRef(false);
  const imageRefs = useRef<(HTMLImageElement | null)[]>([]);

  const images = product.images ?? [];
  const hasImages = images.length > 0;

  // Selected-variant price wins; fall back to product price.
  const activePrice = variant?.price ?? product.price ?? 0;
  const activeCompareAt =
    variant?.compare_at_price ?? product.compare_at_price ?? null;
  const hasDiscount = activeCompareAt != null && activeCompareAt > activePrice;
  const savings = hasDiscount ? activeCompareAt - activePrice : 0;
  const savingsPct =
    hasDiscount && activeCompareAt > 0
      ? Math.round((savings / activeCompareAt) * 100)
      : 0;

  // Stock: variant overrides product-level flag when a variant is resolved.
  const inStock = variant ? variant.is_in_stock : product.in_stock;

  const badges =
    demoOrPlaceholder(true, fallbackBadges(locale)); // labels are static chrome, always shown

  const handleAdd = async () => {
    if (isFallback || addInFlight.current || added || !inStock) return;
    // Block buying until every axis is chosen on variant products.
    if (!isComplete) return;
    addInFlight.current = true;
    try {
      await addItem(product.id, variant?.id, quantity);
      setAdded(true);
      setTimeout(() => {
        setAdded(false);
        addInFlight.current = false;
      }, 2000);
    } catch (err) {
      addInFlight.current = false;
      console.warn("[bazar] add to cart failed", err);
    }
  };

  const addDisabled =
    isFallback || added || !inStock || (product.options?.length ? !isComplete : false);

  return (
    <section
      className="bg-[var(--bz-cream)] pb-20 md:pb-0"
      data-bz-section={sectionId}
    >
      <div className="grid md:grid-cols-2">
        {/* Image column — vertical stack */}
        <div className="relative bg-[var(--bz-cream)] overflow-hidden">
          <div aria-hidden="true" className="hidden md:block absolute top-20 -end-12 w-40 h-40 bg-[var(--bz-amber)]/40 bz-blob pointer-events-none" />
          <div aria-hidden="true" className="hidden md:block absolute bottom-32 -start-16 w-52 h-52 bg-[var(--bz-navy)]/10 bz-blob bz-blob-delay-2 pointer-events-none" />
          <div aria-hidden="true" className="hidden md:block absolute top-6 start-6 w-32 h-32 opacity-40 pointer-events-none bz-dot-grid" />

          <Link
            to="/products"
            aria-label="Back to shop"
            className="absolute top-4 start-4 z-20 w-10 h-10 rounded-full bg-[var(--bz-dark)] text-[var(--bz-amber)] flex items-center justify-center hover:opacity-80 transition-opacity shadow-[3px_3px_0_var(--bz-amber)]"
          >
            <ArrowLeft size={18} aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>

          {hasImages ? (
            <div className="relative z-10 flex flex-col gap-4 md:gap-5 p-4 md:p-6 lg:p-8">
              {images.map((img, i) => (
                <div
                  key={`${img.url}-${i}`}
                  ref={(el) => {
                    imageRefs.current[i] = el as unknown as HTMLImageElement;
                  }}
                  className="relative bg-white rounded-2xl border-2 border-[var(--bz-dark)] overflow-hidden shadow-[6px_6px_0_var(--bz-dark)]"
                >
                  {/* Large, uniform product-card image via the SDK <Image> engine
                      primitive: fixed square frame + object-cover (focal-aware),
                      responsive srcSet + lazy/eager. Renders identically across
                      themes and is immune to global img resets. */}
                  <Image
                    src={img.url}
                    alt={i === 0 ? product.name : `${product.name} — view ${i + 1}`}
                    loading={i === 0 ? "eager" : "lazy"}
                    aspectRatio="1/1"
                    objectFit="cover"
                    className="bz-img-zoom"
                    sizes="(min-width: 768px) 50vw, 100vw"
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="relative z-10 flex flex-col items-center justify-center p-8 min-h-[40vh]">
              <div className="w-48 h-48 sm:w-64 sm:h-64 rounded-2xl bg-white border-2 border-[var(--bz-dark)] shadow-[6px_6px_0_var(--bz-dark)] flex items-center justify-center">
                <ShoppingBag size={48} className="text-[var(--bz-dark)]/30" aria-hidden="true" />
              </div>
            </div>
          )}
        </div>

        {/* Buy-box column — sticky on the wavy panel */}
        <div className="bz-wavy-bg p-6 sm:p-8 md:p-12 lg:p-16">
          <div className="max-w-md mx-auto w-full md:sticky md:top-6">
            {/* Breadcrumb */}
            <nav
              aria-label="Breadcrumb"
              className="bz-label text-[11px] text-[var(--bz-dark)]/60 mb-4 flex items-center flex-wrap gap-1"
            >
              <Link to="/" className="hover:text-[var(--bz-dark)] transition-colors">
                {homeLabel}
              </Link>
              <ChevronLeft size={12} aria-hidden="true" className="rotate-180 rtl:rotate-0" />
              <Link to="/products" className="hover:text-[var(--bz-dark)] transition-colors">
                {shopLabel}
              </Link>
              <ChevronLeft size={12} aria-hidden="true" className="rotate-180 rtl:rotate-0" />
              <span className="text-[var(--bz-dark)]/80 truncate max-w-[160px]">
                {product.name}
              </span>
            </nav>

            <h1 className="bz-heading text-2xl sm:text-3xl md:text-4xl lg:text-5xl text-[var(--bz-dark)]">
              {product.name}
            </h1>

            {/* Price */}
            <div className="flex items-baseline gap-3 mt-3 md:mt-4 flex-wrap">
              <span className="bz-heading text-xl sm:text-2xl md:text-3xl text-[var(--bz-dark)]">
                <Money amount={activePrice} currency={product.currency} />
              </span>
              {hasDiscount && (
                <>
                  <span className="text-base sm:text-lg text-[var(--bz-dark)]/40 line-through">
                    <Money amount={activeCompareAt} currency={product.currency} />
                  </span>
                  <span className="bz-label text-[10px] px-2.5 py-1 bg-red-500 text-white rounded-full border-2 border-[var(--bz-dark)] shadow-[2px_2px_0_var(--bz-dark)]">
                    {localized(locale, "SAVE", "وفّر")} {savingsPct}%
                  </span>
                </>
              )}
            </div>

            {/* Stock state */}
            {!inStock ? (
              <div className="mt-4">
                <span className="bz-label text-red-600 text-sm">
                  {outOfStockLabel}
                </span>
              </div>
            ) : (
              <div className="mt-4 inline-flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-green-600" aria-hidden="true" />
                <span className="bz-label text-green-800 text-xs">{inStockLabel}</span>
              </div>
            )}

            {/* Description */}
            {product.description && (
              <p className="text-[var(--bz-dark)]/70 text-sm mt-6 leading-relaxed">
                {product.description}
              </p>
            )}

            {/* Variant axes */}
            {product.options && product.options.length > 0 && (
              <div className="mt-8 space-y-6">
                {product.options.map((opt) => {
                  const chosen = selection[opt.name];
                  const avail = availability[opt.name];
                  return (
                    <div key={opt.name}>
                      <span className="bz-label text-[var(--bz-dark)]/60 mb-3 block uppercase">
                        {opt.name}
                        {chosen ? ` — ${chosen}` : ""}
                      </span>
                      <div
                        className="flex gap-3 flex-wrap"
                        role="radiogroup"
                        aria-label={opt.name}
                      >
                        {opt.values.map((v) => {
                          const isSelected = chosen === v;
                          // availability[axis] (when present) lists values
                          // that still resolve to an in-stock variant given
                          // other locked axes; grey out the rest.
                          const unavailable =
                            avail != null && !avail.has(v) && !isSelected;
                          return (
                            <button
                              type="button"
                              key={v}
                              role="radio"
                              aria-checked={isSelected}
                              disabled={unavailable}
                              onClick={() => select(opt.name, v)}
                              className={`bz-size-btn ${isSelected ? "active" : ""} ${
                                unavailable ? "opacity-30 cursor-not-allowed line-through" : ""
                              }`}
                            >
                              {v}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Quantity */}
            <div className="mt-6">
              <span className="bz-label text-[var(--bz-dark)]/60 mb-3 block">
                {quantityLabel}
              </span>
              <div className="inline-flex items-stretch rounded-full border-2 border-[var(--bz-dark)] overflow-hidden bg-[var(--bz-cream)]">
                <button
                  type="button"
                  aria-label="Decrease quantity"
                  onClick={() => setQuantity((q) => Math.max(q - 1, 1))}
                  disabled={quantity <= 1}
                  className="w-11 h-11 flex items-center justify-center text-[var(--bz-dark)] hover:bg-[var(--bz-dark)] hover:text-[var(--bz-amber)] transition-colors disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-[var(--bz-dark)]"
                >
                  <Minus size={16} aria-hidden="true" />
                </button>
                <span
                  className="bz-heading text-base text-[var(--bz-dark)] min-w-[3rem] flex items-center justify-center border-x-2 border-[var(--bz-dark)] select-none"
                  aria-live="polite"
                >
                  {quantity}
                </span>
                <button
                  type="button"
                  aria-label="Increase quantity"
                  onClick={() => setQuantity((q) => q + 1)}
                  className="w-11 h-11 flex items-center justify-center text-[var(--bz-dark)] hover:bg-[var(--bz-dark)] hover:text-[var(--bz-amber)] transition-colors"
                >
                  <Plus size={16} aria-hidden="true" />
                </button>
              </div>
            </div>

            {/* Add to cart */}
            <button
              type="button"
              onClick={handleAdd}
              disabled={addDisabled || loading}
              className={`bz-btn w-full mt-8 rounded-full py-4 text-[13px] ${
                added
                  ? "bg-green-600 text-white border-green-600"
                  : !inStock
                    ? "opacity-50 cursor-not-allowed bz-btn-filled"
                    : "bz-btn-filled"
              }`}
            >
              {added ? (
                <span className="flex items-center justify-center gap-2">
                  <Check size={16} aria-hidden="true" /> {addedLabel}
                </span>
              ) : !inStock ? (
                outOfStockLabel
              ) : (
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="add_to_cart_label"
                  value={addToCartLabel}
                />
              )}
            </button>

            {/* Trust badges */}
            <div className="mt-8 pt-6 border-t border-[var(--bz-dark)]/15 grid grid-cols-3 gap-3">
              {badges.map((b, i) => {
                const Icon = [ShieldCheck, RotateCcw, Truck][i] ?? ShieldCheck;
                return (
                  <div
                    key={b.label}
                    className="flex flex-col items-center text-center gap-1.5"
                  >
                    <Icon size={18} className="text-[var(--bz-dark)]" aria-hidden="true" />
                    <span className="bz-label text-[10px] text-[var(--bz-dark)]">
                      {b.label}
                    </span>
                    <span className="text-[10px] text-[var(--bz-dark)]/60 leading-tight">
                      {b.desc}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Mobile sticky add-to-cart bar */}
      <div className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-[var(--bz-cream)] border-t-2 border-[var(--bz-dark)] px-4 py-3 flex items-center gap-3 shadow-[0_-2px_8px_rgba(0,0,0,0.08)]">
        <div className="flex flex-col leading-tight shrink-0">
          <span className="bz-heading text-base text-[var(--bz-dark)]">
            <Money amount={activePrice * quantity} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-[10px] text-[var(--bz-dark)]/40 line-through">
              <Money amount={activeCompareAt * quantity} currency={product.currency} />
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={handleAdd}
          disabled={addDisabled || loading}
          className={`bz-btn flex-1 rounded-full py-3 text-[12px] ${
            added
              ? "bg-green-600 text-white border-green-600"
              : !inStock
                ? "bg-[var(--bz-dark)]/20 text-[var(--bz-dark)]/60 border-[var(--bz-dark)]/20 cursor-not-allowed"
                : "bz-btn-filled"
          }`}
        >
          {added ? (
            <span className="flex items-center justify-center gap-2">
              <Check size={14} aria-hidden="true" /> {addedLabel}
            </span>
          ) : !inStock ? (
            outOfStockLabel
          ) : (
            addToCartLabel
          )}
        </button>
      </div>
    </section>
  );
}
